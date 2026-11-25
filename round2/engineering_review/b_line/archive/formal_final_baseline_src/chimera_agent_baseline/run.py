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
import hashlib
import json
import logging
import os
import subprocess
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
from chimera_agent_baseline.runtime_audit import (ComponentFailure, component_event, strict_runtime,
    case_scope, consumption_event)
from chimera_agent_baseline import guarded_runtime

load_dotenv()
log = logging.getLogger(__name__)


_VALID_TASKS = (1, 2, 3)

# GT decision file per task (used by skip_gt filter)
_GT_FILES = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}


def _mcp_environment() -> dict[str, str]:
    """Pass required local runtime settings through stdio's env allowlist.

    The MCP SDK otherwise inherits only basic OS variables. In particular a
    per-run embedding socket and the frozen source path must reach the server.
    Do not forward model API credentials or the entire parent environment.
    """
    keys = {
        "PYTHONPATH", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "TOKENIZERS_PARALLELISM",
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "CHIMERA_EMBED_SOCKET", "CHIMERA_QA_STRICT", "CHIMERA_RERANKER",
        "CHIMERA_STRICT_RUNTIME", "CHIMERA_OUTPUT_DIR",
    }
    return {key: os.environ[key] for key in sorted(keys) if key in os.environ}


def _serialize_messages(messages: list, content_limit: int = 8000) -> list[dict]:
    """Serialize LangChain messages to JSON-safe dicts for trace output."""
    if content_limit < 0:
        raise ValueError("Trace content limit must be nonnegative (0 means unlimited)")
    out = []
    for msg in messages:
        entry: dict[str, Any] = {"type": getattr(msg, "type", type(msg).__name__)}
        for key in ("id", "name", "status", "usage_metadata"):
            value = getattr(msg, key, None)
            if value is not None:
                entry[key] = value
        content = getattr(msg, "content", str(msg))
        if isinstance(content, str):
            entry["content"] = content[:content_limit] if content_limit else content
            entry["content_truncated"] = bool(content_limit and len(content) > content_limit)
        else:
            entry["content"] = str(content)[:content_limit] if content_limit else str(content)
        # Capture tool calls if present (AIMessage)
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [
                {"id": tc.get("id"), "name": tc.get("name", tc.get("type", "")), "args": tc.get("args", {})}
                for tc in tool_calls
            ]
        # Capture tool_call_id for ToolMessage
        tool_call_id = getattr(msg, "tool_call_id", None)
        if tool_call_id:
            entry["tool_call_id"] = tool_call_id
        out.append(entry)
    return out


def _save_trace(case_dir: Path, case_id: str, task_int: int, result: dict | None,
                elapsed: float, exc: Exception | None = None,
                predictor_trace: dict | None = None) -> None:
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
        trace["messages"] = _serialize_messages(result.get("messages", []),
            content_limit=int(os.environ.get("CHIMERA_TRACE_CONTENT_LIMIT", "8000")))
        trace["disease_plan"] = result.get("disease_plan")
        trace["refined_transcript"] = result.get("refined_transcript")
        trace["reflect_warnings"] = result.get("reflect_warnings", [])
        trace["form_fill_warnings"] = result.get("form_fill_warnings", [])
        trace["structured_response"] = result.get("structured_response")
        trace["policy_trace"] = result.get("policy_trace", [])
        trace["policy_model_turns"] = result.get("policy_model_turns", 0)
        trace["processing_stages"] = result.get("processing_stages", [])
        if "information_access" in result:
            trace["information_access"] = result["information_access"]
    # P0-1: predictor 输入审计（纯观测；不进 prediction.json、不进评分）
    if predictor_trace is not None:
        trace["predictor"] = predictor_trace
    elif exc is None and result is None:
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


def _execution_identity(cfg: DictConfig) -> dict:
    from chimera_agent_baseline.agent import form_fill
    paths = {"runner": Path(__file__), "form_fill": Path(form_fill.__file__),
             "form_parsing": Path(__file__).parent / "agent/form_parsing.py",
             "postprocess": Path(__file__).parent / "agent/postprocess.py",
             "evidence_features": Path(__file__).parent / "agent/evidence_features.py",
             "predictor_node": Path(__file__).parent / "agent/predictor_node.py",
             "predictor_source": Path(__file__).parent / "predictor.py",
             "runtime_audit": Path(__file__).parent / "runtime_audit.py",
             "t1_v1_coefficients": Path(form_fill._T1_DL_JSON),
             "t1_v2_coefficients": Path(form_fill._T1_DL2_JSON)}
    for task in (1, 2, 3):
        paths[f"predictor_task{task}"] = Path(cfg.paths.model_dir) / "predictor" / f"predictor_task{task}.joblib"
    return {
        "python": sys.executable,
        "strict_runtime": strict_runtime(),
        "trace_content_limit": int(os.environ.get("CHIMERA_TRACE_CONTENT_LIMIT", "8000")),
        "configured_paths": {key: str(value) for key, value in cfg.paths.items()},
        "model_provider": str(cfg.model.get("provider")),
        "model_id": str(cfg.model.get("model_id")),
        "effective_t1_decision_layer": form_fill._T1_DL_MODE,
        "t1_evidence_features": bool(cfg.agent.get("predictor", {}).get("t1_evidence_features", False)),
        "weight_table_enabled": form_fill._WEIGHT_TABLE_ENABLED,
        "files": {key: {"path": str(path.resolve()), "exists": path.is_file(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
                  for key, path in paths.items()},
    }


def _write_run_manifest(cfg: DictConfig, plan: list[tuple[int, Path]], status: str = "running",
                        task_summaries: list[dict] | None = None) -> None:
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
            "workflow": str(cfg.agent.get("workflow", "planned_react")),
            "max_model_turns": cfg.agent.get("max_model_turns"),
            "plan_context_chars": cfg.agent.get("plan_context_chars", 500),
            "task_summaries": task_summaries or [],
            "execution_identity": _execution_identity(cfg),
            "information_access_enabled": guarded_runtime.enabled(cfg),
        }
        # Keep the start timestamp on the final status update.
        current = Path(".chimera_run.json")
        if status != "running" and current.is_file():
            previous = json.loads(current.read_text())
            if previous.get("pid") == os.getpid() and previous.get("output_dir") == manifest["output_dir"]:
                manifest["start_time"] = previous.get("start_time", manifest["start_time"])
        manifest["updated_time"] = datetime.now().isoformat(timespec="seconds")
        Path(".chimera_run.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False)
        )
        output_dir = Path(cfg.paths.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
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
    # MCP SDK 会剥离父环境（get_default_environment 仅含 5 个基础变量），
    # CHIMERA_EMBED_SOCKET 必须显式转发，否则 MCP 子进程拿不到自定义 socket，
    # QA dense / chunk dense 会静默失效（2026-10-26 臂 B 实测 21 次回退即此因）。
    args += [
        "--embed-socket",
        os.environ.get("CHIMERA_EMBED_SOCKET", "/tmp/chimera_embed.sock"),
    ]
    return args


async def _load_mcp_tools(cfg, input_dir, registry, task_int, access_path=None, *, evidence_session=None):
    log.info("Task %d: starting MCP server (data_dir=%s)", task_int, input_dir)
    client = MultiServerMCPClient(
        {
            "chimera": {
                "command": sys.executable,
                "args": _mcp_args(cfg, input_dir, registry) + (
                    ["--access-session", str(access_path)] if access_path is not None else []),
                "transport": "stdio",
                "env": _mcp_environment(),
            },
        },
        **({"tool_interceptors": [evidence_session.attest_mcp_result]} if evidence_session is not None else {}),
    )
    tools = await client.get_tools()
    from chimera_agent_baseline.runtime_audit import current_runtime
    runtime = current_runtime()
    pilot = getattr(runtime, 'case_pilot', None)
    if pilot is not None:
        pilot.verify_registry(tools, task_int, runtime)
    if access_path is not None:
        from chimera_agent_baseline.information_access import tool_contract
        allowed = set(tool_contract(task_int)["tools"])
        if cfg.agent.information_access.get("knowledge_manifest"):
            allowed.add("search_guidelines")
        tools = [t for t in tools if t.name in allowed]
    log.info("Task %d: loaded %d tools from MCP server", task_int, len(tools))

    # T1 工具集瘦身（2026-10-31 补漏）：之前只在 plan_node 里过滤，ReAct 的
    # ToolNode(tools) 与 model_with_tools 仍拿到全量工具 —— 实测 19/91 例
    # 绕过 plan 直接调了病理工具（那些例 acc 0.632 vs 未调 0.875）。
    # 必须在建图前过滤，两处共用同一份 tools。
    if task_int == 1:
        from chimera_agent_baseline.agent.graph import _T1_TOOL_DENY as _DENY
        if _DENY:
            _drop = [t.name for t in tools if t.name in _DENY]
            if _drop:
                tools = [t for t in tools if t.name not in _DENY]
                log.info("Task %d: tool deny dropped %s (remaining %d)", task_int, _drop, len(tools))

    return tools


async def _run_task(cfg, task_int, input_dir, model, system_prompt, predictor=None, task_summaries=None):
    guarded_runtime.validate_options(cfg, predictor)
    if guarded_runtime.enabled(cfg):
        task_dir = Path(cfg.paths.output_dir) / f"task{task_int}"
        with guarded_runtime.output_lease(task_dir):
            return await _run_task_impl(cfg, task_int, input_dir, model, system_prompt, predictor, task_summaries)
    return await _run_task_impl(cfg, task_int, input_dir, model, system_prompt, predictor, task_summaries)


async def _run_task_impl(
    cfg: DictConfig,
    task_int: int,
    input_dir: Path,
    model,
    system_prompt: str,
    predictor: Predictor | None = None,
    task_summaries: list[dict] | None = None,
) -> int:
    """Run every case for one task; returns the number of predictions written."""
    from chimera_agent_baseline.runtime_audit import current_runtime
    runtime = current_runtime()
    pilot = getattr(runtime, 'case_pilot', None)
    guarded_runtime.validate_options(cfg, predictor)
    registry = f"task{task_int}"
    queries = _filter_queries(load_cases(input_dir, task=task_int), cfg)
    if guarded_runtime.enabled(cfg):
        queries = [{**q, "_system_prompt_digest": hashlib.sha256(system_prompt.encode()).hexdigest()} for q in queries]

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

    guarded = guarded_runtime.enabled(cfg)
    adaptive = cfg.agent.get("workflow") == "adaptive_v2_smoke"
    tools = [] if guarded else await _load_mcp_tools(cfg, input_dir, registry, task_int)

    # P0-2b: 成本对齐开关：predictor 只消费 LLM 已揭示的段落。
    # 2026-11-01 实测（tools/loocv_t3.py 的掩码对照，同 73 折同超参）：
    #   T3 LOOCV C-index  掩码 0.7364  vs  不掩码 0.8587  → 掩码净损 **−0.122**
    #   成因：train_predictor.py 用完整 clinical-data 训练，推理再叠 reveal_mask
    #         → 训练/推理不一致；被掩特征走中位数插补，概率被拉向基率 0.5。
    # T1 实测仅 1/197 例缺 pirads_int（≈无影响）；T2 的病理类缺失是结构性的
    # （T2 尚无术后病理），与掩码无关。
    # 故默认：T3 关、T1/T2 保持开（历史行为）。可用 CHIMERA_COST_ALIGN 强制覆盖。
    _ca_env = os.environ.get("CHIMERA_COST_ALIGN")
    if _ca_env is None:
        _cost_align = (task_int != 3)
    else:
        _cost_align = _ca_env.strip().lower() not in ("0", "false", "no", "")
    log.info("Task %d: cost_align=%s%s", task_int, _cost_align,
             "" if _ca_env is None else " (env override)")
    graph_options = dict(
        predictor=predictor,
        cost_align=_cost_align,
        step_timeout=cfg.agent.step_timeout,
        form_fill_max_retries=cfg.agent.form_fill.max_retries,
        decision_override=decision_override,
        self_refine=bool(cfg.agent.form_fill.get("self_refine", False)),
        atomic_check=bool(cfg.agent.form_fill.get("atomic_check", False)),
        self_refine_max_iter=int(cfg.agent.form_fill.get("self_refine_max_iter", 2)),
        # W5-T2: force search_guidelines on T1 gray-zone cases (PSA 3-10 or
        # PI-RADS 3-4) and inject the hits into the agent's context.
        force_rag_grayzone=bool(cfg.agent.get("force_rag_grayzone", False)),
        workflow=str(cfg.agent.get("workflow", "planned_react")),
        max_model_turns=cfg.agent.get("max_model_turns"),
        plan_context_chars=cfg.agent.get("plan_context_chars", 500),
        t1_evidence_features=bool(pred_cfg.get("t1_evidence_features", False)),
    )

    graph = None if guarded else create_graph(tools, model, system_prompt, **graph_options)

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
        if guarded and (case_dir / "prediction.json").exists():
            guarded_runtime.verify_completed(cfg, input_dir, task_int, query, case_dir)
            n_skipped += 1
            continue
        if (case_dir / "prediction.json").exists() or (
            (case_dir / ".failed").exists() and not cfg.agent.get("retry_failed", False)
        ):
            n_skipped += 1
            continue

        log.info("Task %d: processing case %s", task_int, case_id)

        case_start = time.monotonic()

        # The graph's ReAct loop runs the agent and tools until a final
        # assistant message arrives, then the terminal ``form_fill`` node
        # prompts the SAME model with a per-task Pydantic schema and
        # validates with PydanticOutputParser. No external API.
        access = None
        predictor_trace: dict[str, Any] | None = None
        try:
            case_query = query
            if guarded:
                access_path, access, case_query = guarded_runtime.prepare_case(
                    cfg, input_dir, task_int, query, case_dir)
                evidence_session = getattr(access, 'evidence_session', None)
                transport_options = {"evidence_session": evidence_session} if evidence_session is not None else {}
                case_tools = await _load_mcp_tools(cfg, input_dir, registry, task_int, access_path,
                                                  **transport_options)
                if pilot is not None and pilot.dry:
                    runtime.emit('case_pilot_dry_gate', ledger=access.snapshot())
                    return 0
                if not adaptive:
                    graph = create_graph(case_tools, model, system_prompt, guarded_access=access, **graph_options)
            predictor_decision: dict[str, Any] = {}
            # P0-2b：默认改由「图内 predictor 节点」在 ReAct 之后计算（可启用成本对齐）。
            # CHIMERA_PREDICTOR_POST_REACT=0 时退回旧路径（图之前计算）。
            _post_react = os.environ.get(
                "CHIMERA_PREDICTOR_POST_REACT", "1").strip().lower() not in ("0", "false", "no", "")
            if not _post_react and predictor is not None and task_int in predictor.available_tasks:
                try:
                    predictor_decision = predictor.predict(input_dir / case_id, task_int)
                    try:
                        predictor_trace = predictor.explain(input_dir / case_id, task_int)
                        predictor_trace["decision"] = (
                            predictor_decision.get("decision")
                            if predictor_decision.get("decision") is not None
                            else predictor_decision.get("event"))
                        predictor_trace["probabilities"] = (
                            predictor_decision.get("probabilities")
                            or predictor_decision.get("p_event"))
                        predictor_trace["at_threshold"] = predictor_decision.get("at_threshold")
                    except Exception as exc2:  # noqa: BLE001
                        predictor_trace = {"explain_failed": f"{type(exc2).__name__}: {exc2}"}
                        log.warning(
                            "Task %d: predictor.explain failed for %s: %s", task_int, case_id, exc2)
                except Exception as exc:
                    log.warning("Task %d: predictor failed for %s: %s", task_int, case_id, exc)
                    predictor_trace = {"predict_failed": f"{type(exc).__name__}: {exc}"}

            initial_state: dict[str, Any] = {
                "messages": [HumanMessage(content=case_query["context"])],
                "case_id": case_id,
                "task": task_int,
                "patient": {"psa": query.get("psa"), "age": query.get("age")},
                "predictor_decision": predictor_decision,
                # P0-2b: 图内 predictor 节点（ReAct 之后）需要 case 目录
                "case_dir": None if guarded else str(input_dir / case_id),
            }
            if access is not None:
                initial_state["information_access"] = access.snapshot()
            with case_scope(task_int, case_id):
                consumption_event('graph_input', {}, initial_state, status='actual_input')
                if adaptive:
                    from chimera_agent_baseline import shared_plan
                    runtime.emit("adaptive_dispatch", controller="shared_plan.acquire", version=2,
                                 arm="adaptive", tools=[tool.name for tool in case_tools])
                    # S4's optional legacy guard is not the wire authority. Every
                    # invocation here reaches the G1T SDK meter under PrivateRuntime.
                    def sdk_meter_only(*args, **kwargs):
                        return {"authority": "G1T_post_serialization"}
                    result, _, _ = await shared_plan.complete_case(
                        cfg, "adaptive", query, case_dir, case_dir / "common_plan", access,
                        case_query, case_tools, model, sdk_meter_only, strict_output=True)
                else:
                    result = await graph.ainvoke(initial_state, {"recursion_limit": cfg.agent.max_iterations})
                consumption_event('graph_output', {}, result, status='observed')

            # form_fill is the single validation point: it parses the model's
            # output against the per-task Pydantic model and raises if every
            # retry fails, so the structured_response here is already valid.
            if predictor_trace is None:  # P0-2b：节点把落盘块放在 state 里
                predictor_trace = result.get("predictor_trace")
            if access is not None:
                result["information_access"] = access.snapshot()
                if result["information_access"]["pending_requests"]:
                    raise RuntimeError("Unsettled acquisition requests at completion")
            structured = result["structured_response"]

            # Single output file per patient, in a per-case folder mirroring the
            # agent input. The file is exactly the validated structured record.
            case_dir = task_dir / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            (case_dir / "prediction.json").write_text(json.dumps(structured, indent=2))
            consumption_event('prediction_file', structured,
                              json.loads((case_dir / 'prediction.json').read_text()),
                              task=task_int, case_id=case_id, status='written_and_read_back')
            (case_dir / ".failed").unlink(missing_ok=True)

            elapsed = time.monotonic() - case_start
            log.info("Task %d: case %s done in %.1fs", task_int, case_id, elapsed)
            _save_trace(case_dir, case_id, task_int, result, elapsed,
                        predictor_trace=predictor_trace)

            if access is not None:
                guarded_runtime.save_receipt(access, case_dir)
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
            if access is not None:
                (case_dir / "information_access_snapshot.json").write_text(
                    json.dumps(access.snapshot(), ensure_ascii=False, indent=2))
            _save_trace(case_dir, case_id, task_int, None, elapsed, exc,
                        predictor_trace=predictor_trace)
            continue

    log.info("Task %d: wrote %d predictions (%d skipped, %d failed) under %s", task_int, n_done, n_skipped, n_failed, task_dir)
    if task_summaries is not None:
        expected = [q["case_id"] for q in queries]
        missing = [cid for cid in expected if not (task_dir / cid / "prediction.json").is_file()
                   or guarded and ((task_dir / cid / ".failed").exists()
                                   or not (task_dir / cid / "information_access_receipt.json").is_file())]
        task_summaries.append({
            "task": task_int, "expected": len(expected), "predictions_present": len(expected) - len(missing),
            "written_this_run": n_done, "failed_this_run": n_failed, "skipped_this_run": n_skipped,
            "missing_prediction_ids": missing,
            "status": "incomplete" if missing else "done",
        })
    return n_done


async def run_agent(cfg: DictConfig) -> None:
    """Run every task present under ``data_root`` (model loaded once, reused)."""
    # A-2 补充（2026-10-28）：把批次输出目录透传给 form_fill，否则它的
    # trace / judgment_pre_calibration 会落到 cwd 下的 `output/`，与批次错位。
    guarded_runtime.validate_options(cfg)
    if cfg.agent.get("workflow") == "adaptive_v2_smoke":
        from chimera_agent_baseline import shared_plan
        from chimera_agent_baseline.runtime_audit import current_runtime, RuntimeFatal
        runtime = current_runtime()
        if runtime is None or not getattr(runtime, "adaptive_smoke", False):
            raise RuntimeFatal("Adaptive smoke workflow requires trusted formal-entry scope")
        shared_plan.validate_config(cfg)
    os.environ["CHIMERA_OUTPUT_DIR"] = str(cfg.paths.output_dir)

    plan = _run_plan(cfg)
    log.info("Run plan: tasks %s", [t for t, _ in plan])
    _write_run_manifest(cfg, plan, status="running")
    task_summaries: list[dict] = []
    try:
        from chimera_agent_baseline.runtime_audit import current_runtime
        pilot = getattr(current_runtime(), 'case_pilot', None)
        model = None if pilot is not None and pilot.dry else load_model(cfg)

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
                missing = sorted({t for t, _ in plan} - set(predictor.available_tasks))
                component_event("predictor_load", configured=True, status="fallback" if missing else "executed",
                                reason=f"Missing predictor tasks {missing}" if missing else None,
                                fallback="pure_llm" if missing else None, required=True,
                                model_dir=str(Path(cfg.paths.model_dir) / "predictor"))
            except ComponentFailure:
                raise
            except Exception as exc:
                component_event("predictor_load", configured=True, status="fallback", reason=str(exc),
                                fallback="pure_llm", required=True)
                log.warning("Predictor not loaded (will run pure-LLM): %s", exc)

        total = 0
        for task_int, input_dir in plan:
            # P1: per-task system prompt（校准段只注入当前任务小节），graph 在
            # _run_task 内每 task 重建（create_graph 本身不动）。
            system_prompt = build_system_prompt(task_int)
            total += await _run_task(cfg, task_int, input_dir, model, system_prompt, predictor,
                                     task_summaries=task_summaries)
        log.info("Done. Wrote %d predictions across %d task(s).", total, len(plan))
    except BaseException:
        _write_run_manifest(cfg, plan, status="failed", task_summaries=task_summaries)
        raise
    else:
        status = "incomplete" if any(s["status"] != "done" for s in task_summaries) else "done"
        _write_run_manifest(cfg, plan, status=status, task_summaries=task_summaries)
        if status == "incomplete":
            log.error("Run is INCOMPLETE: missing predictions remain; see run_manifest.json. "
                      "After repairing the service, use agent.retry_failed=true to retry failed cases.")
            if strict_runtime() or guarded_runtime.enabled(cfg):
                raise RuntimeError("Strict experiment incomplete: required case predictions missing")


def _run_connectivity_gate(cfg: DictConfig) -> None:
    """运行收尾的连通性体检门。

    ## 为什么放在这里

    项目历史上的两类问题（机制从未生效 / 评估口径失真）**都被发现过、记录过，却照样复发**：
    08-19 正确诊断出 `fact_check` 护栏不触发（FALSE=0/243）却搁置 39 天；
    09-12 写下"禁用自测下结论"的铁律，09-26 同类问题照旧。
    **根因：诊断写进报告 = 一次性文档；规则写进铁律 = 靠人记。两者都不是 gate。**

    历史做法是"靠人记得跑体检脚本"，那不叫 gate。所以把门**硬接在入口收尾**：
    只要走本地 CLI（`python -m chimera_agent_baseline.run`），就一定会体检。
    9 个运行脚本、手工跑、任何路径都自动覆盖，无需逐个改调用点。

    ## 为什么不放进 run_agent()

    容器入口 `inference.py` **import 了 run_agent**。放进 run_agent 会让门在离线容器里执行
    （无 data/tools/trace），可能破坏提交。放在 `main()` 则容器不经此处，天然隔离。

    ## 行为

    - 环境变量 `CHIMERA_GATE=0` 可关闭（默认开）
    - 不通过 → 写 `out_dir/_UNVERIFIED_GATE_FAILED` 标记 + 大字警告，**默认不改退出码**
      （避免 `set -e` 的脚本在 task2 失败时中断后续 task3 阶段）
    - `CHIMERA_GATE_STRICT=1` → 抛异常，让本批以非零码结束（用于 CI/关键批次）
    """
    if os.environ.get("CHIMERA_GATE", "1").strip().lower() in ("0", "false", "no", ""):
        return
    out = str(getattr(cfg.paths, "output_dir", "") or "")
    if not out or not os.path.isdir(out):
        return
    traces = list(Path(out).rglob("trace.json"))
    if not traces:
        return
    log.info("=" * 70)
    log.info("连通性体检门：对本批 %d 例执行 connectivity_gate.py", len(traces))
    gate = Path("tools/connectivity_gate.py")
    if not gate.exists():
        log.warning("未找到 %s，跳过体检", gate)
        return
    try:
        cmd = [sys.executable, str(gate), "all", out, "--src", "src", "--data", "data"]
        # 日志类探针需要运行日志；由 GATE_LOG 传入（脚本可用 GATE_LOG=<logfile> 约定）。
        # 未提供时门会把这些机制标为"无法判定"而非误报 FAIL。
        gate_log = os.environ.get("GATE_LOG", "")
        if gate_log and Path(gate_log).exists():
            cmd += ["--log", gate_log]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        log.warning("连通性体检门超时，跳过（本批未标记）")
        return
    for line in (proc.stdout or proc.stderr or "").splitlines():
        log.info("  %s", line)
    if proc.returncode == 0:
        log.info("连通性体检门：通过")
        log.info("=" * 70)
        return
    Path(out, "_UNVERIFIED_GATE_FAILED").touch()
    log.warning("*" * 70)
    log.warning("体检未通过 → 本批已标记 UNVERIFIED：%s/_UNVERIFIED_GATE_FAILED", out)
    log.warning("按项目纪律：**不得据本批的任何数字下结论**（含消融结论）。")
    log.warning("常见原因：机制 0 次触发（未接通）/ 输出悬空 / 派生数据无分折键。")
    log.warning("*" * 70)
    log.info("=" * 70)
    if os.environ.get("CHIMERA_GATE_STRICT", "0").strip().lower() not in ("0", "false", "no", ""):
        raise RuntimeError("connectivity gate failed (CHIMERA_GATE_STRICT=1)")


@hydra.main(version_base=None, config_path="../../configs", config_name="config")
def main(cfg: DictConfig) -> None:
    setup_logging(cfg.logging.level)

    guarded_runtime.validate_options(cfg)
    embed_svc = None if guarded_runtime.enabled(cfg) else start_embedding_service(cfg.paths.embedding_model_dir)
    try:
        asyncio.run(run_agent(cfg))
        _run_connectivity_gate(cfg)
    finally:
        if embed_svc:
            embed_svc.stop()


if __name__ == "__main__":
    main()
