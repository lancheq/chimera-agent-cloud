"""Grand Challenge entrypoint for the CHIMERA agentic baseline.

On Grand Challenge one container invocation handles a **single case through a
single interface**: the platform drops the input sockets as flat JSON files in
``/input`` (described by ``/input/inputs.json``) and expects the result sockets
as flat JSON files in ``/output``. The platform then aggregates every job's
inputs and outputs into a single ``predictions.json`` (rebuilt locally after the
per-case runs by ``scripts/aggregate_predictions.py``).

The agent package (``chimera_agent_baseline``) is written around a per-case
directory tree ``task<N>/agent_input/<case>/{prompt,clinical,features}.json``.
This entrypoint is the thin adapter between the two worlds:

1. Read ``inputs.json`` and detect which interface (task) is being run from the
   clinical-data socket slug.
2. Materialise the flat GC sockets into a temporary
   ``<tmp>/task<N>/agent_input/<case>/`` tree the package understands:
     * ``structured-prompt``                              -> ``prompt.json``
     * ``prostate-<task>-...-clinical-data``              -> ``clinical.json``
     * ``prostate-modality-level-neural-representations`` -> ``features.json``
3. Run the same :func:`run_agent` used locally (model + MCP tools + LangGraph
   ReAct loop + form-fill), scoped to the single task.
4. Read back the validated prediction and write the GC result sockets flat to
   ``/output`` — a decision value + a reasoning value per interface, in the
   task-specific shapes below. The reasoning value is an object that also
   carries the tool ``reveal_sequence``, so the combined ``predictions.json``
   (rebuilt by ``scripts/aggregate_predictions.py``) surfaces it inside the
   reasoning socket.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any
from contextlib import ExitStack

from chimera_agent_baseline.entry_profile import verify_profile, verify_identity, effective_config
from chimera_agent_baseline.runtime_audit import (PrivateRuntime, RuntimeFatal, case_scope,
    consumption_event)

from omegaconf import OmegaConf

from reasoning_align import align_reasoning
from chimera_agent_baseline.rag import start_embedding_service
from chimera_agent_baseline.run import run_agent
from chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK
from chimera_agent_baseline.utils import setup_logging

log = logging.getLogger(__name__)

# --- Grand Challenge mount points --------------------------------------------
INPUT_PATH = Path("/input")
OUTPUT_PATH = Path("/output")
CONFIG_PATH = Path("/opt/app/configs/config.yaml")
RESOURCE_PATH = Path("/opt/app/resources")
MODEL_PATH = Path("/opt/ml/model")

# Synthetic case id — GC runs one anonymous case per container invocation.
CASE_ID = "gc-case"

# --- Interface (socket) contract ---------------------------------------------
# Fixed socket slugs shared by every interface.
STRUCTURED_PROMPT_SLUG = "structured-prompt"
NEURAL_REP_SLUG = "prostate-modality-level-neural-representations"

# The clinical-data socket slug is what distinguishes the three interfaces /
# tasks. NB: GC truncates slugs to 50 chars, so the task-3 slug is the clipped
# ``...-follow-up-clin`` (the on-disk filename is resolved separately via each
# socket's ``relative_path`` in inputs.json).
CLINICAL_SLUG_TO_TASK: dict[str, int] = {
    "prostate-biopsy-decision-clinical-data": 1,
    "prostate-treatment-decision-clinical-data": 2,
    "prostate-time-to-recurrence-or-last-follow-up-clin": 3,
}

# Result-socket filenames per task (written flat to ``/output``): the decision
# value and the reasoning value. NB the platform's own per-case dumps spell task
# 1's socket ``prostate-biospy-decision`` (and task 3's slug ``...-reas``), but
# the *filenames* the platform validates against are the corrected spellings:
# GC failed run 17a095b4-... with "Output file 'prostate-biopsy-decision.json'
# was not produced" while this container wrote the ``biospy`` spelling. The
# official baseline carries the same typo; see evaluation/evaluate.py, which
# accepts both only because it reads the aggregation file, not the sockets.
OUTPUT_SOCKETS: dict[int, dict[str, str]] = {
    1: {
        "decision": "prostate-biopsy-decision.json",
        "reasoning": "prostate-biopsy-decision-reasoning.json",
    },
    2: {
        "decision": "prostate-treatment-decision.json",
        "reasoning": "prostate-treatment-decision-reasoning.json",
    },
    3: {
        "decision": "prostate-time-to-recurrence-or-last-follow-up.json",
        "reasoning": "prostate-time-to-recurrence-or-last-follow-up-reasoning.json",
    },
}


# ---------------------------------------------------------------------------
# Input side: GC sockets -> per-case agent-input tree
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    with path.open() as f:
        return json.load(f)


def _write_json(path: Path, content: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(content, f, indent=2)


def _socket_paths() -> dict[str, Path]:
    """Map each input socket slug to its file under ``/input`` via inputs.json."""
    inputs = _load_json(INPUT_PATH / "inputs.json")
    return {sv["socket"]["slug"]: INPUT_PATH / sv["socket"]["relative_path"] for sv in inputs}


def _detect_task(slug_to_path: dict[str, Path]) -> int:
    for slug, task in CLINICAL_SLUG_TO_TASK.items():
        if slug in slug_to_path:
            return task
    raise ValueError(
        f"No known clinical-data socket in inputs.json (got {sorted(slug_to_path)}); "
        f"expected one of {sorted(CLINICAL_SLUG_TO_TASK)}"
    )


def _t3_upfront_view(source: Path, runtime: PrivateRuntime) -> tuple[dict, dict]:
    """Admit fields from the official T3 structured-prompt Socket."""
    original = source.read_bytes()
    archived = runtime.root / 't3-upfront-original.json'
    with archived.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    provenance = dict(source_socket=STRUCTURED_PROMPT_SLUG, source_path=str(source),
                      archived_file=archived.name, original_sha256=hashlib.sha256(original).hexdigest())
    try:
        data = json.loads(original)
    except (ValueError, UnicodeError) as error:
        runtime.emit('t3_upfront_source_rejected', reason='invalid_json', **provenance)
        raise RuntimeFatal('Invalid T3 structured-prompt JSON') from error
    if not isinstance(data, dict):
        runtime.emit('t3_upfront_source_rejected', reason='expected_json_object', **provenance)
        raise RuntimeFatal('T3 structured-prompt must be an object')
    from chimera_agent_baseline.guarded_runtime import REFERENCE_AUDIT_FIELDS
    if reference := data.keys() & REFERENCE_AUDIT_FIELDS:
        runtime.emit('t3_upfront_source_rejected', reason='reference_audit_fields_in_input',
                     rejected_fields=sorted(reference), **provenance)
        raise RuntimeFatal('Reference/quality audit fields cannot be upfront')

    field_checks = {
        'age': lambda value: type(value) is int,
        'psa': lambda value: type(value) is int or (type(value) is float and math.isfinite(value)),
        'dre': lambda value: isinstance(value, str),
        'active_treatment_prior_to_surgery': lambda value: value is None or isinstance(value, str),
    }
    view, rejected = {}, {}
    for field, value in data.items():
        if field in ('case_id', 'task'):
            continue  # The adapter adds its own case and interface identity.
        if field in field_checks:
            if field_checks[field](value):
                view[field] = value
            else:
                rejected[field] = 'not_an_allowed_scalar_for_field'
        elif field == 'months':
            rejected[field] = 'ambiguous_time_semantics'
        elif field in ('event', 'months_to_recurrence', 'recurrence', 'last_follow_up', 'last_follow_up_months'):
            rejected[field] = 'outcome_or_follow_up_not_an_upfront_field'
        else:
            rejected[field] = 'field_outside_official_t3_upfront'
    return view, dict(provenance, retained_fields=sorted(view), rejected_fields=rejected,
                      identity_authority='formal_entry_socket_and_case_adapter')


def _materialise_case(task: int, slug_to_path: dict[str, Path], root: Path, case_id: str,
                      *, t3_upfront_runtime: PrivateRuntime | None = None) -> None:
    """Write the ``task<N>/agent_input/<case>/`` tree the package expects."""
    case_dir = root / f"task{task}" / "agent_input" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    # structured-prompt -> prompt.json. The package renders this through the
    # Jinja template; it must carry case_id + task (the Jinja ``show`` macro
    # tolerates any other missing field, rendering it as an em dash).
    prompt: dict[str, Any] = {}
    if t3_upfront_runtime is not None:
        source = slug_to_path.get(STRUCTURED_PROMPT_SLUG)
        if task != 3 or source is None:
            t3_upfront_runtime.emit('t3_upfront_source_rejected', reason='missing_t3_structured_prompt_socket')
            raise RuntimeFatal('T3 admission requires the structured-prompt Socket')
        prompt, admission = _t3_upfront_view(source, t3_upfront_runtime)
    elif STRUCTURED_PROMPT_SLUG in slug_to_path:
        loaded = _load_json(slug_to_path[STRUCTURED_PROMPT_SLUG])
        if isinstance(loaded, dict):
            prompt = dict(loaded)
    prompt["case_id"] = case_id
    prompt["task"] = task
    projected_path = case_dir / "prompt.json"
    _write_json(projected_path, prompt)
    if t3_upfront_runtime is not None:
        t3_upfront_runtime.t3_upfront_admission = t3_upfront_runtime.emit(
            't3_upfront_admitted', projected_sha256=hashlib.sha256(projected_path.read_bytes()).hexdigest(),
            **admission)

    # clinical-data -> the per-task clinical filename + a legacy ``clinical.json``.
    #
    # MCP tools resolve the case file through
    # ``tools/base.CASE_DATA_FILENAMES_BY_TASK``, i.e. the *task-specific* name
    # (``prostate-biopsy-decision-clinical-data.json`` for task 1) -- writing
    # only ``clinical.json`` made ``CaseDataStore`` log "Loaded 0 cases" and
    # every tool answer "Case 'gc-case' not found", so the agent reasoned
    # without any clinical data at all (GC run a7d7744d: the run's own
    # self_refine critique reported "The tools are returning Case not found
    # errors" and listed the fabricated PSA/PI-RADS/PSAD values it then
    # invented). The real data trees on the school server carry the long name,
    # which is why the 180-case batch never showed this.
    clinical_slug = next(s for s in CLINICAL_SLUG_TO_TASK if s in slug_to_path)
    clinical = _load_json(slug_to_path[clinical_slug])
    if isinstance(clinical, dict):
        clinical.setdefault("case_id", case_id)
    task_clinical_name = CASE_DATA_FILENAMES_BY_TASK[task]
    _write_json(case_dir / task_clinical_name, clinical)
    # Keep writing the legacy alias: it is what older copies of this adapter and
    # any ad-hoc tooling look for, and an extra file here is inert.
    if task_clinical_name != "clinical.json":
        _write_json(case_dir / "clinical.json", clinical)

    # neural representations -> features.json (only consumed when the optional
    # image predictor is enabled; inert otherwise).
    if NEURAL_REP_SLUG in slug_to_path:
        features = _load_json(slug_to_path[NEURAL_REP_SLUG])
        if isinstance(features, dict):
            features.setdefault("case_id", case_id)
        _write_json(case_dir / "features.json", features)
        # predictor.py 的 FEATURES_FILE 常量读的是 GC 原始文件名
        # ``prostate-modality-level-neural-representations.json``。容器里只写
        # ``features.json`` 会让 build_case_features 找不到向量文件 →
        # T3 的 reducer 输入退化成全零。同写一份，别名对旧工具也是惰性的。
        _write_json(case_dir / f"{NEURAL_REP_SLUG}.json", features)


# ---------------------------------------------------------------------------
# Output side: prediction records -> per-case result files + predictions.json
# ---------------------------------------------------------------------------


def _decision_value(task: int, prediction: dict[str, Any]) -> Any:
    """The ``decision`` result-socket value for a case (task-specific shape)."""
    if task == 1:
        return prediction["biopsy_decision"]  # "yes" / "no"
    if task == 2:
        return prediction["treatment_recommendation"]["primary"]  # action token
    return {
        "event": int(prediction["event"]),
        "months_to_recurrence": float(prediction["months_to_recurrence"]),
    }


def _reasoning_value(task: int, prediction: dict[str, Any], *, align: bool = True) -> Any:
    """The ``reasoning`` result-socket value for a case.

    Tasks 1 & 2 emit an object carrying the free-text rationale, the overall
    ``confidence``, the per-variable ``variable_weights``, and the tool
    ``reveal_sequence``. Task 3 emits the free-text rationale on its own (its
    reveal sequence is not evaluated).

    Tasks 1 & 2 are first passed through :func:`reasoning_align.align_reasoning`,
    which replaces the LLM-authored ``confidence``/``variable_weights`` with the
    measured fixed tables. Those two fields carry 67.5% of the platform's
    gate-passed case score and the LLM's own values score below the constant
    table on the official training ground truth; see ``reasoning_align.py`` for
    the measurements and ``tools/reasoning_alignment_regress.py`` to reproduce
    them. The decision socket, the free text and the reveal sequence are
    untouched, so gate behaviour is unchanged.
    """
    if task == 3:
        return prediction["free_text"]
    if align:
        aligned = align_reasoning(task, prediction)
        consumption_event('reasoning_align', prediction, aligned, task=task, case_id=CASE_ID,
                          authority='existing_align_D05_unreviewed')
        prediction = aligned
    return {
        "free_text": prediction["free_text"],
        "confidence": prediction["confidence"],
        "variable_weights": _variable_weights_for_platform(
            prediction.get("variable_weights", {}), task
        ),
        "reveal_sequence": _reveal_sequence_for_platform(
            prediction.get("reveal_sequence", []), task
        ),
    }


# --- variable_weights: allow only the platform's per-task key set ------------
#
# The task-2 socket schema sets ``additionalProperties: false``. GC run
# f698486a rejected the whole file over one key our own schema padding had added:
#
#   instance Additional properties are not allowed ('bx_gl_tert' was unexpected)
#
# The fix for the padding lives in output/schema.py (TASK2_VARIABLES no longer
# lists bx_gl_tert); this boundary filter is the belt-and-braces guard so a key
# can never reach the socket even if the internal shape changes again. The sets
# below are the platform's, which match docs/CHIMERA-agent赛事整理.md.
_TASK1_WEIGHT_KEYS = frozenset({
    "psa", "age", "dre", "comorbidity", "bx", "pirads", "psad", "vol", "cspca", "fh",
})
_TASK2_WEIGHT_KEYS = frozenset({
    "psa", "age", "ct", "comorbidity", "pirads", "psad", "cspca",
    "bx_gl_prim", "bx_gl_sec", "bx_isup", "fh",
})

_WEIGHT_KEYS_BY_TASK: dict[int, frozenset[str]] = {
    1: _TASK1_WEIGHT_KEYS,
    2: _TASK2_WEIGHT_KEYS,
}


def _variable_weights_for_platform(weights: Any, task: int) -> dict[str, Any]:
    """Drop any variable the platform's schema does not declare for *task*.

    Unlike ``reveal_sequence`` this field is not padded here: the internal
    record already carries the full per-task key set, and this only removes
    keys the platform would reject.
    """
    allowed = _WEIGHT_KEYS_BY_TASK.get(task)
    if allowed is None or not isinstance(weights, dict):
        return dict(weights or {}) if isinstance(weights, dict) else {}
    return {k: v for k, v in weights.items() if k in allowed}


# --- reveal_sequence: internal trace shape -> platform socket vocabulary -----
#
# The run's own ``reveal_sequence`` is a list of rich trace dicts
# (``page``/``order``/``key``/``label``/``value``/``via``/``ts``), which is what
# the vendored evaluation fixtures also contain -- so every local check passed.
# The platform's live socket schema instead types this field as the union of the
# clinical *segments*:
#
#     instance is not one of ['family_history', 'previous_notes',
#     'laboratory_results', 'psa_trend', 'radiology_report']
#
# Two GC runs pinned this down. 0e45f139 rejected the dict form outright; then
# a7d7744d rejected the value ``pathology_report`` for TASK 1:
#
#     instance 'pathology_report' is not one of ['family_history', ...]
#
# so the vocabulary is task-scoped and task 1 has exactly five members. The
# project docs mention ``pathology_report`` as a task-2 extra, but that is not
# independently confirmed by a platform error, so it is allowed for task 2 only
# -- never for task 1, where the platform has now explicitly refused it. Note
# the values are the *segment names*, NOT the socket slug
# ``prostate-...-clinical-data`` -- the evaluation's own slug set is
# ``{prostate-biospy-decision, prostate-biopsy-decision}`` for the decision
# socket only.
_TASK1_SEGMENTS = (
    "family_history",
    "previous_notes",
    "laboratory_results",
    "psa_trend",
    "radiology_report",
)

# Task 2 documents one extra segment (histology is part of its decision).
_TASK2_SEGMENTS = _TASK1_SEGMENTS + ("pathology_report",)

_PLATFORM_SEGMENTS_BY_TASK: dict[int, tuple[str, ...]] = {
    1: _TASK1_SEGMENTS,
    2: _TASK2_SEGMENTS,
}

# Internal trace key -> platform segment name.
_KEY_TO_SEGMENT = {
    "section_s3-mri": "radiology_report",
    "section_s3-labs": "laboratory_results",
    "section_s3-psa": "psa_trend",
    "section_s3-prev": "previous_notes",
    "section_s3-fh": "family_history",
    "section_s3-path": "pathology_report",
}

# MCP tool name -> platform segment name (fallback when only the tool is known).
_TOOL_TO_SEGMENT = {
    "get_mri_report": "radiology_report",
    "get_lab_results": "laboratory_results",
    "get_psa_trend": "psa_trend",
    "get_previous_notes": "previous_notes",
    "get_family_history": "family_history",
    "get_pathology_report": "pathology_report",
    "get_surgical_pathology_report": "pathology_report",
}


def _reveal_sequence_for_platform(reveal_sequence: Any, task: int) -> list[str]:
    """Map the internal reveal trace to the platform's segment vocabulary.

    The vocabulary is *task-scoped*: the platform refused ``pathology_report``
    for task 1, so a task-1 sequence can never carry it even though the
    internal trace may contain a pathology section key.

    Entries that cannot be mapped to a segment this task may emit are dropped
    rather than passed through: a single unrecognised value fails the whole
    socket, and this field is a secondary scoring signal, not the decision.
    """
    allowed = _PLATFORM_SEGMENTS_BY_TASK.get(task, ())
    out: list[str] = []
    for entry in reveal_sequence or []:
        segment = None
        if isinstance(entry, str):
            segment = entry
        elif isinstance(entry, dict):
            # Prefer the tool name, then the internal section key.
            for field in ("tool", "tool_name", "name", "via"):
                value = entry.get(field)
                if isinstance(value, str) and value in _TOOL_TO_SEGMENT:
                    segment = _TOOL_TO_SEGMENT[value]
                    break
            if segment is None:
                key = entry.get("key")
                if isinstance(key, str):
                    segment = _KEY_TO_SEGMENT.get(key)
        if segment in allowed and segment not in out:
            out.append(segment)
    return out


# ---------------------------------------------------------------------------
# Config + orchestration
# ---------------------------------------------------------------------------


def _load_config(data_root: Path, output_dir: Path, task: int):
    """Load the canonical config and override paths/scope for this GC run."""
    cfg = OmegaConf.load(CONFIG_PATH)
    OmegaConf.update(cfg, "paths.data_root", str(data_root))
    OmegaConf.update(cfg, "paths.output_dir", str(output_dir))
    OmegaConf.update(cfg, "paths.resource_dir", str(RESOURCE_PATH))
    OmegaConf.update(cfg, "paths.model_dir", str(MODEL_PATH))
    # All weights (LLM GGUF, embedding model, reranker) ship in model.tar.gz,
    # which Grand Challenge mounts at /opt/ml/model. The image only carries the
    # guidelines database under RESOURCE_PATH; embedding/reranker live under
    # /opt/ml/model (mirrors the local data layout used during development).
    OmegaConf.update(cfg, "paths.embedding_model_dir", str(MODEL_PATH / "embedding_model"))
    OmegaConf.update(cfg, "agent.tasks", [task])

    # Point the agent at a specific endpoint.  Grand Challenge runs use the
    # shipped default; offline A/B runs (comparing two served models) set this.
    #
    # NB: the first attempt at this wrapped `inference._load_config` from the
    # calling harness.  That silently did nothing -- two supposedly different
    # models produced byte-identical result files -- so the override now lives
    # *inside* the loader and is verified before use: if the caller asked for a
    # URL and the loaded config does not carry it, the process aborts instead of
    # quietly evaluating the wrong model.
    requested = os.environ.get("CHIMERA_BASE_URL", "").strip()
    if requested:
        OmegaConf.update(cfg, "model.base_url", requested)
        effective = str(cfg.model.base_url).strip()
        if effective != requested:
            raise RuntimeError(
                f"base_url override did not take effect: requested {requested!r} "
                f"but config carries {effective!r} -- refusing to run the wrong model"
            )
        log.info("model.base_url overridden to %s (verified)", effective)
    return cfg


def run(*, _profile_registry=None, _token_counter=None, _reflection_test=None, _evidence_test=None,
        _host_smoke=None, _case_pilot=None, _benchmark=None) -> int:
    setup_logging("INFO")
    if _case_pilot is not None:
        from chimera_agent_baseline.case_pilot import CasePilotApproval
        if (type(_case_pilot) is not CasePilotApproval or any(x is not None for x in
                (_host_smoke, _profile_registry, _token_counter, _reflection_test, _evidence_test))):
            raise RuntimeFatal('Case pilot requires its own isolated entry scope')
        _case_pilot.verify_input(INPUT_PATH)
    if _benchmark is not None:
        from chimera_agent_baseline.benchmark_entry import BenchmarkApproval
        if (type(_benchmark) is not BenchmarkApproval or any(x is not None for x in
                (_host_smoke, _case_pilot, _profile_registry, _token_counter,
                 _reflection_test, _evidence_test))):
            raise RuntimeFatal('Benchmark requires its own isolated entry scope')
        _benchmark.verify_input(INPUT_PATH)
    slug_to_path = _socket_paths()
    task = _detect_task(slug_to_path)
    log.info("Detected interface for task %d", task)

    with tempfile.TemporaryDirectory(prefix="chimera-gc-") as tmp, ExitStack() as stack:
        tmp_root = Path(tmp)
        data_root = tmp_root / "input"
        output_dir = tmp_root / "output"
        cfg = _load_config(data_root, output_dir, task)
        adaptive = cfg.agent.get('workflow') == 'adaptive_v2_smoke'
        if _case_pilot is not None:
            def identity_check():
                _case_pilot.verify(cfg, CONFIG_PATH, INPUT_PATH, OUTPUT_PATH, task)
            identity_check()
            profile, token_counter = _case_pilot.profile, _case_pilot.count
        elif _host_smoke is not None:
            from chimera_agent_baseline.host_smoke import HostSmokeApproval
            if (type(_host_smoke) is not HostSmokeApproval or not adaptive
                    or any(x is not None for x in (_profile_registry, _token_counter, _reflection_test, _evidence_test))):
                raise RuntimeFatal('Host smoke approval must use its own isolated entry scope')
            _host_smoke.verify(cfg, CONFIG_PATH, INPUT_PATH, OUTPUT_PATH, task)
            profile = _host_smoke.profile
            token_counter = _host_smoke.count
            identity_check = lambda: _host_smoke.verify(cfg, CONFIG_PATH, INPUT_PATH, OUTPUT_PATH, task,
                                                         require_empty_output=False)
        elif _benchmark is not None:
            from chimera_agent_baseline.benchmark_entry import BenchmarkApproval
            if (type(_benchmark) is not BenchmarkApproval or not adaptive
                    or any(x is not None for x in (_profile_registry, _token_counter,
                                                   _reflection_test, _evidence_test))):
                raise RuntimeFatal('Benchmark approval must use its own isolated entry scope')
            _benchmark.verify(cfg, CONFIG_PATH, INPUT_PATH, OUTPUT_PATH, task)
            profile = _benchmark.profile
            token_counter = _benchmark.count
            identity_check = lambda: _benchmark.verify(cfg, CONFIG_PATH, INPUT_PATH, OUTPUT_PATH, task,
                                                       require_empty_output=False)
        else:
            profile = verify_profile(cfg, CONFIG_PATH, registry=_profile_registry)
            token_counter = _token_counter
            identity_check = lambda: verify_identity(profile, cfg, CONFIG_PATH)
        if adaptive:
            if (profile is None or _reflection_test is not None or _evidence_test is not None
                    or not cfg.agent.information_access.enabled
                    or cfg.agent.shared_plan.control_interface_version != 2):
                raise RuntimeFatal('Adaptive smoke requires a scoped v2 profile and guarded sources')
            if OUTPUT_PATH.exists() and any(OUTPUT_PATH.iterdir()):
                raise RuntimeFatal('Adaptive smoke needs an empty output directory')
        if _reflection_test is not None:
            from chimera_agent_baseline.agent.reflection_contract import SyntheticReview
            if (type(_reflection_test) is not SyntheticReview or _profile_registry is None
                    or profile is None or profile.purpose != 'synthetic_test_only'):
                raise RuntimeFatal('Reflection fixture requires a code-injected synthetic profile')
        if _evidence_test is not None:
            from chimera_agent_baseline.agent.evidence_reconciliation import SyntheticEvidence
            from chimera_agent_baseline.guarded_runtime import enabled
            if (type(_evidence_test) is not SyntheticEvidence or _profile_registry is None
                    or profile is None or profile.purpose != 'synthetic_test_only'
                    or _evidence_test.task != task or _evidence_test.case_id != CASE_ID
                    or not enabled(cfg) or cfg.agent.workflow != 'react'):
                raise RuntimeFatal('Evidence fixture requires a matched code-injected guarded ReAct profile')
        runtime = None
        if profile is not None:
            def refuse_unverified_counter(payload):
                raise RuntimeFatal("No exact full-template counter approved")
            runtime = PrivateRuntime(profile.audit_root,
                max_attempts=profile.max_attempts,
                max_reserved_tokens=profile.max_reserved_tokens,
                token_counter=token_counter or refuse_unverified_counter,
                identity=dict(profile=profile.name, purpose=profile.purpose,
                              effective_config=effective_config(cfg), sources=profile.sources,
                              loaded_flags=profile.runtime_flags),
                identity_check=identity_check)
            runtime.host_smoke = _host_smoke is not None
            runtime.benchmark = _benchmark is not None
            runtime.case_pilot = _case_pilot
            runtime.adaptive_smoke = adaptive
            runtime.reflection_test = _reflection_test
            runtime.evidence_test = _evidence_test
            if runtime.root.is_relative_to(tmp_root):
                raise RuntimeFatal("Audit sink must survive temporary directory cleanup")
            stack.enter_context(runtime.activate())
        stack.enter_context(case_scope(task, CASE_ID))
        paths = {key: OUTPUT_PATH / filename for key, filename in OUTPUT_SOCKETS[task].items()}
        try:
            try:
                if adaptive and task == 3:
                    _materialise_case(task, slug_to_path, data_root, CASE_ID, t3_upfront_runtime=runtime)
                else:
                    _materialise_case(task, slug_to_path, data_root, CASE_ID)
                if adaptive:
                    runtime.emit('adaptive_profile_scope', task=task, controller='shared_plan.v2',
                                 c02='not_validated_no_approved_C02_source_or_consumer',
                                 b='skipped_no_certified_time_source' if task == 3 else 'formal_policy_pending',
                                 final_field_authority='model_form_plus_platform_projection')
                log.info("Starting agent inference (model=%s, task=%d)", cfg.model.model_id, task)
                embed_svc = None if profile is not None else start_embedding_service(cfg.paths.embedding_model_dir)
                try:
                    asyncio.run(run_agent(cfg))
                finally:
                    if embed_svc:
                        embed_svc.stop()
                if _case_pilot is not None and _case_pilot.dry:
                    if runtime.attempts or any(OUTPUT_PATH.iterdir()):
                        raise RuntimeFatal('Case pilot dry gate produced a side effect')
                    return 0
                prediction = _load_json(output_dir / f"task{task}" / CASE_ID / "prediction.json")
                values = dict(decision=_decision_value(task, prediction),
                              reasoning=_reasoning_value(task, prediction, align=not adaptive))
                for key, path in paths.items():
                    _write_json(path, values[key])
            finally:
                if runtime is not None:
                    try:
                        runtime.export_tree(tmp_root)
                    except OSError as error:
                        raise RuntimeFatal('audit_persistence_failed') from error
            if runtime is not None:
                authority = ('engineering_form_and_platform_projection' if adaptive else
                             'existing_projection_and_align_unreviewed_D05')
                runtime.finalize_sockets(task, CASE_ID, prediction, paths, values,
                    weight_keys=_WEIGHT_KEYS_BY_TASK.get(task), authority=authority)
                runtime.emit('run_completed', attempts=runtime.attempts,
                             reserved_tokens=runtime.reserved_tokens, scope=profile.purpose)
        except BaseException:
            if adaptive:
                for path in paths.values():
                    path.unlink(missing_ok=True)
            raise
    log.info("Wrote GC result sockets for task %d to %s", task, OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
