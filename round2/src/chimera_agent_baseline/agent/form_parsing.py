"""JSON extraction, transcript selection and legacy decision recovery.

Mechanically extracted from form_fill; fallback values and parsing behavior
remain unchanged. Strict execution policy is enforced by the orchestrator.
"""
from __future__ import annotations

import json
import re
from typing import Any
from langchain_core.messages import AIMessage

def _extract_json_object(text: str) -> str:
    """Best-effort extraction of a JSON object from raw model output.

    Handles three failure modes observed in the wild:
    1. CoT prose containing { } before the real JSON.
    2. { } inside string values (e.g. free_text: "grade group {1}").
    3. Truncated output where no matching } exists.
    """
    if not text:
        return text

    # 1. Prefer markdown fence if present. The regex is greedy to the last
    # '}' of the fenced block, then _balanced_extract re-trims to the
    # outermost object — the old non-greedy version stopped at the FIRST
    # '}' and silently truncated fenced JSON containing nested objects
    # (e.g. variable_weights).
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        balanced = _balanced_extract(fence.group(1), 0)
        return balanced if balanced else fence.group(1)

    # 2. Collect every balanced {…} candidate (skips braces inside strings).
    candidates: list[str] = []
    for i, ch in enumerate(text):
        if ch == "{":
            obj = _balanced_extract(text, i)
            if obj and obj not in candidates:
                candidates.append(obj)

    if not candidates:
        return text  # no { at all — let parser raise the error

    # 3. Longest parseable candidate first. The judgment object is the
    # outermost (largest) balanced object in the text. The old
    # `reversed(candidates)` ordering tried the LAST opening brace first,
    # which — for bare JSON with nested variable_weights — returned the
    # innermost sub-object, failed schema validation, and degraded every
    # such case to the salvage path.
    for c in sorted(candidates, key=len, reverse=True):
        try:
            json.loads(c)
            return c
        except json.JSONDecodeError:
            # Try fixing trailing commas (common in small-model output).
            fixed = re.sub(r",\s*([}\]])", r"\1", c)
            try:
                json.loads(fixed)
                return fixed
            except json.JSONDecodeError:
                continue

    # 4. All candidates failed to parse — return the longest (the biggest
    # chunk of the real object) so the parser's error is informative.
    return max(candidates, key=len)


def _balanced_extract(text: str, start: int) -> str | None:
    """Extract a balanced {…} substring starting at *start*, skipping braces
    that appear inside JSON string values.

    Returns ``None`` when no matching ``}`` is found (truncated output).
    """
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _final_assistant_text(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
            return m.content if isinstance(m.content, str) else json.dumps(m.content)
    return ""


def _is_tool_call_block(text: str) -> bool:
    """判断一个文本块是否为工具调用（<function= 语法或纯 JSON 工具调用）。

    P5：ReAct 流式输出中模型常把工具调用以 ``<tool_call><function=...>``
    或纯 JSON 形式追加在正文末尾。这类块不能作为 free_text 的正文来源。
    """
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.startswith(
        ("<function=", "<tool_call", "<parameter=", "</function>", "</tool_call>", "</parameter>")
    ):
        return True
    # 纯 JSON 工具调用：可解析且含 function/name/arguments/tool_call 键
    if stripped.startswith(("{", "[")):
        try:
            obj = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return False
        items = obj if isinstance(obj, list) else [obj]
        return any(
            isinstance(d, dict) and any(k in d for k in ("function", "name", "arguments", "tool_call"))
            for d in items
        )
    return False


def _final_answer_tail(transcript: str) -> str:
    """从 transcript 提取"最后一个非工具调用文本块"。

    P5：free_text 兜底原先直接取 ``transcript[-500:]``，会抓到 ReAct 工具
    调用语法裸文本（``<function=...>`` / ``</think>`` / 纯 JSON 工具调用），
    实测 14/91 case free_text 中毒、GEval 0.00-0.20。改为按消息边界（空行）
    切块 → 跳过工具调用块 → 取最后一段 >=100 字符的正文块。
    无工具调用块时返回原文，保持原有行为不变。
    """
    if not transcript:
        return transcript
    blocks = [b.strip() for b in re.split(r"\n\s*\n", transcript) if b.strip()]
    text_blocks = [b for b in blocks if not _is_tool_call_block(b)]
    if len(text_blocks) == len(blocks):
        # 无工具调用块 → 行为不变，直接返回原文
        return transcript
    for b in reversed(text_blocks):
        if len(b) >= 100:
            return b
    # 没有 >=100 字符的正文块，退回原文尾部
    return transcript


# ── P6: decision 字段兜底 — 优先 salvage raw_json，失败才走默认值 ──

_T2_PRIMARY_VALUES = (
    "active_surveillance|active_treatment|continued_surveillance|watchful_waiting"
)


def _salvage_decision(task: int, raw_json: str) -> tuple[str | None, bool]:
    """P6: 从 raw_json 文本中 salvage 决策字段。

    解析成功但 schema 未捕获 decision（T1 biopsy_decision / T2 primary）时，
    用正则从原始 JSON 文本提取合法决策值，避免静默默认值污染决策。
    命中合法值返回 (value, True)；否则 (None, False)。
    """
    if not isinstance(raw_json, str) or not raw_json:
        return None, False
    if task == 1:
        m = re.search(r'"biopsy_decision"\s*:\s*"(yes|no)"', raw_json)
        if m:
            return m.group(1), True
    elif task == 2:
        m = re.search(rf'"primary"\s*:\s*"({_T2_PRIMARY_VALUES})"', raw_json)
        if m:
            return m.group(1), True
    return None, False


def _fill_missing_decision_fields(
    task: int, judgment: dict, raw_json: str, warnings: list
) -> None:
    """P6: 补缺 decision 字段——优先 salvage raw_json，失败才走默认值。

    只改动失败路径（decision 缺失时）；成功路径行为与改前完全一致
    （P6 只动失败路径）。
    """
    if task == 1:
        if not judgment.get("biopsy_decision"):
            val, ok = _salvage_decision(1, raw_json)
            if ok:
                judgment["biopsy_decision"] = val
                warnings.append("decision salvaged from raw response")
            else:
                judgment["biopsy_decision"] = "no"
                warnings.append("decision DEFAULTED (parse incomplete): biopsy_decision")
        if not judgment.get("confidence"):
            judgment["confidence"] = "uncertain"
        if not judgment.get("variable_weights"):
            judgment["variable_weights"] = {}
    elif task == 2:
        if not judgment.get("treatment_recommendation"):
            judgment["treatment_recommendation"] = {}
        rec = judgment["treatment_recommendation"]
        if not rec.get("primary"):
            val, ok = _salvage_decision(2, raw_json)
            if ok:
                rec["primary"] = val
                warnings.append("decision salvaged from raw response")
            else:
                rec["primary"] = "active_surveillance"
                warnings.append("decision DEFAULTED (parse incomplete): primary")
        rec.setdefault("modalities", [])
        rec.setdefault("detail", None)
        rec.setdefault("as_protocol", None)
        rec.setdefault("as_trigger", None)
        if not judgment.get("confidence"):
            judgment["confidence"] = "uncertain"
        if not judgment.get("variable_weights"):
            judgment["variable_weights"] = {}


# ── P6-failure: parse 全部失败时的 salvage + 默认兜底 ──


def _salvage_decision_from_raw(text: str, task: int) -> dict[str, Any] | None:
    """P6: 从 raw_response 全文抢救 decision 字段（解析全部失败时调用）。

    与 _salvage_decision 的区别：
    - _salvage_decision 操作 raw_json（已提取的 JSON 串），用于成功路径补缺。
    - 本函数操作完整 raw_response（可能含 thinking / 散落字段 / 代码块），
      用于失败路径（所有 retry 解析失败）的最后抢救。

    正则匹配 JSON 格式的 decision 字段，在以下 6 种 raw 形态中均能命中
    （空文本除外）：
    1. 正常 JSON
    2. 代码块 JSON (```json ... ```)
    3. 字段散落在散文中
    4. thinking 标签污染
    5. 乱码中嵌入合法字段
    6. 空文本 → None

    Returns: 包含 decision 字段的 dict，或 None（抢救失败）。
    """
    if not text or not text.strip():
        return None
    if task == 1:
        m = re.search(r'"biopsy_decision"\s*:\s*"(yes|no)"', text, re.IGNORECASE)
        if m:
            return {"biopsy_decision": m.group(1).lower()}
    elif task == 2:
        m = re.search(
            r'"primary"\s*:\s*"(active_surveillance|active_treatment|'
            r'continued_surveillance|watchful_waiting)"',
            text, re.IGNORECASE,
        )
        if m:
            return {"treatment_recommendation": {"primary": m.group(1).lower()}}
    elif task == 3:
        ev = re.search(r'"event"\s*:\s*([01])', text)
        mo = re.search(r'"months_to_recurrence"\s*:\s*([0-9.]+)', text)
        if ev and mo:
            return {"event": int(ev.group(1)), "months_to_recurrence": float(mo.group(1))}
    return None


def _has_decision(obj: dict, task: int) -> bool:
    """检查 dict 是否包含该 task 的 decision 字段。"""
    if task == 1:
        return "biopsy_decision" in obj
    if task == 2:
        rec = obj.get("treatment_recommendation")
        return isinstance(rec, dict) and "primary" in rec
    return "event" in obj and "months_to_recurrence" in obj


def _default_judgment(task: int) -> dict[str, Any]:
    """最后兜底默认 judgment（salvage 也失败时使用）。

    值与改前静默兜底一致：T1 biopsy_decision=no, T2 primary=active_surveillance,
    T3 event=0 months=36.0。
    """
    if task == 3:
        return {
            "event": 0,
            "months_to_recurrence": 36.0,
            "repeat_test": None,
            "free_text": "Decision could not be parsed; defaulted to no recurrence.",
        }
    base: dict[str, Any] = {
        "confidence": "uncertain",
        "variable_weights": {},
        "repeat_test": None,
        "free_text": "Decision could not be parsed; defaulted.",
    }
    if task == 1:
        base["biopsy_decision"] = "no"
    else:
        base["treatment_recommendation"] = {
            "primary": "active_surveillance",
            "modalities": [],
            "detail": None,
            "as_protocol": None,
            "as_trigger": None,
        }
    return base


def _merge_salvaged_into_schema(salvaged: dict, task: int) -> dict[str, Any]:
    """用 salvaged 的 decision 覆盖默认 judgment 的对应字段，补全其余必填字段。

    对 treatment_recommendation 等嵌套 dict 做一层 deep-merge，
    避免 update() 整体替换丢失 modalities/detail 等字段。
    """
    judgment = _default_judgment(task)
    for key, val in salvaged.items():
        if isinstance(val, dict) and isinstance(judgment.get(key), dict):
            judgment[key] = {**judgment[key], **val}
        else:
            judgment[key] = val
    return judgment
