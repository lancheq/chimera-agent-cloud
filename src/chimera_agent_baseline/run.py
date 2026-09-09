"""Agent entry-point.

Hydra-driven runner. Walks the hierarchical input tree
``<data_root>/task<N>/agent_input/<case>/`` and runs the LangGraph ReAct +
form-fill graph on every case, one task at a time, writing
``<output_dir>/task<N>/<case>/prediction.json``.

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
import time
from datetime import datetime
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
from chimera_agent_baseline.predictor import Predictor
from chimera_agent_baseline.rag import start_embedding_service
from chimera_agent_baseline.utils import setup_logging

load_dotenv()
log = logging.getLogger(__name__)


_VALID_TASKS = (1, 2, 3)

# GT decision file per task (used by skip_gt filter)
_GT_FILES = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}


def _serialize_messages(messages: list) -> list[dict]:
    """Serialize LangChain messages to JSON-safe dicts for trace output."""
    out = []
    for msg in messages:
        entry: dict[str, Any] = {"type": getattr(msg, "type", type(msg).__name__)}
        content = getattr(msg, "content", str(msg))
        if isinstance(content, str):
            entry["content"] = content[:8000]  # cap to keep trace manageable
        else:
            entry["content"] = str(content)[:8000]
        # Capture tool calls if present (AIMessage)
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [
                {"name": tc.get("name", tc.get("type", "")), "args": str(tc.get("args", ""))[:500]}
                for tc in tool_calls
            ]
        # Capture tool_call_id for ToolMessage
        tool_call_id = getattr(msg, "tool_call_id", None)
        if tool_call_id:
            entry["tool_call_id"] = tool_call_id
        out.append(entry)
    return out


def _save_trace(case_dir: Path, case_id: str, task_int: int, result: dict | None,
                elapsed: float, exc: Exception | None = None) -> None:
    """Save a debug trace.json with full intermediate state for reproduction.

    Captures data that is normally discarded after graph.ainvoke:
    - All messages (plan, agent, tool results, form_fill prompt/response)
    - disease_plan, refined_transcript, reflect_warnings, form_fill_warnings
    - Per-case timing
    - Exception details if failed
    """
    trace: dict[str, Any] = {
        "case_id": case_id,
        "task": task_int,
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 2),
    }
    if exc is not None:
        trace["status"] = "failed"
        trace["error_type"] = type(exc).__name__
        trace["error_message"] = str(exc)[:2000]
    elif result is not None:
        trace["status"] = "success"
        trace["messages"] = _serialize_messages(result.get("messages", []))
        trace["disease_plan"] = result.get("disease_plan")
        trace["refined_transcript"] = result.get("refined_transcript")
        trace["reflect_warnings"] = result.get("reflect_warnings", [])
        trace["form_fill_warnings"] = result.get("form_fill_warnings", [])
        trace["structured_response"] = result.get("structured_response")
    else:
        trace["status"] = "unknown"
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "trace.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False, default=str))


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


def _detect_log_file() -> str | None:
    """Return basename of stdout's target if it is a regular file (Linux /proc)."""
    try:
        target = os.readlink("/proc/self/fd/1")
        if target and Path(target).is_file():
            return Path(target).name
    except OSError:
        pass
    return None


def _write_run_manifest(cfg: DictConfig, plan: list[tuple[int, Path]], status: str = "running") -> None:
    """Write ``.chimera_run.json`` so the status command can target this run
    even after the process exits. Best-effort: never fails the run.

    The manifest is the single source of truth for "what is the current run" —
    it records the resolved task list, case subset (pids/limit), output dir,
    data root and real log file, removing the need for the status command to
    reverse-engineer these from the process command line.
    """
    try:
        pids = cfg.agent.get("pids")
        limit = cfg.agent.get("limit")
        manifest = {
            "start_time": datetime.now().isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "output_dir": str(cfg.paths.output_dir),
            "data_root": str(cfg.paths.data_root),
            "tasks": [t for t, _ in plan],
            "pids": list(pids) if pids else None,
            "limit": int(limit) if limit else None,
            "log_file": _detect_log_file(),
            "status": status,
        }
        Path(".chimera_run.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False)
        )
    except Exception as exc:
        log.warning("Failed to write run manifest: %s", exc)


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
        "--reranker-dir",
        str(Path(cfg.paths.model_dir) / "reranker_model"),
        "--tool-registry",
        registry,
    ]
    # Optional image-embedding predictor tool (off by default).
    predictor = cfg.agent.get("predictor")
    if predictor and predictor.get("enabled"):
        args += ["--enable-predictor"]
    return args


async def _run_task(
    cfg: DictConfig,
    task_int: int,
    input_dir: Path,
    model,
    system_prompt: str,
    predictor: Predictor | None = None,
) -> int:
    """Run every case for one task; returns the number of predictions written."""
    registry = f"task{task_int}"
    queries = _filter_queries(load_cases(input_dir, task=task_int), cfg)

    # skip_gt: exclude cases that have GT files, mock cases, and official cases.
    # Used for pseudo-label generation runs on unlabelled data only.
    if cfg.agent.get("skip_gt", False):
        gt_file = _GT_FILES.get(task_int)
        before = len(queries)
        queries = [
            q for q in queries
            if "mock" not in q["case_id"]
            and not q["case_id"].startswith("BX_")
            and not q["case_id"].startswith("T2_")
            and not (gt_file and (input_dir / q["case_id"] / gt_file).exists())
        ]
        log.info("Task %d: skip_gt filtered %d -> %d cases (excluded GT/mock/official)",
                 task_int, before, len(queries))

    # decision_override 支持按任务列表控制；列表为空时回退到全局 bool
    pred_cfg = cfg.agent.get("predictor", {})
    override_tasks = pred_cfg.get("decision_override_tasks") or []
    if override_tasks:
        decision_override = task_int in {int(t) for t in override_tasks}
    else:
        decision_override = bool(pred_cfg.get("decision_override", False))
    if decision_override:
        log.info("Task %d: predictor decision override ENABLED", task_int)

    log.info("Task %d: starting MCP server (data_dir=%s)", task_int, input_dir)
    client = MultiServerMCPClient(
        {
            "chimera": {
                "command": sys.executable,
                "args": _mcp_args(cfg, input_dir, registry),
                "transport": "stdio",
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
        decision_override=decision_override,
        self_refine=bool(cfg.agent.form_fill.get("self_refine", False)),
        atomic_check=bool(cfg.agent.form_fill.get("atomic_check", False)),
        self_refine_max_iter=int(cfg.agent.form_fill.get("self_refine_max_iter", 2)),
        # W5-T2: force search_guidelines on T1 gray-zone cases (PSA 3-10 or
        # PI-RADS 3-4) and inject the hits into the agent's context.
        force_rag_grayzone=bool(cfg.agent.get("force_rag_grayzone", False)),
    )

    # Output mirrors the agent-input hierarchy: <output_dir>/task<N>/<case_id>/prediction.json
    task_dir = Path(cfg.paths.output_dir) / f"task{task_int}"

    n_done = 0
    n_failed = 0
    n_skipped = 0
    for query in queries:
        case_id = query["case_id"]

        # Resume support: skip finished cases and cases already marked failed.
        # A case that failed all form_fill retries will (almost certainly) fail
        # again identically on the next resume; re-running it just burns LLM
        # time in an endless loop. Deleting the .failed marker re-enables retry.
        case_dir = task_dir / case_id
        if (case_dir / "prediction.json").exists() or (case_dir / ".failed").exists():
            n_skipped += 1
            continue

        log.info("Task %d: processing case %s", task_int, case_id)

        case_start = time.monotonic()

        # The graph's ReAct loop runs the agent and tools until a final
        # assistant message arrives, then the terminal ``form_fill`` node
        # prompts the SAME model with a per-task Pydantic schema and
        # validates with PydanticOutputParser. No external API.
        try:
            predictor_decision: dict[str, Any] = {}
            if predictor is not None and task_int in predictor.available_tasks:
                try:
                    predictor_decision = predictor.predict(input_dir / case_id, task_int)
                except Exception as exc:
                    log.warning("Task %d: predictor failed for %s: %s", task_int, case_id, exc)

            initial_state: dict[str, Any] = {
                "messages": [HumanMessage(content=query["context"])],
                "case_id": case_id,
                "task": task_int,
                "patient": {"psa": query.get("psa"), "age": query.get("age")},
                "predictor_decision": predictor_decision,
            }
            result = await graph.ainvoke(initial_state, {"recursion_limit": cfg.agent.max_iterations})

            # form_fill is the single validation point: it parses the model's
            # output against the per-task Pydantic model and raises if every
            # retry fails, so the structured_response here is already valid.
            structured = result["structured_response"]

            # Single output file per patient, in a per-case folder mirroring the
            # agent input. The file is exactly the validated structured record.
            case_dir = task_dir / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "prediction.json").write_text(json.dumps(structured, indent=2))

            elapsed = time.monotonic() - case_start
            log.info("Task %d: case %s done in %.1fs", task_int, case_id, elapsed)
            _save_trace(case_dir, case_id, task_int, result, elapsed)

            n_done += 1
        except Exception as exc:
            # A single case failing (e.g. form_fill cannot parse the model
            # output after all retries) must not abort the whole task —
            # log and move on so the remaining cases still produce output.
            # Mark the case as failed so a later resume skips it (no endless
            # re-runs of the same failing case).
            elapsed = time.monotonic() - case_start
            log.error("Task %d: case %s FAILED after %.1fs, skipping: %s", task_int, case_id, elapsed, exc)
            n_failed += 1
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / ".failed").write_text(f"{type(exc).__name__}: {exc}\n")
            _save_trace(case_dir, case_id, task_int, None, elapsed, exc)
            continue

    log.info("Task %d: wrote %d predictions (%d skipped, %d failed) under %s", task_int, n_done, n_skipped, n_failed, task_dir)
    return n_done


async def run_agent(cfg: DictConfig) -> None:
    """Run every task present under ``data_root`` (model loaded once, reused)."""
    plan = _run_plan(cfg)
    log.info("Run plan: tasks %s", [t for t, _ in plan])
    _write_run_manifest(cfg, plan, status="running")
    try:
        model = load_model(cfg)

        # Deterministic predictor (decision from LLM 抽离). Loaded once, reused.
        predictor = None
        pred_cfg = cfg.agent.get("predictor", {})
        needs_predictor = (
            pred_cfg.get("enabled")
            or pred_cfg.get("decision_override")
            or bool(pred_cfg.get("decision_override_tasks"))
        )
        if needs_predictor:
            try:
                predictor = Predictor(Path(cfg.paths.model_dir) / "predictor")
            except Exception as exc:
                log.warning("Predictor not loaded (will run pure-LLM): %s", exc)

        total = 0
        for task_int, input_dir in plan:
            # P1: per-task system prompt（校准段只注入当前任务小节），graph 在
            # _run_task 内每 task 重建（create_graph 本身不动）。
            system_prompt = build_system_prompt(task_int)
            total += await _run_task(cfg, task_int, input_dir, model, system_prompt, predictor)
        log.info("Done. Wrote %d predictions across %d task(s).", total, len(plan))
    except BaseException:
        _write_run_manifest(cfg, plan, status="failed")
        raise
    else:
        _write_run_manifest(cfg, plan, status="done")


@hydra.main(version_base=None, config_path="../../configs", config_name="config")
def main(cfg: DictConfig) -> None:
    setup_logging(cfg.logging.level)

    embed_svc = start_embedding_service(cfg.paths.embedding_model_dir)
    try:
        asyncio.run(run_agent(cfg))
    finally:
        if embed_svc:
            embed_svc.stop()


if __name__ == "__main__":
    main()
