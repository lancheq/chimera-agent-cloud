"""Hippocrates-o1 self-reflection module — Critique → Refine loop.

Implements the Stage C reflect node of the Pro four-stage graph.
After the evidence-gathering ReAct loop produces a reasoning transcript,
this module runs a Critique→Refine cycle:

1. **Critique**: an LLM call in the "senior reviewer" role audits the
   agent's draft reasoning for over-treatment bias, unsupported claims,
   confidence miscalibration, and fabricated facts.
2. **Refine**: if issues are found, a second LLM call produces a corrected
   reasoning trace. At most ``max_iterations`` cycles run (default 2) to
   prevent infinite loops.

The module is provider-neutral — any LangChain ``BaseChatModel`` works.
No new model weights are loaded; the same Qwen3.6-35B base is used with
prompt-based role differentiation.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from chimera_agent_baseline.agent.trace_ids import trace_case_id as _trace_case_id

log = logging.getLogger(__name__)

_CRITIQUE_SYSTEM = """\
You are a clinical reasoning auditor. Your ONLY output is a JSON object.

ABSOLUTE RULES (violation = task failure):
1. Do NOT output a thinking process.
2. Do NOT start with "Here's" or "Let's" or "I need to".
3. Do NOT repeat or restate the user's instructions.
4. The FIRST character of your output MUST be '{'.
5. Output ONLY: {"issues": ["<specific issue with evidence>", ...], "severity": "none|mild|moderate|severe"}

If you find no issues, output: {"issues": [], "severity": "none"}

Check for these 10 issue types:
1. fabricated_facts — values cited in reasoning that are NOT in the evidence transcript
2. unsupported_weights — variable_weights rated 'decisive' or 'important' without supporting evidence
3. over_treatment — recommending biopsy/active_treatment when evidence supports conservative management (PSAD < 0.15, PI-RADS <= 3, ISUP GG 1)
4. under_treatment — recommending no action when clear high-risk features exist (PI-RADS 5, ISUP >= 3, PSAD > 0.5)
5. confidence_miscalibration — confidence too high/low for the evidence strength
6. internal_inconsistency — weights or conclusion contradicts the cited evidence
7. missing_guideline — borderline case (PSA 3-10, PI-RADS 3, PSAD 0.10-0.20) with no EAU/NCCN guideline reference
8. imaging_pathology_mismatch — MRI PI-RADS and biopsy ISUP grade disagree (e.g., PI-RADS 5 but ISUP 1) without explanation
9. psa_volume_inconsistency — PSA density claimed but not matching PSA/prostate_volume
10. treatment_biopsy_confusion — T1 (biopsy decision) confused with T2 (treatment decision)

Be specific — cite the exact value, field, or sentence that is wrong.

EXAMPLES:

Case: PSA 5.2, PI-RADS 3, PSAD 0.151, prior negative biopsy. Agent recommended biopsy with confidence "clear".
{"issues": ["PI-RADS 3 is equivocal and PSAD 0.151 is just above the 0.15 threshold — this is a borderline case, not 'clear' confidence", "Prior negative biopsy reduces the pre-test probability, but the agent did not weigh this factor"], "severity": "moderate"}

Case: PSA 12.0, PI-RADS 5, ISUP GG 4, PSAD 0.35. Agent recommended active surveillance with confidence "clear".
{"issues": ["PI-RADS 5 + ISUP GG 4 indicates high-risk csPCa — active surveillance is under-treatment", "PSAD 0.35 well above 0.15 threshold supports definitive treatment, not surveillance", "Confidence 'clear' is inappropriate given the high-risk features"], "severity": "severe"}\
"""

_REFINE_SYSTEM = """\
You are revising a clinical decision-support agent's reasoning trace based \
on reviewer critique. Produce a corrected reasoning trace that addresses \
every issue raised in the critique.

Rules:
- Do NOT introduce new facts that were not in the original evidence \
transcript.
- Do NOT change the decision unless the critique specifically identifies a \
decision-level error.
- Keep the reasoning concise (3-5 paragraphs max).
- End with a clear statement of the decision and the 2-4 key factors.
- Do NOT repeat these instructions or output a thinking process. \
Output only the corrected reasoning trace.\
"""


def _coerce_issues(obj: Any) -> list[str] | None:
    """校验 obj 的 issues 字段：非 dict / 无 issues / issues 非 list → 不采信。

    返回规整后的 issue 字符串列表（可为空列表 = 有效"无问题"结论）；
    结构不合法返回 None。
    """
    if not isinstance(obj, dict):
        return None
    if "issues" not in obj:
        return None
    issues = obj.get("issues")
    if isinstance(issues, str):
        issues = [issues]
    if not isinstance(issues, list):
        return None
    return [s.strip() for s in issues if isinstance(s, str) and s.strip()]


def _extract_tail_critique_json(raw: str) -> tuple[str, list[str]] | None:
    """从 raw 尾部反向提取最后一个有效的 critique JSON。

    S1：thinking 前缀场景下模型常在末尾输出完整
    {"issues": [...], "severity": ...}，而 2A 前置过滤把整个输出误判为
    污染直接 return (raw, [])，把尾部 JSON 全部错杀（消融 B 217 个
    critique 全部命中该 bug，refine 0% 触发的唯一根因）。

    本函数反向扫描所有平衡 {...} 块，取**最后一个**可解析且 issues 为
    list 的块采信：
      - thinking 内嵌的反引号示例片段（如 `{"issues": [], "severity":
        "none"}`）位置更靠前，反向扫描自然跳过；
      - 尾部恰好被反引号包裹的真实输出（模型误包裹）同样可解析；
      - ```json ... ``` fenced 块内的 {...} 也能被反向扫描捕获。
    返回 (clean_json, issues)；无任何可采信块返回 None（保持原降级语义）。
    """
    end = len(raw)
    while True:
        start = raw.rfind("{", 0, end)
        if start < 0:
            return None
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(raw[start : i + 1])
                    except (json.JSONDecodeError, AttributeError):
                        break
                    issues = _coerce_issues(obj)
                    if issues is not None:
                        return json.dumps(obj, ensure_ascii=False), issues
                    break
        end = start  # 当前候选不可采信，继续向前找下一个 {


def _parse_critique_json(raw: str) -> tuple[str, list[str]]:
    """Parse the critique LLM output into (clean_text, issues_list).

    Expected JSON format:
        {"issues": ["issue1", "issue2", ...], "severity": "none|mild|moderate|severe"}

    Falls back to extracting issues from raw text if JSON parsing fails.
    Returns (raw_text, []) when no issues are found.

    修复 2: 前置过滤 thinking process 污染，避免无效 critique 触发 refine。
    """
    raw = raw.strip()

    # === 修复 2A: 前置过滤 thinking process 污染 ===
    thinking_patterns = [
        "here's a thinking process",
        "here is a thinking process",
        "analyze user input",
        "let's analyze the provided",
        "the user wants me to audit",
        "i need to check for:",
        "i'll analyze",
        "let me review",
    ]
    raw_lower = raw.lower()[:200]
    for pattern in thinking_patterns:
        if pattern in raw_lower:
            # S1: thinking 前缀不再直接判废——模型常在尾部补出完整 JSON。
            # 提取最后一个有效 critique JSON（反向扫描、跳过内联示例片段），
            # 解析成功且 issues 为 list → 采信（含空 issues = 有效"无问题"）；
            # 尾部确无 JSON → 才走原降级语义。
            extracted = _extract_tail_critique_json(raw)
            if extracted is not None:
                clean, issues = extracted
                if issues:
                    log.warning(
                        "critique has thinking prefix but tail JSON recovered (%d issues)",
                        len(issues),
                    )
                return clean, issues
            log.warning("critique contaminated by thinking process, treating as no issues")
            return raw, []

    # Try to extract a JSON object from the output
    json_str = raw
    # Handle markdown-fenced JSON
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        json_str = fence.group(1)
    else:
        start = raw.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(raw)):
                if raw[i] == "{":
                    depth += 1
                elif raw[i] == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = raw[start : i + 1]
                        break

    try:
        obj = json.loads(json_str)
        issues = obj.get("issues", [])
        if isinstance(issues, str):
            issues = [issues]
        if not isinstance(issues, list):
            issues = []
        # Filter out empty strings
        issues = [s.strip() for s in issues if isinstance(s, str) and s.strip()]
        clean = json.dumps(obj, ensure_ascii=False)
        return clean, issues
    except (json.JSONDecodeError, AttributeError):
        pass

    # Fallback: check for "NO ISSUES" in the raw text
    upper = raw.upper()[:30]
    if "NO ISSUES" in upper:
        return raw, []

    # Fallback: extract numbered issues from raw text
    # Matches patterns like "1. Some issue text" or "- Some issue text"
    numbered = re.findall(r"(?:^|\n)\s*(?:\d+[\.\)]\s*|[-\*]\s*)(.+?)(?=\n\s*(?:\d+[\.\)]\s*|[-\*]\s*)|$)", raw, re.DOTALL)
    issues = [m.strip() for m in numbered if m.strip() and len(m.strip()) > 5]
    if issues:
        return raw, issues

    # 修复 2: 移除"last resort"逻辑——非 JSON 且非 thinking 的输出不应视为有效 issue
    # 原 last-resort 逻辑把任意 >20 字非 thinking 文本当 issue，导致假阳性 refine
    return raw, []


def run_self_refine(
    model: BaseChatModel,
    task: int,
    case_id: str,
    transcript: str,
    draft_decision: str = "",
    max_iterations: int = 2,
) -> tuple[str, list[str]]:
    """Run the Critique → Refine loop.

    Args:
        model: The LLM to use (same Qwen base, different prompt role).
        task: Task number (1/2/3).
        case_id: Case identifier for logging.
        transcript: The agent's reasoning transcript from Stage B.
        draft_decision: The agent's preliminary decision (if known).
        max_iterations: Max critique→refine cycles (default 2).

    Returns:
        Tuple of (refined_transcript, warnings). If no issues are found or
        refinement fails, the original transcript is returned unchanged.
    """
    warnings: list[str] = []
    current_transcript = transcript
    transcript_snippet = transcript[:4000] if len(transcript) > 4000 else transcript

    for iteration in range(1, max_iterations + 1):
        # --- Step 1: Critique ---
        critique_user = (
            f"Case ID: {case_id} (Task {task})\n"
            f"Preliminary decision: {draft_decision or 'unknown'}\n\n"
            f"Evidence + reasoning transcript:\n"
            f'"""\n{transcript_snippet}\n"""\n\n'
            "Audit this reasoning and output ONLY a JSON object with "
            '"issues" (list of specific problems) and "severity". '
            'If no issues, output {"issues": [], "severity": "low"}. '
            "Do NOT restate these instructions."
        )
        try:
            critique_resp = model.invoke(
                [
                    SystemMessage(content=_CRITIQUE_SYSTEM),
                    HumanMessage(content=critique_user),
                ]
            )
            critique_text = (
                critique_resp.content
                if isinstance(critique_resp.content, str)
                else json.dumps(critique_resp.content)
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"self_refine iter {iteration}: critique failed: {type(exc).__name__}: {exc}")
            log.warning("self_refine critique failed (iter %d) for %s: %s", iteration, case_id, exc)
            break

        # Parse JSON critique output; fall back to raw text if not valid JSON
        critique_clean, critique_issues = _parse_critique_json(critique_text)

        # === TRACE DUMP（不影响主流程，失败不报错）===
        import os as _os, json as _json, time as _time
        try:
            _trace_dir = _os.path.join(_os.environ.get("CHIMERA_OUTPUT_DIR", "output"), "trace", _trace_case_id(case_id))
            _os.makedirs(_trace_dir, exist_ok=True)
            with open(_os.path.join(_trace_dir, f"critique_iter{iteration}.json"), "w") as f:
                _json.dump({"iteration": iteration,
                            "critique_raw_full": critique_text,
                            "parsed_issues": critique_issues,
                            "severity_hint": "see raw"}, f, ensure_ascii=False)
            with open(_os.path.join(_trace_dir, f"refined_iter{iteration}.txt"), "w") as f:
                f.write(current_transcript)
        except Exception:
            pass

        if not critique_issues:
            log.info("self_refine: %s iter %d — no issues found", case_id, iteration)
            if iteration > 1:
                warnings.append(f"self_refine: converged after {iteration} iterations")
            else:
                warnings.append("self_refine: no issues found (1 iteration)")
            break

        critique_summary = "; ".join(critique_issues)
        warnings.append(f"self_refine iter {iteration} critique: {critique_summary[:400]}")
        log.info("self_refine: %s iter %d critique found %d issues:\n%s",
                 case_id, iteration, len(critique_issues), critique_summary[:600])

        # --- Step 2: Refine ---
        refine_user = (
            f"Case ID: {case_id} (Task {task})\n\n"
            f"Original evidence + reasoning:\n"
            f'"""\n{transcript_snippet}\n"""\n\n'
            f"Reviewer critique (iteration {iteration}):\n"
            + "\n".join(f"  {i+1}. {iss}" for i, iss in enumerate(critique_issues))
            + "\n\nProduce the corrected reasoning trace."
        )
        try:
            refine_resp = model.invoke(
                [
                    SystemMessage(content=_REFINE_SYSTEM),
                    HumanMessage(content=refine_user),
                ]
            )
            refined = (
                refine_resp.content
                if isinstance(refine_resp.content, str)
                else json.dumps(refine_resp.content)
            )
            if refined and len(refined.strip()) > 50:
                current_transcript = refined.strip()
                log.info("self_refine: %s iter %d — refined successfully", case_id, iteration)
            else:
                warnings.append(f"self_refine iter {iteration}: refine produced empty/short output, keeping previous")
                log.warning("self_refine: %s iter %d refine produced empty output", case_id, iteration)
                break
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"self_refine iter {iteration}: refine failed: {type(exc).__name__}: {exc}")
            log.warning("self_refine refine failed (iter %d) for %s: %s", iteration, case_id, exc)
            break

        # On the last iteration, don't run another critique
        if iteration == max_iterations:
            warnings.append(f"self_refine: reached max iterations ({max_iterations})")
            break

    return current_transcript, warnings
