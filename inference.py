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
import tempfile
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from src.chimera_agent_baseline.rag import start_embedding_service
from src.chimera_agent_baseline.run import run_agent
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
# value and the reasoning value. Slugs carry the challenge's ``biospy`` spelling.
OUTPUT_SOCKETS: dict[int, dict[str, str]] = {
    1: {
        "decision": "prostate-biospy-decision.json",
        "reasoning": "prostate-biospy-decision-reasoning.json",
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

    # clinical-data -> clinical.json (served behind MCP tools). Field names in
    # the GC socket already match the ToolSpec field names, so pass it through.
    clinical_slug = next(s for s in CLINICAL_SLUG_TO_TASK if s in slug_to_path)
    clinical = _load_json(slug_to_path[clinical_slug])
    if isinstance(clinical, dict):
        clinical.setdefault("case_id", case_id)
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
    """
    if task == 3:
        return prediction["free_text"]
    return {
        "free_text": prediction["free_text"],
        "confidence": prediction["confidence"],
        "variable_weights": prediction["variable_weights"],
        "reveal_sequence": prediction.get("reveal_sequence", []),
    }


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
