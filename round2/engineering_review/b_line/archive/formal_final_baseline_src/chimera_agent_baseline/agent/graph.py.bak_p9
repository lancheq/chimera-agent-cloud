"""LangGraph Pro four-stage graph: plan → evidence → reflect → form_fill.

Implements the MedAgent-Pro two-layer workflow + Hippocrates-o1 self-refine
+ Atomic fact-checking guardrail. Four stages share one Qwen3.6-35B base
model with prompt-based role differentiation:

1. **plan**      -- Stage A: disease-level plan guides evidence gathering.
2. **agent/tools** -- Stage B: ReAct loop gathers evidence following the plan.
3. **reflect**   -- Stage C: Hippocrates Critique→Refine loop (max 2 iters).
4. **form_fill** -- Stage D: structured output + atomic fact check + reveal_sequence.

The agent loops between *agent* and *tools* until it stops issuing tool
calls. The router then routes to *reflect*, which runs self-refine on the
reasoning transcript, then to *form_fill* which produces the final
``state["structured_response"]`` for :mod:`chimera_agent_baseline.run`.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from chimera_agent_baseline.agent.form_fill import make_form_fill_node
from chimera_agent_baseline.agent.prompts import build_rag_injection
from chimera_agent_baseline.agent.self_refine import run_self_refine

log = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """State for the ReAct + form-fill graph.

    ``messages`` and the ``add_messages`` reducer are the LangGraph ReAct
    primitives. The other fields carry per-case context for the form-fill
    node:

    * ``task`` — 1 (biopsy decision) or 2 (treatment decision). Drives
      which set of reasoning variables / Pydantic model is used.
    * ``case_id`` — included in the structured response.
    * ``patient`` — raw per-case fields (``psa``, ``age``) read from
      ``prompt.json`` by the case loader, used by ``form_fill`` to
      populate the output record's ``patient`` object.
    * ``predictor_decision`` — deterministic predictor's decision for this
      case (populated by the runner when ``agent.predictor.enabled``), read
      by ``form_fill`` to ground the LLM's reasoning trace and optionally
      override the final decision fields.
    * ``structured_response`` — populated by ``form_fill`` and read by
      :mod:`chimera_agent_baseline.run` after graph completion.
    * ``form_fill_warnings`` — diagnostics (validation retries, post-hoc
      downgrades). Empty list when everything is clean.
    """

    messages: Annotated[list, add_messages]
    task: int
    case_id: str
    patient: dict[str, Any]
    predictor_decision: dict[str, Any]
    structured_response: dict[str, Any]
    form_fill_warnings: list[str]
    # Pro four-stage fields
    disease_plan: dict[str, Any]        # Stage A output: evidence gathering plan
    refined_transcript: str             # Stage C output: refined reasoning
    reflect_warnings: list[str]         # Stage C diagnostics


def _route_after_agent(state: AgentState) -> str:
    """If the last message has tool calls, run them; otherwise go to reflect."""
    return "tools" if tools_condition(state) == "tools" else "reflect"


# ---------------------------------------------------------------------------
# Stage A: Disease-level plan (MedAgent-Pro two-layer workflow)
# ---------------------------------------------------------------------------

# Inline disease plans (expert priors for MOE + evidence gathering strategy)
_DISEASE_PLANS: dict[int, dict[str, Any]] = {
    1: {
        "name": "prostate_biopsy_decision",
        "evidence_order": ["get_mri_report", "get_pathology_report", "get_psa_trend",
                           "get_lab_results", "get_family_history", "search_guidelines"],
        "thresholds": {"biopsy_indicated": "PI-RADS>=4 OR (PSA>3 AND PSAD>=0.15)",
                       "gray_zone": "PI-RADS 3 OR (PSA 3-10 AND PSAD 0.10-0.20)"},
        "expert_priors": {"pirads": 0.30, "psad": 0.25, "psa": 0.15, "bx_isup": 0.15,
                          "age": 0.05, "fh": 0.05, "cspca": 0.05},
    },
    2: {
        "name": "prostate_treatment_decision",
        "evidence_order": ["get_pathology_report", "get_mri_report", "get_psa_trend",
                           "get_previous_notes", "get_family_history", "search_guidelines"],
        "thresholds": {"active_surveillance": "ISUP GG 1-2 AND PSA<10 AND PI-RADS<=3",
                       "active_treatment": "ISUP>=2 (confirmed) OR PI-RADS 4-5 with high PSAD"},
        "expert_priors": {"bx_isup": 0.30, "pirads": 0.20, "psad": 0.15, "psa": 0.10,
                          "age": 0.10, "ct": 0.05, "fh": 0.05, "comorbidity": 0.05},
    },
    3: {
        "name": "prostate_recurrence_prediction",
        "evidence_order": ["get_mri_report", "get_surgical_pathology_report",
                           "get_pathology_report", "get_previous_notes", "get_family_history"],
        "thresholds": {"recurrence_rp": "POST-treatment PSA>0.2 ng/mL (confirmed; NOT applicable to baseline PSA shown in data)",
                       "recurrence_rt": "Phoenix: nadir+2 ng/mL"},
        "expert_priors": {"psa": 0.35, "bx_isup": 0.20, "pirads": 0.15, "psad": 0.10,
                          "age": 0.05, "ct": 0.05, "cspca": 0.10},
    },
}

_PLAN_SYSTEM = (
    "You are a clinical evidence planning assistant (MedAgent-Pro planner role). "
    "Given a prostate cancer case, produce a concise evidence-gathering plan AND "
    "decide which MCP tools to call. List 3-5 key pieces of evidence to retrieve, "
    "the clinical question each addresses, and which decision threshold it informs. "
    "Do NOT make the clinical decision — only plan what evidence to gather.\n\n"
    "TOOL SELECTION RULES — be selective, not exhaustive:\n"
    "1. Only call a tool when the result MIGHT change the clinical decision.\n"
    "2. If a variable is already in the prompt context (headline PSA, PI-RADS, "
    "PSA density, DRE, prior biopsy status, age, prostate volume, csPCa probability), "
    "you do NOT need to call a tool just to re-reveal it. Call a tool only when "
    "you need the detailed report behind the headline value.\n"
    "3. get_mri_report: call when PI-RADS >= 3 and you need the full radiology "
    "report. Skip when PI-RADS 1-2 and PSA is normal.\n"
    "4. get_psa_trend: call when PSA kinetics might influence the decision "
    "(rising PSA, borderline PSA 3-10). Skip when PSA is clearly normal (<3) "
    "or clearly very high (>20).\n"
    "5. get_pathology_report: call when prior biopsy exists (bx != None) and "
    "you need the full pathology report. Skip when patient is biopsy-naïve.\n"
    "6. get_lab_results: call when you need the full lab panel beyond headline "
    "PSA. Skip when headline PSA is sufficient.\n"
    "7. get_previous_notes: call when you need clinical context from prior "
    "visits. Skip when the current encounter context is sufficient.\n"
    "8. get_family_history: call when family history might shift a borderline "
    "decision. Skip when the decision is clear either way.\n"
    "9. get_surgical_pathology_report (Task 3 only): call when prior surgery "
    "exists.\n\n"
    "DYNAMIC TOOL SELECTION (override defaults based on clinical context):\n"
    "Task 1 (biopsy decision):\n"
    "- PSA 3-10 AND PI-RADS 3 (gray zone): TOOLS: [\"get_mri_report\", \"get_psa_trend\", \"get_lab_results\", \"get_previous_notes\", \"get_family_history\"]\n"
    "- PSA > 100 (very high): TOOLS: [\"get_mri_report\", \"get_lab_results\"]\n"
    "- Prior biopsy negative: TOOLS: [\"get_mri_report\", \"get_psa_trend\", \"get_pathology_report\"]\n"
    "- Default: TOOLS: [\"get_mri_report\", \"get_psa_trend\"]\n"
    "Task 2 (treatment decision):\n"
    "- PI-RADS >= 4 AND PSA > 20: TOOLS: [\"get_mri_report\", \"get_pathology_report\", \"get_lab_results\"]\n"
    "- AS candidate (PSA < 10, PI-RADS <= 3, ISUP 1): TOOLS: [\"get_mri_report\", \"get_psa_trend\", \"get_previous_notes\"]\n"
    "- Default: TOOLS: [\"get_mri_report\", \"get_pathology_report\"]\n"
    "Task 3 (recurrence prediction):\n"
    "- Always: TOOLS: [\"get_mri_report\", \"get_surgical_pathology_report\", \"get_psa_trend\"]\n"
    "- If post-RP: TOOLS: [\"get_surgical_pathology_report\", \"get_psa_trend\", \"get_lab_results\"]\n"
    "- If post-RT: TOOLS: [\"get_mri_report\", \"get_psa_trend\", \"get_lab_results\"]\n\n"
    "Rule 10: If prior biopsy is non-empty (bx_history != []), prioritize "
    "get_pathology_report first. This resolves the T1 conflict where an "
    "already-diagnosed case would still trigger initial biopsy per EAU guidelines.\n"
    "Rule 11: For Task 3, if surgical history exists, prioritize "
    "get_surgical_pathology_report to retrieve surgical pathology before other "
    "evidence.\n\n"
    "IMPORTANT: Some variables like DRE and prior biopsy status (bx) are in the "
    "prompt context but have NO dedicated MCP tool. Do not attempt to call tools "
    "for them — assess them from the prompt context directly.\n\n"
    "After your plan text, on the LAST line, output the tools to call as a JSON "
    "array. Output [] if no tools are needed. Examples:\n"
    "Plan: ...\nTOOLS: [\"get_mri_report\", \"get_psa_trend\"]\n"
    "Plan: ...\nTOOLS: []"
)


def _parse_tool_list(text: str, available: list[str]) -> list[str]:
    """Parse tool names from the LLM plan response's TOOLS: line.

    Looks for a JSON array following ``TOOLS:`` on the last meaningful line.
    Falls back to scanning for known tool names in the text.
    """
    # Strategy 1: look for "TOOLS:" prefix
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("TOOLS:"):
            json_part = line[len("TOOLS:"):].strip()
            try:
                parsed = json.loads(json_part)
                if isinstance(parsed, list):
                    return [str(t) for t in parsed if str(t) in available]
            except json.JSONDecodeError:
                pass
            break

    # Strategy 2: find any JSON array in the text
    import re
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return [str(t) for t in parsed if str(t) in available]
        except json.JSONDecodeError:
            pass

    # Strategy 3: scan for known tool names
    found: list[str] = []
    for tool_name in available:
        if tool_name in text:
            found.append(tool_name)
    return found


# ── Step 9: case-based few-shot retrieval for T1 ──────────────────────────

_T1_CASE_INDEX: list[dict] | None = None
_T1_INDEX_FEATURE_NAMES: list[str] | None = None
_T1_INDEX_MEANS: list[float] | None = None
_T1_INDEX_STDS: list[float] | None = None


def _load_t1_case_index() -> bool:
    """Lazily load T1 case index from resources/case_index/t1_case_index.json."""
    global _T1_CASE_INDEX, _T1_INDEX_FEATURE_NAMES, _T1_INDEX_MEANS, _T1_INDEX_STDS
    if _T1_CASE_INDEX is not None:
        return True
    idx_path = Path(__file__).resolve().parents[3] / "resources" / "case_index" / "t1_case_index.json"
    if not idx_path.exists():
        return False
    data = json.load(open(idx_path))
    _T1_CASE_INDEX = data["cases"]
    _T1_INDEX_FEATURE_NAMES = data["feature_names"]
    _T1_INDEX_MEANS = data["means"]
    _T1_INDEX_STDS = data["stds"]
    log.info("Loaded T1 case index: %d cases", len(_T1_CASE_INDEX))
    return True


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return dot / (na * nb)


def _build_t1_fewshot(case_context: str, case_id: str, k: int = 3) -> str:
    """Build top-k similar GT few-shot examples for T1, excluding self."""
    if not _load_t1_case_index():
        return ""
    # Extract features from case context (parse structured-prompt fields)
    # Fallback: use case_id to find features in the index itself (for GT cases)
    query_feats = None
    # Try to parse clinical indicators from context
    import re
    psa = re.search(r'"psa":\s*([\d.]+)', case_context)
    psad = re.search(r'"psad":\s*([\d.]+)', case_context)
    age = re.search(r'"age":\s*(\d+)', case_context)
    vol = re.search(r'"vol":\s*([\d.]+)', case_context)
    pirads = re.search(r'"pirads":\s*"?(\d)"?', case_context)
    cspca = re.search(r'"cspca":\s*([\d.]+)', case_context)
    psav = re.search(r'"psav":\s*([\d.]+)', case_context)
    psap = re.search(r'"psap":\s*([\d.]+)', case_context)

    pirads_map = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
    def _f(m, d=0.0):
        return float(m.group(1)) if m else d

    if psa or psad or pirads:
        query_feats = [
            _f(psa), _f(psad), _f(age), _f(vol),
            float(pirads_map.get(pirads.group(1) if pirads else "0", 0)),
            _f(cspca), _f(psav), _f(psap),
        ]
        # Normalize
        means, stds = _T1_INDEX_MEANS, _T1_INDEX_STDS
        query_norm = [
            (f - m) / s if s and s > 1e-9 else 0.0
            for f, m, s in zip(query_feats, means, stds)
        ]
    else:
        # Fallback: look up by case_id in the index
        for c in _T1_CASE_INDEX:
            if c["case_id"] == case_id:
                query_norm = c.get("features_norm", c["features"])
                break
        else:
            return ""

    # Compute similarity, exclude self
    sims = []
    for c in _T1_CASE_INDEX:
        if c["case_id"] == case_id:
            continue
        s = _cosine_sim(query_norm, c.get("features_norm", c["features"]))
        sims.append((s, c))
    sims.sort(reverse=True)

    examples = []
    for s, c in sims[:k]:
        cs = c.get("clinical_summary", {})
        examples.append(
            f"  Case {c['case_id']}: PSA={cs.get('psa','?')} PSAD={cs.get('psad','?')} "
            f"age={cs.get('age','?')} PI-RADS={cs.get('pirads','?')} vol={cs.get('vol','?')} "
            f"→ biopsy_decision={c['gt_decision']} (confidence={c.get('gt_confidence','?')})"
        )
    if not examples:
        return ""
    return (
        "\n\n[相似病例参考 (top-3 GT)]\n"
        "以下为临床特征最相似的历史病例及其穿刺决策，供参考（非本例结论）：\n"
        + "\n".join(examples)
        + "\n请独立判断本例。"
    )


# ── End step 9 additions ────────────────────────────────────────────────────


# ── W5-T2: forced gray-zone retrieval for T1 ───────────────────────────────

def _t1_gray_zone(context: str) -> tuple[bool, dict[str, Any]]:
    """Detect T1 gray-zone cases: PSA 3-10 ng/mL OR PI-RADS 3-4.

    Parses both the narrative rendering (``- PSA: 5.2 ng/mL``) and the raw
    structured-prompt JSON (``"psa": 5.2``) so it works regardless of which
    form the case context takes. Returns ``(is_gray_zone, features)``.
    """
    import re

    psa: float | None = None
    pirads: float | None = None

    m = re.search(r"^\s*-\s*PSA:\s*([\d.]+)\s*ng/mL", context, re.MULTILINE) \
        or re.search(r'"psa":\s*([\d.]+)', context)
    if m:
        psa = float(m.group(1))
    m = re.search(r"PI-RADS score:\s*(\d)", context) \
        or re.search(r'"pirads":\s*"?(\d)"?', context)
    if m:
        pirads = float(m.group(1))

    features: dict[str, Any] = {"psa": psa, "pirads": pirads}
    psa_gray = psa is not None and 3.0 <= psa <= 10.0
    pirads_gray = pirads is not None and pirads in (3.0, 4.0)
    return (psa_gray or pirads_gray), features


_GRAYZONE_QUERY = (
    "EAU prostate cancer biopsy indication for gray-zone cases: "
    "PI-RADS 3 equivocal lesion management, PSA density threshold 0.15, "
    "PSA 3-10 ng/mL biopsy recommendation"
)

# Fallback query when the plan node executes search_guidelines outside the
# forced gray-zone path (e.g. the LLM selected it for T3). The tool requires
# a non-empty `query` — the old per-case-only invocation silently failed.
_DEFAULT_GL_QUERY = {
    1: "EAU biopsy indication thresholds PI-RADS PSA density",
    2: "EAU treatment recommendation active surveillance criteria ISUP grade group",
    3: "biochemical recurrence prediction post-prostatectomy PSA threshold Phoenix criteria",
}


def _make_plan_node(
    model: BaseChatModel,
    tools: list[BaseTool] | None = None,
    force_rag_grayzone: bool = False,
):
    """Stage A: generate a case-specific evidence gathering plan + execute selected tools.

    The plan node does two things:
    1. Generates a text plan via LLM that also specifies which tools to call
       based on clinical necessity (not fixed mandatory list).
    2. Executes the LLM-selected tools directly and injects results as
       ToolMessages, so the agent sees the revealed sections.

    This replaces the old fixed mandatory-tools approach where every case
    called the same 2 tools regardless of clinical context.
    """
    tool_by_name = {t.name: t for t in tools} if tools else {}

    def plan_node(state: AgentState) -> dict[str, Any]:
        task = int(state.get("task", 1))
        case_id = state.get("case_id", "unknown")
        disease_plan = _DISEASE_PLANS.get(task, {})

        # --- Generate plan + tool selection via LLM ---
        messages = state.get("messages", [])
        case_context = ""
        for m in messages[-3:]:
            content = m.content if hasattr(m, "content") else str(m)
            case_context += str(content)[:500] + "\n"

        # Build tool descriptions for the LLM
        candidate_tool_names = list(tool_by_name.keys())
        tool_descriptions = "\n".join(
            f"- {name}: {tool_by_name[name].description}"
            for name in candidate_tool_names
        ) if candidate_tool_names else "(no tools available)"

        try:
            user_msg = (
                f"Case ID: {case_id} (Task {task})\n"
                f"Disease plan: {disease_plan.get('name', 'unknown')}\n"
                f"Decision thresholds: {disease_plan.get('thresholds', {})}\n\n"
                f"Available tools:\n{tool_descriptions}\n\n"
                f"Case context:\n{case_context[:2000]}\n\n"
                "Produce a 3-5 step evidence gathering plan for this case.\n"
                "Then on the LAST line, output the tools to call as:\n"
                "TOOLS: [\"tool_name1\", \"tool_name2\"]\n"
                "Or TOOLS: [] if no tools are needed.\n"
                "Only call tools when the result might change the clinical decision."
            )
            # T3 only: inject predictor quantitative reference (CV-validated, not training-set)
            if task == 3:
                pred = state.get("predictor_decision", {})
                if pred:
                    user_msg += (
                        f"\n\n[定量模型参考] event={pred.get('event')}, "
                        f"months={pred.get('months_to_recurrence')}"
                        "（5折CV C-index=0.82）"
                    )
            # Step 9: T1 only — inject top-3 similar GT few-shot (exclude self)
            if task == 1:
                fewshot = _build_t1_fewshot(case_context, case_id)
                if fewshot:
                    user_msg += fewshot
            resp = model.invoke([
                SystemMessage(content=_PLAN_SYSTEM),
                HumanMessage(content=user_msg),
            ])
            plan_text = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
        except Exception as exc:  # noqa: BLE001
            plan_text = f"Plan generation failed: {exc}. Using minimal evidence."
            log.warning("plan_node failed for %s: %s", case_id, exc)

        # --- Parse which tools the LLM selected ---
        selected_tools = _parse_tool_list(plan_text, candidate_tool_names)

        # 修复 7: 强制非空校验 — LLM 输出空工具时回退到默认
        _task_defaults = {
            1: ["get_mri_report", "get_psa_trend"],
            2: ["get_mri_report", "get_pathology_report"],
            3: ["get_mri_report", "get_surgical_pathology_report", "get_psa_trend", "search_guidelines"],
        }
        if not selected_tools:
            default_tools = _task_defaults.get(task, [])
            selected_tools = [t for t in default_tools if t in tool_by_name]
            log.warning("plan_node: %s LLM output empty tools, fallback to %s", case_id, selected_tools)

        # 修复 6: T3 强制包含 get_mri_report + get_surgical_pathology_report + search_guidelines
        _t3_mandatory = ["get_mri_report", "get_surgical_pathology_report", "search_guidelines"]
        if task == 3:
            for mt in _t3_mandatory:
                if mt in tool_by_name and mt not in selected_tools:
                    selected_tools.append(mt)
                    log.info("plan_node: %s force-adding mandatory tool %s", case_id, mt)

        # W5-T2: T1 gray-zone cases force a search_guidelines call BEFORE the
        # agent reasons (PSA 3-10 OR PI-RADS 3-4). Config-gated via
        # agent.force_rag_grayzone — default OFF (control runs unaffected).
        gray_zone = False
        gray_features: dict[str, Any] = {}
        forced_rag_injection = ""
        if force_rag_grayzone and task == 1 and "search_guidelines" in tool_by_name:
            human_ctx = next(
                (str(m.content) for m in messages if getattr(m, "type", "") == "human"),
                case_context,
            )
            gray_zone, gray_features = _t1_gray_zone(human_ctx)
            if gray_zone:
                if "search_guidelines" not in selected_tools:
                    selected_tools.insert(0, "search_guidelines")
                    log.info("plan_node: %s gray zone %s — forced search_guidelines first",
                             case_id, gray_features)
                else:
                    log.info("plan_node: %s gray zone %s — search_guidelines already selected",
                             case_id, gray_features)

        log.info("plan_node: %s plan generated (%d chars), selected tools: %s",
                 case_id, len(plan_text), selected_tools)

        # --- Execute selected tools ---
        from langchain_core.messages import ToolMessage

        new_messages: list = []
        executed_tools: list[str] = []

        import asyncio
        for tool_name in selected_tools:
            if tool_name not in tool_by_name:
                continue
            tool = tool_by_name[tool_name]
            tool_call_id = f"plan_{case_id}_{tool_name}"
            # search_guidelines needs a clinical question — the old
            # case_id-only invocation failed silently (missing `query`).
            tool_input: dict[str, Any] = {"case_id": case_id}
            if tool_name == "search_guidelines":
                tool_input["query"] = (
                    _GRAYZONE_QUERY if (task == 1 and gray_zone)
                    else _DEFAULT_GL_QUERY.get(task, _DEFAULT_GL_QUERY[1])
                )
            try:
                # MCP tools are async-only (StructuredTool does not support sync)
                try:
                    result = asyncio.get_event_loop().run_until_complete(
                        tool.ainvoke(tool_input)
                    )
                except RuntimeError:
                    # No event loop in this thread — create one
                    loop = asyncio.new_event_loop()
                    result = loop.run_until_complete(
                        tool.ainvoke(tool_input)
                    )
                    loop.close()
                new_messages.append(ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call_id,
                    name=tool_name,
                ))
                executed_tools.append(tool_name)
                if tool_name == "search_guidelines" and gray_zone:
                    # Pass the raw result — build_rag_injection normalizes
                    # every adapter shape (JSON string / dict / hit list /
                    # content-block list) internally.
                    forced_rag_injection = build_rag_injection(result)
                log.info("plan_node: %s executed %s (result %d chars)",
                         case_id, tool_name, len(str(result)))
            except Exception as exc:  # noqa: BLE001
                new_messages.append(ToolMessage(
                    content=f"Error: {exc}",
                    tool_call_id=tool_call_id,
                    name=tool_name,
                ))
                log.warning("plan_node: %s tool %s failed: %s", case_id, tool_name, exc)

        # Inject plan as system message + note which tools were called
        plan_message = SystemMessage(
            content=f"[Evidence Gathering Plan]\n{plan_text}\n\n"
                    "Follow this plan. The evidence tools above have been called "
                    "for you — review their results, then reason about the clinical "
                    "decision. You may call additional tools if needed.\n\n"
                    f"Tools already called: {executed_tools if executed_tools else '(none — case context was sufficient)'}\n\n"
                    "Note: Variables like DRE and prior biopsy status (bx) are in the "
                    "prompt context already — they have no dedicated MCP tool. Assess "
                    "them from the prompt context directly."
        )
        new_messages.insert(0, plan_message)

        # W5-T2: inject the forced retrieval's QA/chunk hits as a system-level
        # reference block, so the agent reasons over ready-made guideline text.
        if forced_rag_injection:
            new_messages.insert(1, SystemMessage(content=forced_rag_injection))

        log.info("plan_node: %s executed %d tools: %s",
                 case_id, len(executed_tools), executed_tools)

        # === TRACE DUMP（不影响主流程，失败不报错）===
        import os as _os, json as _json, time as _time
        try:
            _trace_dir = _os.path.join(_os.environ.get("CHIMERA_OUTPUT_DIR", "output"), "trace", str(case_id))
            _os.makedirs(_trace_dir, exist_ok=True)
            with open(_os.path.join(_trace_dir, "plan.json"), "w") as f:
                _json.dump({"ts": _time.time(), "plan_text": plan_text[:200], "plan_full_len": len(plan_text),
                            "selected_tools": selected_tools, "executed_tools": executed_tools,
                            "gray_zone": gray_zone, "gray_features": gray_features,
                            "rag_injection_len": len(forced_rag_injection)}, f, ensure_ascii=False)
            with open(_os.path.join(_trace_dir, "plan_full.txt"), "w") as f:
                f.write(plan_text)
        except Exception:
            pass

        return {
            "messages": new_messages,
            "disease_plan": disease_plan,
        }

    return plan_node


# ---------------------------------------------------------------------------
# Stage C: Hippocrates-o1 self-reflection (Critique → Refine)
# ---------------------------------------------------------------------------

def _make_reflect_node(model: BaseChatModel, max_iterations: int = 2):
    """Stage C: run Critique→Refine on the agent's reasoning transcript."""

    def reflect_node(state: AgentState) -> dict[str, Any]:
        task = int(state.get("task", 1))
        case_id = state.get("case_id", "unknown")
        messages = state.get("messages", [])

        # Extract transcript (all assistant messages after the plan)
        transcript_parts: list[str] = []
        for m in messages:
            content = m.content if hasattr(m, "content") else str(m)
            if hasattr(m, "type") and m.type == "ai":
                transcript_parts.append(str(content))
            elif not hasattr(m, "type"):
                transcript_parts.append(str(content))
        transcript = "\n---\n".join(transcript_parts)

        if not transcript.strip():
            log.warning("reflect_node: %s empty transcript, skipping", case_id)
            return {"refined_transcript": "", "reflect_warnings": ["empty transcript"]}

        # Extract draft decision from last assistant message
        draft_decision = ""
        if messages:
            last_msg = messages[-1]
            draft_decision = str(last_msg.content)[:200] if hasattr(last_msg, "content") else ""

        refined, warnings = run_self_refine(
            model=model,
            task=task,
            case_id=case_id,
            transcript=transcript,
            draft_decision=draft_decision,
            max_iterations=max_iterations,
        )

        log.info("reflect_node: %s done, %d warnings", case_id, len(warnings))

        # === TRACE DUMP（不影响主流程，失败不报错）===
        import os as _os, json as _json, time as _time
        try:
            _trace_dir = _os.path.join(_os.environ.get("CHIMERA_OUTPUT_DIR", "output"), "trace", str(case_id))
            _os.makedirs(_trace_dir, exist_ok=True)
            with open(_os.path.join(_trace_dir, "reflect.json"), "w") as f:
                _json.dump({"ts": _time.time(), "warnings": warnings,
                            "refined_transcript_preview": refined[:500],
                            "refined_transcript_len": len(refined)}, f, ensure_ascii=False)
            with open(_os.path.join(_trace_dir, "refined_full.txt"), "w") as f:
                f.write(refined)
        except Exception:
            pass

        return {
            "refined_transcript": refined,
            "reflect_warnings": warnings,
        }

    return reflect_node


def create_graph(
    tools: list[BaseTool],
    model: BaseChatModel,
    system_prompt: str,
    step_timeout: int = 120,
    form_fill_max_retries: int = 3,
    decision_override: bool = False,
    self_refine: bool = False,
    atomic_check: bool = False,
    self_refine_max_iter: int = 2,
    force_rag_grayzone: bool = False,
):
    """Build and compile the Pro four-stage graph: plan → evidence → reflect → form_fill.

    All four stages share the same Qwen3.6-35B base model with prompt-based
    role differentiation:

    * **Stage A (plan)**: disease-level plan guides evidence gathering.
    * **Stage B (agent/tools)**: ReAct loop gathers evidence following the plan.
    * **Stage C (reflect)**: Hippocrates Critique→Refine loop (max iterations).
    * **Stage D (form_fill)**: structured output + atomic fact check.

    *decision_override* forwards to form_fill: when enabled and a
    ``predictor_decision`` is in state, gate decisions are taken from the
    deterministic predictor.

    *self_refine* enables Stage C (reflect node).

    *atomic_check* enables programmatic fact verification in form_fill.
    """
    model_with_tools = model.bind_tools(tools)

    def agent(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt), *messages]
        response = model_with_tools.invoke(messages)
        return {"messages": [response]}

    builder = StateGraph(AgentState)

    # Stage A: plan (with tools for mandatory tool call injection)
    builder.add_node("plan", _make_plan_node(model, tools, force_rag_grayzone=force_rag_grayzone))

    # Stage B: evidence gathering (ReAct loop)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(tools))

    # Stage C: reflect (self-refine)
    if self_refine:
        builder.add_node("reflect", _make_reflect_node(model, max_iterations=self_refine_max_iter))

    # Stage D: form_fill
    builder.add_node(
        "form_fill",
        make_form_fill_node(
            model,
            max_retries=form_fill_max_retries,
            decision_override=decision_override,
            self_refine=False,  # self_refine now handled by reflect node
            atomic_check=atomic_check,
        ),
    )

    # Wire the four stages: plan (selects + executes tools dynamically) → agent → ...
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "agent")  # Plan injects ToolMessages directly
    builder.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "tools", "reflect": "reflect" if self_refine else "form_fill"},
    )
    builder.add_edge("tools", "agent")
    if self_refine:
        builder.add_edge("reflect", "form_fill")
    builder.add_edge("form_fill", END)

    graph = builder.compile()
    graph.step_timeout = step_timeout
    log.info("Compiled ReAct+form_fill graph with %d tools", len(tools))
    return graph
