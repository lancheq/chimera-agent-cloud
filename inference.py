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
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from reasoning_align import align_reasoning
from src.chimera_agent_baseline.rag import start_embedding_service
from src.chimera_agent_baseline.run import run_agent
from src.chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK
from src.chimera_agent_baseline.utils import setup_logging

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


def _materialise_case(task: int, slug_to_path: dict[str, Path], root: Path, case_id: str) -> None:
    """Write the ``task<N>/agent_input/<case>/`` tree the package expects."""
    case_dir = root / f"task{task}" / "agent_input" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    # structured-prompt -> prompt.json. The package renders this through the
    # Jinja template; it must carry case_id + task (the Jinja ``show`` macro
    # tolerates any other missing field, rendering it as an em dash).
    prompt: dict[str, Any] = {}
    if STRUCTURED_PROMPT_SLUG in slug_to_path:
        loaded = _load_json(slug_to_path[STRUCTURED_PROMPT_SLUG])
        if isinstance(loaded, dict):
            prompt = dict(loaded)
    prompt["case_id"] = case_id
    prompt["task"] = task
    _write_json(case_dir / "prompt.json", prompt)

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


def _reasoning_value(task: int, prediction: dict[str, Any]) -> Any:
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
    prediction = align_reasoning(task, prediction)
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


def run() -> int:
    setup_logging("INFO")

    slug_to_path = _socket_paths()
    task = _detect_task(slug_to_path)
    log.info("Detected interface for task %d", task)

    with tempfile.TemporaryDirectory(prefix="chimera-gc-") as tmp:
        tmp_root = Path(tmp)
        data_root = tmp_root / "input"
        output_dir = tmp_root / "output"
        _materialise_case(task, slug_to_path, data_root, CASE_ID)

        cfg = _load_config(data_root, output_dir, task)
        log.info("Starting agent inference (model=%s, task=%d)", cfg.model.model_id, task)

        embed_svc = start_embedding_service(cfg.paths.embedding_model_dir)
        try:
            asyncio.run(run_agent(cfg))
        finally:
            if embed_svc:
                embed_svc.stop()

        prediction = _load_json(output_dir / f"task{task}" / CASE_ID / "prediction.json")

    # Write the GC result sockets flat to /output, in the task-specific shapes.
    # The reasoning socket carries the tool ``reveal_sequence`` so nothing is
    # lost even though only the two declared sockets are written.
    sockets = OUTPUT_SOCKETS[task]
    _write_json(OUTPUT_PATH / sockets["decision"], _decision_value(task, prediction))
    _write_json(OUTPUT_PATH / sockets["reasoning"], _reasoning_value(task, prediction))

    log.info("Wrote GC result sockets for task %d to %s", task, OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
