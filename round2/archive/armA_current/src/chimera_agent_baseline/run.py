"""Agent entry-point.

Hydra-driven runner. Walks the hierarchical input tree
``<data_root>/task<N>/agent_input/<case>/`` and runs the LangGraph ReAct +
form-fill graph on every case, one task at a time, writing task-specific Grand Challenge output files under
``<output_dir>/task<N>/<case>/``.

By default every task present under ``data_root`` is run (``agent.tasks``);
missing task dirs are skipped. The model is loaded once and reused across
tasks. The Grand Challenge container uses the same layout, rooted at
``/input`` / ``/output``.

Usage::

    make run                                       # all tasks under data/
    make run RUN_ARGS="agent.tasks=[2]"            # just task 2
    make run RUN_ARGS="+experiment=qwen_local"     # swap to Qwen
    make run RUN_ARGS="agent.limit=5"              # first 5 cases per task
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import hydra
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from omegaconf import DictConfig

from chimera_agent_baseline.agent.graph import create_graph
from chimera_agent_baseline.agent.prompts import build_system_prompt
from chimera_agent_baseline.case_loader import load_cases
from chimera_agent_baseline.models import load_model
from chimera_agent_baseline.rag import start_embedding_service
from chimera_agent_baseline.utils import setup_logging

load_dotenv()
log = logging.getLogger(__name__)


_VALID_TASKS = (1, 2, 3)


def _task_input_dir(cfg: DictConfig, task: int) -> Path:
    return Path(cfg.paths.data_root) / f"task{task}" / "agent_input"


def _run_plan(cfg: DictConfig) -> list[tuple[int, Path]]:
    """Resolve ``agent.tasks`` to ``(task_int, input_dir)`` pairs that exist."""
    plan: list[tuple[int, Path]] = []
    for raw in cfg.agent.tasks:
        task = int(raw)
        if task not in _VALID_TASKS:
            raise ValueError(f"Unknown task {task!r} in agent.tasks; expected one of {list(_VALID_TASKS)}")
        input_dir = _task_input_dir(cfg, task)
        if input_dir.is_dir():
            plan.append((task, input_dir))
        else:
            log.warning("Skipping task %d: %s not found", task, input_dir)
    if not plan:
        raise FileNotFoundError(f"No task data found under {cfg.paths.data_root} for tasks {list(cfg.agent.tasks)}")
    return plan


def _filter_queries(queries: list[dict], cfg: DictConfig) -> list[dict]:
    """Apply optional ``cfg.agent.pids`` / ``cfg.agent.limit`` subset filters."""
    pids = cfg.agent.get("pids")
    if pids:
        wanted = set(pids)
        out = [q for q in queries if q["case_id"] in wanted]
        missing = wanted - {q["case_id"] for q in out}
        if missing:
            log.warning("Requested pids not found in input dir: %s", sorted(missing))
        log.info("Filtered to %d hand-picked cases: %s", len(out), [q["case_id"] for q in out])
        return out
    limit = cfg.agent.get("limit")
    if limit:
        out = queries[: int(limit)]
        log.info("Limiting to first %d cases", len(out))
        return out
    return queries


def _mcp_args(cfg: DictConfig, input_dir: Path, registry: str) -> list[str]:
    args = [
        "-m",
        "chimera_agent_baseline.mcp_server",
        "--data-dir",
        str(input_dir),
        "--resource-dir",
        str(cfg.paths.resource_dir),
        "--tool-registry",
        registry,
    ]
    # Optional image-embedding predictor tool (off by default).
    predictor = cfg.agent.get("predictor")
    if predictor and predictor.get("enabled"):
        args += ["--enable-predictor"]
    return args

def _mcp_environment() -> dict[str, str]:
    """Forward the settings the stdio MCP server cannot otherwise inherit.

    The MCP SDK passes only basic OS variables. Without PYTHONPATH the server
    resolves ``chimera_agent_baseline`` from whatever copy is installed in the
    interpreter, which for this benchmark is the *other* arm's tree.
    """
    keys = {
        "PYTHONPATH", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "TOKENIZERS_PARALLELISM",
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "CHIMERA_EMBED_SOCKET", "CHIMERA_OUTPUT_DIR", "CHIMERA_BENCH_LOG",
    }
    return {key: os.environ[key] for key in sorted(keys) if key in os.environ}


def _write_json(path: Path, content: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2))


def _write_task_outputs(case_dir: Path, task: int, structured: dict[str, Any]) -> None:
    """Write GC-style output files for one case."""

    if task == 1:
        decision = "yes" if structured["biopsy_decision"] else "no"
        reasoning = {
            "confidence": structured["confidence"],
            "variable_weights": structured["variable_weights"],
            "reveal_sequence": structured["reveal_sequence"],
            "free_text": structured["reasoning"],
        }

        _write_json(case_dir / "prostate-biopsy-decision.json", decision)
        _write_json(case_dir / "prostate-biopsy-decision-reasoning.json", reasoning)
        return

    if task == 2:
        reasoning = {
            "confidence": structured["confidence"],
            "variable_weights": structured["variable_weights"],
            "reveal_sequence": structured["reveal_sequence"],
            "free_text": structured["reasoning"],
        }

        _write_json(case_dir / "prostate-treatment-decision.json", structured["action"])
        _write_json(case_dir / "prostate-treatment-decision-reasoning.json", reasoning)
        return

    if task == 3:
        _write_json(
            case_dir / "prostate-time-to-recurrence-or-last-follow-up.json",
            {
                "event": structured.get("event", 0),
                "months_to_recurrence": structured["months_to_recurrence"],
            },
        )
        _write_json(
            case_dir / "prostate-time-to-recurrence-or-last-follow-up-reasoning.json",
            structured["reasoning"],
        )
        return

    raise ValueError(f"Unknown task: {task}")

async def _run_task(cfg: DictConfig, task_int: int, input_dir: Path, model, system_prompt: str) -> int:
    """Run every case for one task; returns the number of predictions written."""
    registry = f"task{task_int}"
    queries = _filter_queries(load_cases(input_dir, task=task_int), cfg)

    log.info("Task %d: starting MCP server (data_dir=%s)", task_int, input_dir)
    client = MultiServerMCPClient(
        {
            "chimera": {
                "command": sys.executable,
                "args": _mcp_args(cfg, input_dir, registry),
                "transport": "stdio",
                # MCP stdio keeps only a basic OS allowlist, so PYTHONPATH would
                # be dropped and the subprocess would import whatever copy of the
                # package is pip-installed in this interpreter -- i.e. a different
                # source tree than the agent process. Forward the settings the
                # server actually needs. Runtime compatibility only; no policy.
                "env": _mcp_environment(),
            },
        }
    )
    tools = await client.get_tools()
    log.info("Task %d: loaded %d tools from MCP server", task_int, len(tools))

    graph = create_graph(
        tools,
        model,
        system_prompt,
        step_timeout=cfg.agent.step_timeout,
        form_fill_max_retries=cfg.agent.form_fill.max_retries,
    )
    # Output mirrors the agent-input hierarchy:
    # <output_dir>/task<N>/<case_id>/<task-specific-output-files>.json
    task_dir = Path(cfg.paths.output_dir) / f"task{task_int}"

    n_done = 0
    for query in queries:
        case_id = query["case_id"]
        log.info("Task %d: processing case %s", task_int, case_id)

        # Cost instrument marker: lets a batched run be attributed per case
        # without changing anything the agent does. Measurement only.
        try:
            from chimera_agent_baseline import bench_cost
            bench_cost.record("case_start", case=case_id, task=task_int)
        except Exception:  # noqa: BLE001
            pass

        # The graph's ReAct loop runs the agent and tools until a final
        # assistant message arrives, then the terminal ``form_fill`` node
        # prompts the SAME model with a per-case Pydantic schema and
        # validates with PydanticOutputParser. No external API.
        initial_state: dict[str, Any] = {
            "messages": [HumanMessage(content=query["context"])],
            "case_id": case_id,
            "task": task_int,
        }
        result = await graph.ainvoke(initial_state, {"recursion_limit": cfg.agent.max_iterations})

        # form_fill is the single validation point: it parses the model's
        # output against the per-task Pydantic model and raises if every
        # retry fails, so the structured_response here is already valid.
        structured = result["structured_response"]

        # GC-style output files per patient.
        case_dir = task_dir / case_id
        #case_dir.mkdir(parents=True, exist_ok=True)
        #(case_dir / "prediction.json").write_text(json.dumps(structured, indent=2))
        _write_task_outputs(case_dir, task_int, structured)
        n_done += 1

    log.info("Task %d: wrote %d predictions under %s", task_int, n_done, task_dir)
    return n_done


async def run_agent(cfg: DictConfig) -> None:
    """Run every task present under ``data_root`` (model loaded once, reused)."""
    plan = _run_plan(cfg)
    log.info("Run plan: tasks %s", [t for t, _ in plan])

    model = load_model(cfg)
    system_prompt = build_system_prompt()

    total = 0
    for task_int, input_dir in plan:
        total += await _run_task(cfg, task_int, input_dir, model, system_prompt)
    log.info("Done. Wrote %d predictions across %d task(s).", total, len(plan))


@hydra.main(version_base=None, config_path="../../configs", config_name="config")
def main(cfg: DictConfig) -> None:
    setup_logging(cfg.logging.level)

    #embed_svc = start_embedding_service(cfg.paths.resource_dir)
    embed_svc = start_embedding_service(cfg.paths.embedding_model_dir)
    try:
        asyncio.run(run_agent(cfg))
    finally:
        if embed_svc:
            embed_svc.stop()


if __name__ == "__main__":
    main()
