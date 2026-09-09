"""Terminal form-fill node — prompt + parse against the output schema.

The ReAct loop ends when the model stops issuing tool calls. The router
then routes to this node, which prompts the same model to emit a JSON
object matching a per-case *judgment* shape (built dynamically from
:mod:`chimera_agent_baseline.output.schema`) and validates with
:class:`langchain_core.output_parsers.PydanticOutputParser`. The judgment
is then merged with the programmatic case fields (``case_id``, ``patient``,
``reveal_sequence`` — derived from the run itself) into the full
``Task<N>Output`` record.

We deliberately avoid ``model.with_structured_output`` — it relies on
function-calling support that varies wildly across providers (Gemma 4's
offline tool-call parser, for instance, sometimes does not emit a tool
call when there is only one forced schema-tool). Prompt-and-parse is
provider-neutral: any LangChain ``BaseChatModel`` works.

The node retries on validation errors up to ``max_retries`` times and
raises if every attempt fails — there is no partial / stub fallback, so
an unfillable case aborts the run loudly rather than writing a
half-formed prediction.
"""

from __future__ import annotations

from chimera_agent_baseline.agent.fact_check import atomic_fact_check

import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser

from chimera_agent_baseline.output.schema import (
    VARIABLES_BY_TASK,
    assemble_full_output,
    build_dynamic_model,
    eligible_variables,
    reveal_info_for_tool,
)

log = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are filling out the structured decision form for the prostate-cancer "
    "case you just analysed. The user message contains your reasoning "
    "transcript and the tools you called. Output a SINGLE JSON object matching "
    "the supplied schema EXACTLY — no extra keys, no markdown fences, no "
    "commentary before or after the JSON.\n\n"
    "CRITICAL — JSON FORMAT RULES (violations cause parse failure):\n"
    "1. Do NOT use // comments. JSON does not support comments. Any text after "
    "// on a line will break parsing.\n"
    "2. Do NOT copy skeleton placeholders like <\"yes\"|\"no\">, <number 6-60>, "
    "or <weight_enum> into your output. Replace every <...> with a real value.\n"
    "3. Do NOT wrap output in ```json fences. Output the raw JSON object only.\n"
    "4. Do NOT add trailing commas after the last key in an object or array.\n"
    "5. Every string value must be in double quotes. Use JSON null (not 'null' "
    "string) for empty values where indicated.\n\n"
    "IMPORTANT — Before finalising your decision, re-examine whether you are "
    "over-treating. High PSA or high PI-RADS alone does NOT justify biopsy or "
    "active treatment without considering PSA density, prior pathology, and "
    "guideline thresholds. Conservative management (active_surveillance, "
    "watchful_waiting, no biopsy) is often the correct answer. For Task 3, "
    "a single post-treatment PSA above zero does NOT automatically mean "
    "recurrence — check the modality-specific threshold.\n\n"
    "CONFIDENCE CALIBRATION RULES:\n"
    "- Use 'clear' ONLY when the evidence is unambiguous and the decision "
    "follows directly from well-established thresholds (e.g., PI-RADS 5 + "
    "PSAD > 0.5 + positive biopsy → active treatment, clear).\n"
    "- Use 'borderline' when there is moderate ambiguity (e.g., PI-RADS 4 "
    "with borderline PSAD, or conflicting evidence).\n"
    "- Use 'uncertain' when genuine clinical uncertainty exists. Specifically:\n"
    "  * PSA in the boundary zone (3-4 ng/mL) with no other strong features\n"
    "  * PI-RADS 3 (equivocal lesion) — always uncertain unless other "
    "high-risk features are present\n"
    "  * ISUP Grade Group 2 (borderline between AS and active treatment)\n"
    "  * PSA density in the range 0.10-0.20 (gray zone)\n"
    "  * Conflicting or incomplete evidence\n"
    "  * Any case where reasonable clinicians would disagree\n"
    "Approximately 15% of cases should receive 'uncertain' — do NOT default "
    "to 'clear' or 'borderline' when the evidence is genuinely ambiguous."
)


def _diagnose_parse_error(raw: str, exc: Exception) -> str:
    """Diagnose common JSON parse errors and provide specific, actionable feedback.

    Instead of a generic "did not validate" message, this checks for the
    specific failure patterns observed in practice (// comments, skeleton
    placeholders, markdown fences, trailing commas) and tells the model
    exactly what to fix.
    """
    hints: list[str] = []

    if "//" in raw:
        hints.append(
            "Your output contains // comments. JSON does not support comments. "
            "Remove ALL // comments from your output."
        )

    if re.search(r'<[^>]+>', raw):
        hints.append(
            "Your output contains skeleton placeholders (text inside < > angle "
            "brackets like <\"yes\"|\"no\"> or <number>). Replace every <...> "
            "placeholder with a real value."
        )

    if "```" in raw:
        hints.append(
            "Your output is wrapped in markdown fences (```). Remove the ``` "
            "markers — output ONLY the raw JSON object."
        )

    # Check for trailing commas (common with small models)
    if re.search(r',\s*[}\]]', raw):
        hints.append(
            "Your output contains trailing commas before } or ]. "
            "Remove all trailing commas — the last item in a JSON object or "
            "array must not be followed by a comma."
        )

    if not hints:
        # Fall back to the raw exception message
        hints.append(
            f"Validation error: {exc}. Emit the JSON object exactly matching "
            "the shape above. Every required key must appear. Output ONLY the JSON."
        )
    else:
        hints.append(
            "Fix the issue(s) above and emit the JSON object exactly matching "
            "the shape. Output ONLY the raw JSON — no comments, no fences."
        )

    return " ".join(hints)


def make_form_fill_node(
    model: BaseChatModel,
    max_retries: int = 3,
    decision_override: bool = False,
    self_refine: bool = False,
    atomic_check: bool = False,
):
    """Return a LangGraph node closure that captures the unbound *model*.

    *max_retries* is the number of validation attempts before the node
    raises :class:`RuntimeError`. A successful attempt yields a payload
    that already validates against the per-task output model
    (Task1Output / Task2Output / Task3Output).

    *decision_override*: when True and ``state["predictor_decision"]`` is
    present, the final gate-relevant decision fields are replaced by the
    deterministic predictor's decision (biopsy_decision / primary /
    event+months). The LLM's free_text / weights / confidence stay as-is.

    *self_refine*: when True, after the initial judgment is parsed, a
    Critique→Refine LLM loop runs once. The critique identifies reasoning
    errors (over-treatment, unsupported weights, confidence miscalibration,
    fabricated facts); the refine step produces a corrected judgment. If
    the refined judgment fails validation, the original is kept.

    *atomic_check*: when True, a programmatic (no LLM) fact-check runs
    after self-refine. It verifies that values cited in free_text appear
    in the transcript, that decisive/important variables have supporting
    mentions, and that confidence is calibrated to evidence strength.
    Issues are logged as warnings; obvious confidence over-calibration is
    auto-downgraded (clear→borderline).
    """

    def form_fill(state: dict[str, Any]) -> dict[str, Any]:
        messages = state["messages"]
        task = int(state.get("task", 1))
        case_id = state.get("case_id", "unknown")
        predictor_dec = state.get("predictor_decision") or {}

        tool_order = _called_tools_in_order(messages)
        called = set(tool_order)
        transcript = _final_assistant_text(messages)
        elig = eligible_variables(task, called) if task in VARIABLES_BY_TASK else []

        Dynamic = build_dynamic_model(task, called)
        parser = PydanticOutputParser(pydantic_object=Dynamic)
        skeleton = _build_skeleton_instructions(task, elig)

        log.info(
            "form_fill: case=%s task=%d called_tools=%s eligible_vars=%s predictor=%s "
            "self_refine=%s atomic_check=%s",
            case_id,
            task,
            tool_order,
            elig,
            predictor_dec.get("decision") or predictor_dec.get("event"),
            self_refine,
            atomic_check,
        )

        predictor_note = _predictor_note(task, predictor_dec)
        base_user = _user_prompt(case_id, task, transcript, tool_order, elig) + predictor_note + "\n\n" + skeleton

        # Provider-agnostic retry loop. We keep the conversation flat — a
        # single system + user pair, regenerated on retry — because small
        # models can be derailed by long error-laden histories. The retry
        # message names the missing/invalid fields explicitly.
        warnings: list[str] = []
        judgment: dict[str, Any] | None = None
        retry_hint: str | None = None
        raw = ""  # P6: initialized here so failure-path salvage can access it

        for attempt in range(1, max_retries + 1):
            user_content = base_user if not retry_hint else f"{base_user}\n\n{retry_hint}"
            raw = ""
            try:
                # Try assistant prefill "{" to force JSON-first output.
                try:
                    response = model.invoke(
                        [
                            SystemMessage(content=_SYSTEM_PROMPT),
                            HumanMessage(content=user_content),
                            AIMessage(content="{"),
                        ]
                    )
                    resp_text = response.content if isinstance(response.content, str) else json.dumps(response.content)
                    raw = "{" + resp_text
                except Exception as invoke_exc:
                    log.warning("form_fill: assistant prefill failed (%s), falling back to no-prefill", invoke_exc)
                    response = model.invoke(
                        [
                            SystemMessage(content=_SYSTEM_PROMPT),
                            HumanMessage(content=user_content),
                        ]
                    )
                    raw = response.content if isinstance(response.content, str) else json.dumps(response.content)
                # === TRACE DUMP（不影响主流程，失败不报错）===
                import os as _os, json as _json, time as _time
                _trace_path = None
                try:
                    _trace_dir = _os.path.join(_os.environ.get("CHIMERA_OUTPUT_DIR", "output"), "trace", str(case_id))
                    _os.makedirs(_trace_dir, exist_ok=True)
                    _reasoning = ""
                    try:
                        _reasoning = (response.additional_kwargs.get("reasoning_content", "") or "")[:2000]
                    except Exception:
                        pass
                    _trace_path = _os.path.join(_trace_dir, f"form_fill_attempt{attempt}.json")
                    with open(_trace_path, "w") as f:
                        _json.dump({
                            "ts": _time.time(),
                            "attempt": attempt,
                            "user_content": user_content[:4000],
                            "raw_response": raw[:4000],
                            "reasoning_content": _reasoning,
                            "parse_ok": False,  # 成功时改为 True
                        }, f, ensure_ascii=False)
                except Exception:
                    pass
                # --- 检测扁平输出：变量键直接在顶层而非 variable_weights 内 ---
                raw_json = _extract_json_object(raw)
                try:
                    _tmp = json.loads(raw_json)
                    if isinstance(_tmp, dict) and any(k in elig for k in _tmp):
                        _weights = {k: _tmp.pop(k) for k in list(_tmp.keys()) if k in elig}
                        _tmp.setdefault("variable_weights", {}).update(_weights)
                        raw_json = json.dumps(_tmp)
                except (json.JSONDecodeError, TypeError):
                    pass
                obj = parser.parse(raw_json)
                judgment = obj.model_dump(mode="json")
                # === TRACE DUMP: 解析成功，回写 parse_ok=True ===
                if _trace_path:
                    try:
                        with open(_trace_path, "r") as f:
                            _trace = _json.load(f)
                        _trace["parse_ok"] = True
                        with open(_trace_path, "w") as f:
                            _json.dump(_trace, f, ensure_ascii=False)
                    except Exception:
                        pass
                # --- 补缺必要字段（扁平输出时 wrapper 字段可能缺失或为 None）---
                # P6: decision 缺失时先 salvage raw_json，失败才走默认值（warning 区分）
                _fill_missing_decision_fields(task, judgment, raw_json, warnings)
                judgment.setdefault("repeat_test", None)
                if not judgment.get("free_text") or len(judgment.get("free_text", "")) < 40:
                    # P5: 从 transcript 尾部提取"最后一个非工具调用文本块"——
                    # 直接截尾部会抓到 ReAct 工具调用语法裸文本（<function=...>），
                    # 实测 14/91 case free_text 中毒、GEval 0.00-0.20。
                    # 按消息边界切块 → 跳过工具调用块 → 取最后一段 >=100 字符
                    # 的正文块 → 再取其 [-500:]。
                    judgment["free_text"] = (_final_answer_tail(transcript) or "Reasoning not available.")[-500:]
                    warnings.append("free_text fallback: final answer extracted")
                break
            except Exception as exc:  # noqa: BLE001 — any parse/validation failure triggers a retry
                warnings.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                log.warning("form_fill parse failed (attempt %d) for %s: %s", attempt, case_id, exc)
                if attempt == max_retries:
                    break
                retry_hint = (
                    "Your previous attempt did not validate. "
                    + _diagnose_parse_error(raw, exc)
                )

        if judgment is None:
            # P6: All retries failed — salvage decision from last raw response
            # before falling back to defaults. Prevents silent AS pollution
            # when the model emitted a valid decision but wrapped it in
            # unparseable JSON.
            salvaged = _salvage_decision_from_raw(raw, task)
            if salvaged is not None:
                log.warning(
                    "form_fill: parse failed but decision salvaged from raw_response "
                    "(case=%s, task=%d)", case_id, task,
                )
                warnings.append("decision salvaged from raw_response (all retries failed)")
                judgment = _merge_salvaged_into_schema(salvaged, task)
            else:
                log.error(
                    "form_fill: salvage failed, falling back to default "
                    "(task=%d, case=%s)", task, case_id,
                )
                warnings.append("decision DEFAULTED (all retries + salvage failed)")
                judgment = _default_judgment(task)
            # Fill free_text from transcript (same as success path)
            if not judgment.get("free_text") or len(judgment.get("free_text", "")) < 40:
                judgment["free_text"] = (
                    _final_answer_tail(transcript) or "Reasoning not available."
                )[-500:]
                warnings.append("free_text fallback: final answer extracted (parse failed)")
            judgment.setdefault("repeat_test", None)

        # --- Hippocrates self-refine: Critique → Refine (1 iteration) ---
        if self_refine:
            judgment = _run_self_refine(
                model, parser, task, case_id, transcript, judgment, skeleton, warnings
            )

        # --- Atomic fact verification (programmatic, no LLM) ---
        if atomic_check:
            fc_result = atomic_fact_check(task, case_id, transcript, judgment, elig, model=model)
            warnings.extend(fc_result.warnings)
            # P1-C: Post-check confidence calibration — downgrade to 'uncertain'
            # when the evidence is genuinely ambiguous (beyond fact_check's
            # clear→borderline downgrade).
            _calibrate_uncertain(task, transcript, judgment, fc_result, warnings)

        if decision_override and predictor_dec:
            _apply_decision_override(task, judgment, predictor_dec, warnings)

        # 修复 5: 公式计算后处理 — 校验 free_text 数值，修正错误
        ft = judgment.get("free_text", "")
        if ft:
            verified = _calculate_and_verify(ft, state.get("patient") or {}, task, judgment, warnings)
            if verified != ft:
                judgment["free_text"] = verified

        # T2 AT→AS 欠治疗偏置修复：高危 case 强制 AT
        if task == 2:
            _enforce_treatment_floor(judgment, transcript, warnings)

        # T3 event 偏置修复：防止 LLM 机械套 BCR 阈值导致 event=1 偏置
        if task == 3:
            _calibrate_t3_event(judgment, transcript, warnings)

        # T3 基率校准：months 阈值重切（训练集 τ=24, 详见 _apply_t3_tau docstring）
        if task == 3:
            _apply_t3_tau(judgment, warnings)

        # T3-004 型兜底：event=0 且 months=None 时填随访时长默认值
        # （防 pydantic float 校验崩溃；值取 GT 随访时长中位数≈36，见 Step 6a E1）
        if task == 3 and judgment.get("event") == 0 and judgment.get("months_to_recurrence") is None:
            judgment["months_to_recurrence"] = 36.0
            warnings.append("t3_months_fallback: event=0 & months=None → 36.0")

        # 修复 4: variable_weights 后处理统计校准
        if task in (1, 2) and judgment.get("variable_weights"):
            dec_str = judgment.get("biopsy_decision") or ""
            if task == 2:
                rec = judgment.get("treatment_recommendation", {})
                if isinstance(rec, dict):
                    dec_str = rec.get("primary", "")
            weight_warns = _calibrate_weights(judgment, task, dec_str, judgment.get("confidence", "clear"), state.get("patient") or {})
            warnings.extend(weight_warns)
            # Wave3 B: confidence 分布校准（clear→borderline），只作用于 confidence
            _calibrate_confidence(judgment, task, dec_str, warnings)

        # Wave3 B: T3 free_text 注入 GT 高频要素（Gleason/切缘/术后 PSA 趋势）
        if task == 3:
            _inject_t3_salient_elements(judgment, transcript, warnings)

        reveal_sequence = _build_reveal_sequence(
            tool_order,
            judgment.get("variable_weights", {}),
            task=task,
            patient_data=state.get("patient") or {},
            free_text=judgment.get("free_text", ""),
        )
        patient = _build_patient(case_id, state)
        # P2: atomic_fact_check 注入的内部字段 _fact_check_flags 无任何下游
        # 读取方，且 Task1/2/3Output 均 extra="forbid" —— 不清除会导致
        # ValidationError(extra_forbidden)。此处静默清除（不记 warning）。
        judgment.pop("_fact_check_flags", None)
        full = assemble_full_output(task, case_id, patient, reveal_sequence, judgment)
        return {"structured_response": full, "form_fill_warnings": warnings}

    return form_fill


def _predictor_note(task: int, predictor_dec: dict) -> str:
    """渲染确定性 predictor 的决策为 prompt 里的"临床预测参考"段落。"""
    if not predictor_dec:
        return ""
    if task == 3:
        if "event" not in predictor_dec:
            return ""
        ev = "recurrence" if predictor_dec.get("event") == 1 else "no recurrence"
        months = predictor_dec.get("months_to_recurrence")
        m_str = f", expected time {months:.1f} months" if months is not None else ""
        return (
            "\n\nClinical prediction reference (from a quantitative model over the "
            f"same patient data): event = {ev}{m_str}. You may agree or disagree, "
            "but your final structured output must be consistent with the evidence."
        )
    dec = predictor_dec.get("decision")
    if not dec:
        return ""
    label = {"yes": "biopsy", "no": "no biopsy"}.get(dec, dec)
    return (
        "\n\nClinical prediction reference (from a quantitative model over the "
        f"same patient data): {label}. You may agree or disagree, but your final "
        "structured output must be consistent with the evidence."
    )


def _apply_decision_override(task: int, judgment: dict, predictor_dec: dict, warnings: list) -> None:
    """用 predictor 决策覆盖 gate 相关字段（free_text/weights/confidence 不动）。"""
    if task == 1:
        dec = predictor_dec.get("decision")
        if dec in ("yes", "no") and judgment.get("biopsy_decision") != dec:
            warnings.append(f"decision override: biopsy_decision {judgment.get('biopsy_decision')} -> {dec}")
            judgment["biopsy_decision"] = dec
    elif task == 2:
        dec = predictor_dec.get("decision")
        rec = judgment.setdefault("treatment_recommendation", {})
        if dec and rec.get("primary") != dec:
            warnings.append(f"decision override: primary {rec.get('primary')} -> {dec}")
            rec["primary"] = dec
    elif task == 3:
        if "event" in predictor_dec:
            ev = int(predictor_dec["event"])
            if judgment.get("event") != ev:
                warnings.append(f"decision override: event {judgment.get('event')} -> {ev}")
                judgment["event"] = ev
        m = predictor_dec.get("months_to_recurrence")
        if m is not None:
            judgment["months_to_recurrence"] = float(m)


# ---------------------------------------------------------------------------
# 修复 5: 公式计算后处理 — 校验 free_text 数值，修正错误
# ---------------------------------------------------------------------------


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _downgrade_confidence_post(judgment: dict, warnings: list[str], reason: str) -> None:
    """Downgrade confidence to 'uncertain' when decision contradicts evidence."""
    conf = judgment.get("confidence", "")
    if conf in ("clear", "borderline"):
        judgment["confidence"] = "uncertain"
        warnings.append(f"confidence: {conf} → uncertain ({reason})")
        log.info("numeric_verify: confidence downgraded: %s", reason)


def _calculate_and_verify(
    free_text: str,
    patient_data: dict[str, Any],
    task: int,
    judgment: dict[str, Any],
    warnings: list[str],
) -> str:
    """Post-processing: verify and correct numeric values in free_text.

    Recomputes PSAD, PSADT, BCR thresholds from values mentioned in free_text
    and corrects wrong values (>10% error). Flags contradictions and
    downgrades confidence when the decision contradicts the evidence.

    Only modifies free_text and confidence — never changes decision fields.
    """
    if not free_text:
        return free_text

    corrections: list[str] = []

    # --- PSAD verification: PSAD = PSA / prostate_volume ---
    # Parse PSA and volume from free_text
    psa_match = re.search(
        r"PSA\b[\s:=]*(?:is|of|was|level\s+of|value\s+of)?\s*(\d+\.?\d*)",
        free_text, re.IGNORECASE,
    )
    vol_match = re.search(
        r"(?:prostate\s+)?volume\b[\s:=]*(?:of|is|was)?\s*(\d+\.?\d*)",
        free_text, re.IGNORECASE,
    )
    if psa_match and vol_match:
        psa_val = float(psa_match.group(1))
        vol_val = float(vol_match.group(1))
        if vol_val > 0:
            true_psad = psa_val / vol_val
            # Find and correct PSAD mentions in free_text
            for m in re.finditer(
                r"(?:PSAD|PSA\s*density)[\s:]*(?:of\s+)?(?:is\s+)?(\d+\.?\d*)",
                free_text, re.IGNORECASE,
            ):
                mentioned = float(m.group(1))
                if mentioned > 0 and abs(mentioned - true_psad) / max(mentioned, true_psad, 0.001) > 0.10:
                    old_str = m.group(0)
                    new_str = old_str.replace(m.group(1), f"{true_psad:.3f}")
                    free_text = free_text.replace(old_str, new_str, 1)
                    corrections.append(f"PSAD {mentioned:.3f}→{true_psad:.3f}")

            # Check threshold claims
            if re.search(r"PSAD.*(high|elevated|above|increased|≥|>=)", free_text, re.IGNORECASE):
                if true_psad < 0.15:
                    _downgrade_confidence_post(
                        judgment, warnings,
                        f"PSAD {true_psad:.3f} < 0.15 but free_text claims elevated",
                    )

    # --- PSADT verification: PSADT = ln(2) × Δt / ln(PSA₂/PSA₁) ---
    # Parse two PSA values with dates from free_text (simplified)
    psa_vals = re.findall(r"PSA\b[\s:=]*(?:is|of|was)?\s*(\d+\.?\d*)", free_text, re.IGNORECASE)
    if len(psa_vals) >= 2:
        psa1, psa2 = float(psa_vals[0]), float(psa_vals[-1])
        # Try to find time difference mentioned in free_text
        dt_match = re.search(r"(\d+\.?\d*)\s*(?:month|mo)[\s.]", free_text, re.IGNORECASE)
        if dt_match and psa1 > 0 and psa2 > 0 and psa1 != psa2:
            dt_months = float(dt_match.group(1))
            true_psadt = math.log(2) * dt_months / abs(math.log(psa2 / psa1))
            # Find and verify PSADT mentions
            for m in re.finditer(
                r"(?:PSADT|PSA\s*doubling\s*time)[:\s]*(\d+\.?\d*)",
                free_text, re.IGNORECASE,
            ):
                mentioned = float(m.group(1))
                if mentioned > 0 and abs(mentioned - true_psadt) / max(mentioned, true_psadt, 0.001) > 0.10:
                    old_str = m.group(0)
                    new_str = old_str.replace(m.group(1), f"{true_psadt:.1f}")
                    free_text = free_text.replace(old_str, new_str, 1)
                    corrections.append(f"PSADT {mentioned:.1f}→{true_psadt:.1f}")

    # --- BCR threshold verification (Task 3 only) ---
    if task == 3:
        ft_lower = free_text.lower()
        claims_recurrence = any(
            kw in ft_lower for kw in ["recurrence", "recurrent", "relapse", "bcr", "biochemical recurrence"]
        )
        if claims_recurrence:
            # Use ground-truth PSA from patient_data if available
            current_psa = _safe_float(patient_data.get("psa"))
            if current_psa is None and psa_match:
                current_psa = float(psa_match.group(1))
            if current_psa is not None and current_psa < 0.2:
                _downgrade_confidence_post(
                    judgment, warnings,
                    f"PSA {current_psa} < 0.2 (BCR threshold) but free_text claims recurrence",
                )

    if corrections:
        warnings.append(f"numeric corrections: {'; '.join(corrections)}")
    return free_text


# ---------------------------------------------------------------------------
# T2 AT→AS 欠治疗偏置修复：高危 case 强制 AT
# ---------------------------------------------------------------------------


def _extract_int(text: str, pattern: str) -> int | None:
    """Extract the first integer matching *pattern* from *text*."""
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, IndexError):
            return None
    return None


def _extract_float(text: str, pattern: str) -> float | None:
    """Extract the first float matching *pattern* from *text*."""
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except (ValueError, IndexError):
            return None
    return None


def _enforce_treatment_floor(judgment: dict, transcript: str, warnings: list) -> None:
    """ISUP>=3 + PI-RADS>=4 + PSA>10 → 强制 AT（防欠治疗）。"""
    if judgment.get("treatment_recommendation", {}).get("primary") in (
        "active_surveillance", "continued_surveillance", "watchful_waiting"):
        isup = _extract_int(transcript, r"ISUP.*?(\d)")
        pirads = _extract_int(transcript, r"PI-?RADS.*?(\d)")
        psa = _extract_float(transcript, r"PSA[:\s]+(\d+\.?\d*)")
        if isup and isup >= 3 and pirads and pirads >= 4 and psa and psa > 10:
            judgment["treatment_recommendation"]["primary"] = "active_treatment"
            warnings.append(f"treatment floor enforced: AS→AT (ISUP{isup},PI-RADS{pirads},PSA{psa})")


def _calibrate_t3_event(judgment: dict, transcript: str, warnings: list) -> None:
    """T3 event 校准：防止 LLM 机械套 BCR 阈值导致 event=1 偏置。

    触发条件（同时满足才降级为 event=0）：
    1. LLM 预测 event=1
    2. transcript 中 LLM 的推理仅基于 "baseline PSA > 0.2" 或 "PSA exceeds threshold"
       （即没有引用术后 PSA、病理切缘阳性、Gleason 升级等独立复发证据）
    3. 无 surgical_pathology_report 中的切缘阳性 (positive_margin) 等强复发证据

    校准动作：将 event 改为 0，months 改为 None，记 warning。
    不触发条件：有独立复发证据（切缘阳性、Gleason 升级、术后 PSA 序列上升等）
    """
    event = judgment.get("event")
    if event != 1:
        return  # 只校准 event=1 的 case

    free_text = judgment.get("free_text", "").lower()
    transcript_lower = transcript.lower() if isinstance(transcript, str) else ""

    # 检查是否有独立复发证据（非 baseline PSA 套阈值）
    independent_evidence = []
    if "positive margin" in transcript_lower or "positive_margin" in transcript_lower:
        independent_evidence.append("positive_margin")
    if "gleason upgrade" in transcript_lower or "gleason upgrading" in transcript_lower:
        independent_evidence.append("gleason_upgrade")
    if "post-treatment psa" in transcript_lower and "rising" in transcript_lower:
        independent_evidence.append("rising_post_treatment_psa")
    if "psa doubling" in transcript_lower:
        independent_evidence.append("psa_doubling")
    if "nadir" in transcript_lower and "rising" in transcript_lower:
        independent_evidence.append("rising_from_nadir")

    # 检查推理是否仅基于 baseline PSA > 0.2
    weak_reasoning = (
        "psa" in free_text and
        ("0.2" in free_text or "threshold" in free_text) and
        "baseline" not in free_text and
        "pre-treatment" not in free_text
    )

    if weak_reasoning and not independent_evidence:
        # 降级：LLM 仅凭 baseline PSA 套阈值，无独立复发证据
        judgment["event"] = 0
        judgment["months_to_recurrence"] = None
        warnings.append("t3_event_calibrated: downgraded event 1→0, "
                        "LLM reasoning based on baseline PSA threshold only, "
                        "no independent recurrence evidence")
        # 在 free_text 追加校准说明
        judgment["free_text"] = (
            judgment.get("free_text", "") +
            "\n\n[校准说明] 原判断 event=1 仅基于 baseline PSA 超过 0.2 ng/mL 阈值，"
            "但该 PSA 为术前 baseline，BCR 阈值仅适用于术后 PSA。"
            "无切缘阳性、Gleason 升级、术后 PSA 序列上升等独立复发证据，降级为 event=0。"
        )


def _apply_t3_tau(judgment: dict, warnings: list) -> None:
    """T3 τ 重切 v2（仅降级）：LLM 判 event=1 但 months≥τ 时降为 event=0。

    背景：LLM event 判定系统性过判（训练集阳性率 60.3% vs GT 26%），
    months 排序质量高（C-index 0.705）。τ=24.0 训练集常量（26th 分位，
    k=16 口径：FP 13.0% / EventAcc 76.7%）。do NOT retune on eval sets.

    v2 变更（0828 L2 后）：删除升级分支。event=0 的 months 语义是随访
    时长而非复发时间——L2 实测 T3-004（censored, months=6）被强制升级为
    event=1 产生 FP，且校准说明与 LLM 原判矛盾。训练集重放中升级分支
    0 次触发（event=0 的 months 范围 24-60），删除无损失。event=0 永不改动。
    """
    TAU = 24.0  # train-calibrated; do NOT retune
    if judgment.get("event") != 1:
        return  # event=0 永不升级
    m = judgment.get("months_to_recurrence")
    if m is None:
        judgment["event"] = 0
        warnings.append("t3_tau: months=None, downgraded to event=0 (censored)")
        judgment["free_text"] = (judgment.get("free_text") or "") + (
            "\n\n[校准说明] 预测 months 缺失，按 censored 语义校准为 event=0。")
        return
    if m >= TAU:
        judgment["event"] = 0
        warnings.append(f"t3_tau: months={m} >= tau={TAU}, resliced to event=0")
        judgment["free_text"] = (judgment.get("free_text") or "") + (
            "\n\n[校准说明] 虽然上文推理识别出复发风险因素，但预测复发时间 "
            f"({m} 个月) 不在早期复发窗口内（训练集基率校准阈值 τ={TAU} 个月），"
            "按生化复发基率校准规则判定 event=0（观察期内无复发）。")


# ---------------------------------------------------------------------------
# 修复 4: variable_weights 后处理统计校准
# ---------------------------------------------------------------------------

import random as _random

_WEIGHT_CALIBRATION: dict | None = None


def _load_weight_calibration() -> dict:
    """加载 GT 统计的权重校准表（懒加载，只加载一次）。"""
    global _WEIGHT_CALIBRATION
    if _WEIGHT_CALIBRATION is None:
        # Search in multiple locations (matching fact_check.py pattern)
        candidates = [
            Path(__file__).resolve().parent.parent.parent.parent / "resources" / "weight_calibration.json",
            Path.cwd() / "resources" / "weight_calibration.json",
        ]
        for path in candidates:
            if path.exists():
                _WEIGHT_CALIBRATION = json.loads(path.read_text())
                log.info("Loaded weight calibration from %s", path)
                break
        else:
            _WEIGHT_CALIBRATION = {}
            log.warning("weight_calibration.json not found, skip calibration")
    return _WEIGHT_CALIBRATION


def _calibrate_weights(
    judgment: dict,
    task: int,
    decision: str,
    confidence: str,
    patient_data: dict,
) -> list[str]:
    """校准 variable_weights，三层机制。

    1. 频次校准：LLM 给 decisive 但 GT 该变量 decisive <30% → 按分布重采样
    2. 一致性约束：weights 与 decision 矛盾 → 修正
    3. 置信度联动：uncertain/borderline 但全 decisive → 降 1 个 important

    Returns: warnings list
    """
    warnings: list[str] = []
    calibration = _load_weight_calibration()
    if not calibration or str(task) not in calibration:
        return []

    weights = judgment.get("variable_weights", {})
    if not weights:
        return []

    _random.seed(42)  # 可复现

    task_calib = calibration[str(task)]
    corrected = dict(weights)

    # === 层 1：频次校准 ===
    for var, llm_level in weights.items():
        if var not in task_calib:
            continue
        dec_key = decision.lower().strip() if decision else "default"
        dec_dist = task_calib[var].get(dec_key, task_calib[var].get("default", {}))
        if not dec_dist:
            continue

        gt_decisive_prob = dec_dist.get("decisive", 0)

        # LLM 给 decisive 但 GT decisive <30% → 过度赋予，降级
        if llm_level == "decisive" and gt_decisive_prob < 0.30:
            levels = list(dec_dist.keys())
            probs = list(dec_dist.values())
            new_level = _random.choices(levels, weights=probs, k=1)[0]
            if new_level != llm_level:
                warnings.append(
                    f"weight calibrated: {var} {llm_level}→{new_level} "
                    f"(GT decisive prob={gt_decisive_prob:.2f})"
                )
                corrected[var] = new_level

        # LLM 给 important 但 GT decisive >60% → 低估，升级
        elif llm_level == "important" and gt_decisive_prob > 0.60:
            warnings.append(
                f"weight upgraded: {var} {llm_level}→decisive "
                f"(GT decisive prob={gt_decisive_prob:.2f})"
            )
            corrected[var] = "decisive"

    # === 层 2：一致性约束 ===
    # T1 consistency constraints reverted to exp-006 baseline (0.794 F1)

    # T2: decision=AS 但 isup=decisive → 矛盾
    if task == 2 and "active_surveillance" in decision.lower():
        isup_level = corrected.get("bx_isup", "important")
        if isup_level == "decisive":
            warnings.append("consistency: decision=AS but bx_isup=decisive, downgraded to important")
            corrected["bx_isup"] = "important"

    # === 层 3：置信度联动 ===
    if confidence in ("uncertain", "borderline"):
        decisive_count = sum(1 for v in corrected.values() if v == "decisive")
        total_vars = len(corrected)
        if total_vars > 0 and decisive_count / total_vars > 0.7:
            # 灰区不该全 decisive，降 1 个优先级最高的变量
            priority_demote = ["psa", "psad", "pirads", "bx_isup", "bx"]
            for var in priority_demote:
                if corrected.get(var) == "decisive":
                    corrected[var] = "important"
                    warnings.append(
                        f"confidence linkage: {var} demoted decisive→important "
                        f"(gray zone, confidence={confidence})"
                    )
                    break

    judgment["variable_weights"] = corrected
    return warnings


# ---------------------------------------------------------------------------
# Confidence 分布校准 + T3 free_text 病灶要素注入（Wave3 B）
# ---------------------------------------------------------------------------

_CONF_CALIB_ENABLED = True  # 校准只动 confidence，绝不动 decision


def _load_conf_calibration() -> dict:
    """从 weight_calibration.json 读取 confidence 分布表（{task: {decision: {level: p}}}）。"""
    cal = _load_weight_calibration()
    return (cal or {}).get("_confidence", {})


def _calibrate_confidence(judgment: dict, task: int, decision: str, warnings: list) -> None:
    """基于 GT confidence 分布作保守校准：仅当 LLM 标 'clear' 而该 decision 的 GT
    大多非 clear 时降为 'borderline'。绝不升级、绝不写 uncertain、绝不动 decision。"""
    if not _CONF_CALIB_ENABLED:
        return
    if judgment.get("confidence") != "clear":
        return
    cal = _load_conf_calibration().get(str(task), {})
    dec_key = (decision or "").strip().lower() or "_global"
    dist = cal.get(dec_key) or cal.get("_global") or {}
    if not dist:
        return
    p_clear = float(dist.get("clear", 0))
    if p_clear < 0.65:
        judgment["confidence"] = "borderline"
        warnings.append(
            f"confidence calibrated: clear→borderline "
            f"(GT P(clear|{dec_key})={p_clear:.2f})"
        )


def _extract_gleason(text: str) -> str | None:
    m = re.search(r"Gleason\s*(?:score\s*(?:is|of|sum)?\s*)?(\d+\s*\+\s*\d+|\d+)",
                  text or "", re.IGNORECASE)
    return (m.group(1).replace(" ", "") if m else None)


def _extract_margin(text: str) -> str | None:
    t = (text or "").lower()
    if "surgical margins were positive" in t or "positive surgical margin" in t:
        return "positive"
    if "surgical margins were negative" in t or "negative surgical margin" in t:
        return "negative"
    return None


def _extract_postop_psa(text: str) -> str | None:
    if not text:
        return None
    t = text.lower()
    if any(k in t for k in ("undetectable", "nadir", "post-operative psa",
                            "postoperative psa", "post-op psa", "follow-up psa")):
        return "trend discussed"
    return None


def _inject_t3_salient_elements(judgment: dict, transcript: str, warnings: list) -> None:
    """T3 free_text 注入 GT 高频要素：Gleason / 切缘状态 / 术后 PSA 趋势必提。

    若 free_text 已涵盖，跳过；缺失项从 transcript 抽取实际数值补写（可溯源），
    抽不到时用通用表述。只追加，绝不动 decision / event / months。
    """
    ft = judgment.get("free_text") or ""
    lower = ft.lower()

    parts = []
    # Gleason
    if not any(k in lower for k in ("gleason", "grade group", "isup")):
        g = _extract_gleason(transcript)
        parts.append(("Gleason grade", f"Gleason {g}") if g else ("Gleason grade", None))
    # 切缘状态
    if "margin" not in lower:
        m = _extract_margin(transcript)
        parts.append(("surgical margin status",
                      ("positive surgical margins" if m == "positive" else
                       "negative surgical margins") if m else None))
    # 术后 PSA 趋势
    if not any(k in lower for k in ("undetectable", "nadir", "post-operative psa",
                                    "postoperative psa", "post-op psa")):
        p = _extract_postop_psa(transcript)
        parts.append(("post-operative PSA trend", "post-operative PSA trend" if p else None))

    if not parts:
        return

    fragments = []
    for label, value in parts:
        if value:
            fragments.append(value)
    if fragments:
        injected = "Key surgical-outcome factors considered: " + "; ".join(fragments) + "."
    else:
        injected = ("Gleason grade, surgical margin status and post-operative "
                    "PSA trend were considered in the recurrence assessment.")
    judgment["free_text"] = (ft.rstrip() + " " + injected if ft else injected).strip()
    warnings.append(f"t3_free_text: injected salient elements ({len(parts)})")


def _calibrate_uncertain(
    task: int,
    transcript: str,
    judgment: dict[str, Any],
    fc_result: Any,
    warnings: list[str],
) -> None:
    """P1-C: Downgrade confidence to 'uncertain' for genuinely ambiguous cases.

    This runs AFTER atomic_fact_check's clear→borderline downgrade. It catches
    cases where the evidence is inherently uncertain (PI-RADS 3, PSA boundary
    zone, ISUP GG2, gray-zone PSAD, or many unverified claims).
    """
    confidence = judgment.get("confidence", "")
    if confidence not in ("clear", "borderline"):
        return

    # High-risk guard: don't downgrade clear high-risk cases to uncertain.
    # Cases with PI-RADS ≥ 4, ISUP ≥ 3, or PSA > 20 have unambiguous
    # high-risk features and should not be marked uncertain.
    _pirads_hr = re.search(r"PI-?RADS[:\s]*(\d)", transcript, re.IGNORECASE)
    if _pirads_hr and int(_pirads_hr.group(1)) >= 4:
        return
    _psa_hr = re.search(r"PSA[:\s]+(\d+\.?\d*)", transcript, re.IGNORECASE)
    if _psa_hr and float(_psa_hr.group(1)) > 20:
        return
    _isup_hr = re.search(r"ISUP[:\s]*(?:grade\s*group)?[:\s]*(\d)", transcript, re.IGNORECASE)
    if _isup_hr and int(_isup_hr.group(1)) >= 3:
        return

    should_uncertain = False
    reasons: list[str] = []

    # Rules are mutually exclusive: first hit triggers uncertain and stops.
    # Multiple hits only record the first reason — no stacking / over-correction.
    for _ in range(1):
        # Rule 1: UNVERIFIED claims > 2 → uncertain (allow 1-2 without triggering)
        if hasattr(fc_result, "n_unverified") and fc_result.n_unverified > 2:
            should_uncertain = True
            reasons.append(f"{fc_result.n_unverified} unverified claims")
            break

        # Rule 2: PI-RADS 3 + ISUP ≤ 2 → uncertain;
        #   PSAD > 0.15 → not gray zone (biopsy indicated, skip);
        #   PSAD 0.10-0.15 → gray zone but borderline, not uncertain;
        #   PSAD < 0.10 → genuinely uncertain (very low, ambiguous)
        pirads_match = re.search(r"PI-?RADS[:\s]*(\d)", transcript, re.IGNORECASE)
        if pirads_match:
            pirads_val = int(pirads_match.group(1))
            if pirads_val == 3:
                isup_match = re.search(
                    r"ISUP[:\s]*(?:grade\s*group)?[:\s]*(\d)", transcript, re.IGNORECASE
                )
                isup_val = int(isup_match.group(1)) if isup_match else 0
                if isup_val <= 2:
                    psad_m = re.search(
                        r"PSA\s*density[:\s]*(\d+\.?\d*)", transcript, re.IGNORECASE
                    )
                    if not psad_m:
                        psad_m = re.search(
                            r"PSAD[:\s]*(\d+\.?\d*)", transcript, re.IGNORECASE
                        )
                    psad_v = float(psad_m.group(1)) if psad_m else 0.0
                    if psad_v < 0.10:
                        should_uncertain = True
                        reasons.append(
                            "PI-RADS 3 (equivocal) with ISUP ≤ 2 and PSAD < 0.10"
                        )
                        break

        # Rule 3: PSA in boundary zone (3-4 ng/mL) with no strong features
        psa_match = re.search(r"PSA[:\s]+(\d+\.?\d*)", transcript, re.IGNORECASE)
        if psa_match:
            psa_val = float(psa_match.group(1))
            if 3.0 <= psa_val <= 4.0:
                psad_match = re.search(
                    r"PSA\s*density[:\s]*(\d+\.?\d*)", transcript, re.IGNORECASE
                )
                if not psad_match:
                    psad_match = re.search(
                        r"PSAD[:\s]*(\d+\.?\d*)", transcript, re.IGNORECASE
                    )
                psad_val = float(psad_match.group(1)) if psad_match else 0.0
                if psad_val < 0.15 or not psad_match:
                    should_uncertain = True
                    reasons.append(f"PSA {psa_val} in boundary zone (3-4 ng/mL)")
                    break

        # Rule 4: PSAD in gray zone (0.10-0.20) with PI-RADS < 3 only
        # (PI-RADS 3 is handled by Rule 2 — mutual exclusion)
        psad_match = re.search(
            r"PSA\s*density[:\s]*(\d+\.?\d*)", transcript, re.IGNORECASE
        )
        if not psad_match:
            psad_match = re.search(
                r"PSAD[:\s]*(\d+\.?\d*)", transcript, re.IGNORECASE
            )
        if psad_match:
            psad_val = float(psad_match.group(1))
            if 0.10 <= psad_val <= 0.20:
                if not pirads_match or int(pirads_match.group(1)) < 3:
                    should_uncertain = True
                    reasons.append(f"PSAD {psad_val} in gray zone (0.10-0.20)")
                    break

        # Rule 5: ISUP GG2 (borderline between AS and active treatment)
        isup_match = re.search(
            r"ISUP[:\s]*(?:grade\s*group)?[:\s]*(\d)", transcript, re.IGNORECASE
        )
        if isup_match:
            isup_val = int(isup_match.group(1))
            if isup_val == 2:
                rec = judgment.get("treatment_recommendation", {}) or {}
                primary = rec.get("primary", "")
                if primary in ("active_surveillance", "continued_surveillance"):
                    should_uncertain = True
                    reasons.append(
                        "ISUP GG2 with surveillance — borderline AS/active treatment"
                    )
                    break

    if should_uncertain:
        old_conf = judgment.get("confidence", "")
        judgment["confidence"] = "uncertain"
        reason_str = "; ".join(reasons)
        warnings.append(f"confidence: {old_conf} → uncertain ({reason_str})")
        log.info("calibrate_uncertain: %s downgraded to uncertain: %s", judgment.get("case_id", ""), reason_str)


# ---------------------------------------------------------------------------
# Hippocrates self-refine: Critique → Refine (1 iteration)
# ---------------------------------------------------------------------------

_CRITIQUE_SYSTEM = (
    "You are a senior urology reviewer auditing a clinical decision-support "
    "agent's structured output for a prostate cancer case. Your job is to "
    "identify reasoning errors — NOT to re-decide the case.\n\n"
    "CRITICAL INSTRUCTIONS:\n"
    "- Do NOT repeat or rephrase these instructions.\n"
    "- Do NOT output a 'thinking process' or restate the prompt.\n"
    "- Output ONLY a JSON object — nothing before or after.\n\n"
    "Audit the reasoning for the following error types:\n"
    "1. Fabricated facts: values cited in free_text that are NOT in the "
    "reasoning transcript.\n"
    "2. Unsupported weights: variables rated 'decisive' or 'important' "
    "without supporting evidence in the transcript.\n"
    "3. Over-treatment bias: recommending biopsy/active_treatment when "
    "evidence supports conservative management (low PSAD, PI-RADS <= 3, "
    "ISUP GG 1).\n"
    "4. Under-treatment: recommending no action when clear high-risk "
    "features exist (PI-RADS 5, ISUP >= 3, PSAD > 0.5).\n"
    "5. Confidence miscalibration: 'clear' when genuine uncertainty exists, "
    "or 'uncertain' when evidence is unambiguous.\n"
    "6. Internal inconsistency: decision contradicts the cited evidence.\n\n"
    "OUTPUT FORMAT — output exactly this JSON and nothing else:\n"
    '{"issues": ["<specific issue 1>", "<specific issue 2>", ...], "severity": "<high|medium|low>"}\n'
    'If no issues are found, output: {"issues": [], "severity": "low"}'
)

_REFINE_SYSTEM = (
    "You are revising a clinical decision-support agent's structured output "
    "based on reviewer critique. Produce a SINGLE corrected JSON object "
    "matching the supplied schema EXACTLY. Address every issue raised in "
    "the critique. If the critique says 'NO ISSUES', reproduce the original "
    "judgment unchanged. Do NOT introduce new facts that were not in the "
    "original reasoning transcript. Do NOT wrap in markdown fences. "
    "Do NOT repeat these instructions or output a thinking process."
)


def _run_self_refine(
    model: BaseChatModel,
    parser: PydanticOutputParser,
    task: int,
    case_id: str,
    transcript: str,
    judgment: dict[str, Any],
    skeleton: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Critique → Refine loop (1 iteration).

    Returns the refined judgment if it validates; otherwise returns the
    original judgment unchanged. All issues are logged as warnings.
    """
    judgment_json = json.dumps(judgment, indent=2, ensure_ascii=False)
    transcript_snippet = transcript[:3000] if len(transcript) > 3000 else transcript

    # --- Step 1: Critique ---
    critique_user = (
        f"Case ID: {case_id} (Task {task})\n\n"
        f"Reasoning transcript:\n\"\"\"\n{transcript_snippet}\n\"\"\"\n\n"
        f"Agent's structured judgment:\n{judgment_json}\n\n"
        "Audit this output and output ONLY a JSON object with "
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
        critique_text = critique_resp.content if isinstance(critique_resp.content, str) else json.dumps(critique_resp.content)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"self_refine: critique failed: {type(exc).__name__}: {exc}")
        log.warning("self_refine critique failed for %s: %s", case_id, exc)
        return judgment

    # Parse JSON critique; reuse self_refine's parser for consistency
    from chimera_agent_baseline.agent.self_refine import _parse_critique_json
    critique_clean, critique_issues = _parse_critique_json(critique_text)

    if not critique_issues:
        log.info("self_refine: %s critique found no issues", case_id)
        return judgment

    critique_summary = "; ".join(critique_issues)
    warnings.append(f"self_refine critique: {critique_summary[:300]}")
    log.info("self_refine: %s critique found %d issues:\n%s", case_id, len(critique_issues), critique_summary[:500])

    # --- Step 2: Refine ---
    refine_user = (
        f"Case ID: {case_id} (Task {task})\n\n"
        f"Original reasoning transcript:\n\"\"\"\n{transcript_snippet}\n\"\"\"\n\n"
        f"Original judgment:\n{judgment_json}\n\n"
        "Reviewer critique:\n"
        + "\n".join(f"  {i+1}. {iss}" for i, iss in enumerate(critique_issues))
        + "\n\nProduce the corrected JSON object.\n\n" + skeleton
    )
    try:
        refine_resp = model.invoke(
            [
                SystemMessage(content=_REFINE_SYSTEM),
                HumanMessage(content=refine_user),
            ]
        )
        raw = refine_resp.content if isinstance(refine_resp.content, str) else json.dumps(refine_resp.content)
        refined = parser.parse(_extract_json_object(raw))
        refined_dict = refined.model_dump(mode="json")
        warnings.append("self_refine: judgment refined (1 iteration)")
        log.info("self_refine: %s judgment refined successfully", case_id)
        return refined_dict
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"self_refine: refine failed, keeping original: {type(exc).__name__}: {exc}")
        log.warning("self_refine refine failed for %s: %s", case_id, exc)
        return judgment


# ---------------------------------------------------------------------------
# Atomic fact verification (programmatic, no LLM)
# ---------------------------------------------------------------------------

# Numeric value patterns to extract from free_text and check against transcript
_VALUE_PATTERNS = [
    (r"PSA[:\s]+(\d+\.?\d*)", "PSA"),
    (r"PI-?RADS[:\s]*(\d)", "PI-RADS"),
    (r"ISUP[:\s]*(?:grade\s*group)?[:\s]*(\d)", "ISUP"),
    (r"Gleason[:\s]*(\d)\s*\+\s*(\d)", "Gleason"),
    (r"PSA\s*density[:\s]*(\d+\.?\d*)", "PSA density"),
]


# ---------------------------------------------------------------------------
# Programmatic case fields — not produced by the LLM.
# ---------------------------------------------------------------------------


def _called_tools_in_order(messages: list) -> list[str]:
    """Unique tool names in first-call order, derived from ``ToolMessage``s."""
    seen: set[str] = set()
    order: list[str] = []
    for m in messages:
        if isinstance(m, ToolMessage) and m.name and m.name not in seen:
            seen.add(m.name)
            order.append(m.name)
    return order


def _build_reveal_sequence(
    tool_order: list[str],
    variable_weights: dict[str, str] | None = None,
    task: int | None = None,
    patient_data: dict[str, Any] | None = None,
    free_text: str = "",
) -> list[dict[str, Any]]:
    """Build the ``reveal_sequence`` — from tool calls AND variable weights.

    Primary source: tool call order (agent explicitly revealed sections).
    Secondary source (P0-E): variable_weights — if a variable is rated
    'decisive' or 'important', the agent implicitly used the section
    containing that variable's data. This generates reveal entries for
    sections that were used but not explicitly called as tools.

    Task 3 fallback (P0-A fix): T3 schema has no ``variable_weights`` field,
    so the secondary source never fires. For T3, we infer used sections from
    the patient input data (psa/psad/pirads/dre/vol/fh/bx etc.) — any
    non-None/non-empty value indicates the section containing it was used.

    Timestamps are synthetic (evenly spaced from "now"): the agent runtime
    does not currently record the wall-clock time of each tool call.
    """
    base_ts = datetime.now(timezone.utc)
    sequence: list[dict[str, Any]] = []
    existing_keys: set[str] = set()

    def _add_entry(key: str, label: str) -> None:
        if key in existing_keys:
            return
        order = len(sequence) + 1
        ts = base_ts + timedelta(seconds=2 * (order - 1))
        sequence.append(
            {
                "page": "decision",
                "order": order,
                "key": key,
                "label": label,
                "value": "section",
                "via": "tool_call",
                "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            }
        )
        existing_keys.add(key)

    # --- Primary: tool call order ---
    for tool_name in tool_order:
        key, label = reveal_info_for_tool(tool_name)
        _add_entry(key, label)

    # Maps variable_weights keys to their primary section (from
    # ground_truth/section_variable_mapping.json).
    _VAR_TO_SECTION: dict[str, tuple[str, str]] = {
        "pirads": ("section_s3-mri", "Radiology / MRI report"),
        "psad": ("section_s3-mri", "Radiology / MRI report"),
        "psa": ("section_s3-psa", "PSA trend"),
        "dre": ("section_s3-labs", "Laboratory results"),
        "cspca": ("section_s3-mri", "Radiology / MRI report"),
        "vol": ("section_s3-mri", "Radiology / MRI report"),
        "fh": ("section_s3-fh", "Family history (anamnesis)"),
        "bx": ("section_s3-path", "Pathology report"),
        "bx_isup": ("section_s3-path", "Pathology report"),
        "bx_gl_prim": ("section_s3-path", "Pathology report"),
        "bx_gl_sec": ("section_s3-path", "Pathology report"),
        "ct": ("section_s3-mri", "Radiology / MRI report"),
        "comorbidity": ("section_s3-comorb", "Comorbidities"),
        # age is always_available — no section needed
    }

    # --- Secondary: variable weights (P0-E inline reveal_sequence) ---
    if variable_weights:
        for var, weight in variable_weights.items():
            if weight not in ("decisive", "important"):
                continue
            section_info = _VAR_TO_SECTION.get(var)
            if section_info:
                _add_entry(section_info[0], section_info[1])

    # --- T3 fallback: infer sections from patient input data (P0-A fix) ---
    # T3 schema has no variable_weights, so we scan the structured-prompt
    # patient data for non-None values and map them to their source section.
    if task == 3 and not sequence and patient_data:
        ft_lower = free_text.lower()
        for var, (sec_key, sec_label) in _VAR_TO_SECTION.items():
            val = patient_data.get(var)
            if val is None:
                continue
            # Skip empty strings / empty lists
            if isinstance(val, str) and not val.strip():
                continue
            if isinstance(val, (list, dict)) and not val:
                continue
            # Only add if free_text references this variable (agent actually used it)
            # This avoids over-revealing sections the agent didn't consider.
            var_keywords = {
                "pirads": ["pirads", "pi-rads", "pi_rads"],
                "psad": ["psad", "psa density", "psa-density"],
                "psa": ["psa"],
                "dre": ["dre", "rectal", "t-stage", "tstage", "ct2"],
                "cspca": ["cspca", "cancer probability"],
                "vol": ["volume", "prostate volume"],
                "fh": ["family history", "familial"],
                "bx": ["biopsy", "gleason", "isup"],
                "bx_isup": ["isup", "grade group"],
                "bx_gl_prim": ["gleason", "primary pattern"],
                "bx_gl_sec": ["gleason", "secondary pattern"],
                "ct": ["clinical t", "ct2", "ct3", "t-stage"],
                "comorbidity": ["comorbid", "charlson", "medhx", "medical history"],
            }
            keywords = var_keywords.get(var, [var])
            if any(kw in ft_lower for kw in keywords):
                _add_entry(sec_key, sec_label)

    return sequence


def _build_patient(case_id: str, state: dict[str, Any]) -> dict[str, Any]:
    patient_state = state.get("patient") or {}
    return {
        "id": case_id,
        "psa_ng_ml": patient_state.get("psa"),
        "age_years": patient_state.get("age"),
    }


# ---------------------------------------------------------------------------
# Prompt builders + helpers
# ---------------------------------------------------------------------------


def _user_prompt(
    case_id: str,
    task: int,
    transcript: str,
    called_tools: list[str],
    eligible: list[str],
) -> str:
    tools_line = ", ".join(called_tools) if called_tools else "(no tools called)"
    head = (
        f"Case ID: {case_id}\n"
        f"Task: {task}\n\n"
        "Your reasoning transcript (final assistant message from the ReAct "
        'loop):\n"""\n'
        f"{transcript}\n"
        '"""\n\n'
        f"Tools you called during the ReAct loop: {tools_line}\n\n"
    )
    if task not in VARIABLES_BY_TASK:
        return head + (
            "Now fill out the form for biochemical recurrence (BCR) prediction.\n\n"
            "KEY DEFINITIONS:\n"
            "- event = 1: you predict BCR WILL occur during follow-up.\n"
            "- event = 0: you predict NO BCR by last follow-up (censored).\n"
            "- months_to_recurrence: the TIME from treatment to BCR (if event=1) "
            "OR time from treatment to last follow-up (if event=0). "
            "This is ALWAYS a positive number (typically 6-60 months). "
            "Do NOT output 0 — even if PSA is high now, months represents "
            "projected time to event, not current status.\n\n"
            "BCR thresholds (for reference):\n"
            "- Post-RP: PSA >= 0.2 ng/mL (confirmed)\n"
            "- Post-RT: PSA nadir + 2 ng/mL (Phoenix)\n"
            "- A high PSA at baseline (pre-treatment) does NOT mean recurrence.\n"
            "  Recurrence = PSA RISES after treatment to threshold.\n"
            "  NOTE: These thresholds are for POST-treatment PSA. The PSA in patient data is BASELINE (pre-treatment).\n"
            "  Do NOT conclude event=1 solely because baseline PSA > 0.2.\n\n"
            "If the patient has NOT yet been treated or PSA is the pre-treatment "
            "value, consider whether recurrence prediction is applicable.\n\n"
            "Give a focused reasoning naming the 2-4 factors that most influenced "
            "your estimate (PSA trajectory, treatment modality, pathology grade, "
            "stage, margins, etc.)."
        )
    return head + (
        "Variables you may weight (you may ONLY weight these — every other "
        "variable was either out of scope for this task or behind a tool you "
        "did not call):\n"
        f"  {', '.join(eligible) if eligible else '(none)'}\n\n"
        "Now fill out the form. Weight each variable (not_used / noted / "
        "important / decisive); give an overall confidence; and a focused "
        "reasoning naming the 2-4 factors that most influenced your "
        "recommendation."
    )


def _build_skeleton_instructions(task: int, eligible: list[str]) -> str:
    """Concrete JSON-skeleton format instructions.

    PydanticOutputParser's ``get_format_instructions()`` dumps the full
    JSON schema (with ``$defs``, ``$ref``, ``additionalProperties``,
    etc.). Small models tested in the wild (Gemma 4 E2B) sometimes echo
    that schema back verbatim instead of producing an instance. A
    concrete shape with placeholder values is far more robust and is
    still unambiguous about which keys are required.
    """
    if task == 3:
        skeleton = "\n".join(
            [
                "{",
                '  "event": <0 | 1 — 1 if you predict biochemical recurrence will occur, '
                "0 if censored / no recurrence by last follow-up>,",
                '  "months_to_recurrence": <number 6-60 — projected months from treatment to '
                "BCR (event=1) or to last follow-up (event=0). NEVER 0.>,",
                '  "repeat_test": <"<a short description of the recommended follow-up test>" | null>,',
                '  "free_text": "<at least 40 chars; the evidence and the 2-4 factors that drove your estimate>"',
                "}",
            ]
        )
        return (
            "Output a SINGLE JSON object matching exactly this shape (replace "
            "every <...> with a real value, or JSON null where indicated):\n\n"
            f"{skeleton}\n\n"
            "Do NOT emit any other keys. Do NOT wrap in markdown fences. Do NOT "
            "echo the schema. Do NOT use // comments — JSON does not support "
            "comments. Do NOT copy <...> placeholders verbatim — replace them "
            "with real values."
        )

    weight_enum = '"not_used" | "noted" | "important" | "decisive"'
    confidence_enum = '"clear" | "borderline" | "uncertain"'

    weight_lines = [f'    "{var}": <{weight_enum}>,' for var in eligible]
    if weight_lines:
        weight_lines[-1] = weight_lines[-1].rstrip(",")

    if task == 1:
        decision_lines = ['  "biopsy_decision": <"yes" | "no">,']
    else:
        action_enum = '"active_surveillance" | "continued_surveillance" | "watchful_waiting" | "active_treatment"'
        decision_lines = [
            '  "treatment_recommendation": {',
            f'    "primary": <{action_enum}>,',
            '    "modalities": [<specific modality strings, or empty list>],',
            '    "detail": <"<free-text detail>" | null>,',
            '    "as_protocol": <"<surveillance protocol description>" | null — only when primary is a '
            "surveillance action, else null>,",
            '    "as_trigger": <"<trigger for escalation>" | null — only when primary is a surveillance '
            "action, else null>",
            "  },",
        ]

    skeleton = "\n".join(
        [
            "{",
            *decision_lines,
            f'  "confidence": <{confidence_enum}>,',
            '  "variable_weights": {',
            *weight_lines,
            "  },",
            '  "repeat_test": <"<a short description of the recommended follow-up>" | null>,',
            '  "free_text": "<at least 40 chars; name the 2-4 factors that drove your call>"',
            "}",
        ]
    )

    return (
        "Output a SINGLE JSON object matching exactly this shape (replace "
        "every <...> with a real value, or JSON null where indicated):\n\n"
        f"{skeleton}\n\n"
        f"`variable_weights` MUST include all and only these keys: {', '.join(eligible)}.\n"
        "Use the weight 'not_used' for any variable that did not influence your "
        "decision. Do NOT emit any other keys at the top level. Do NOT wrap in "
        "markdown fences. Do NOT echo the schema. Do NOT use // comments — JSON "
        "does not support comments. Do NOT copy <...> placeholders verbatim — "
        "replace them with real values."
    )


def _extract_json_object(text: str) -> str:
    """Best-effort extraction of a JSON object from raw model output.

    Handles three failure modes observed in the wild:
    1. CoT prose containing { } before the real JSON.
    2. { } inside string values (e.g. free_text: "grade group {1}").
    3. Truncated output where no matching } exists.
    """
    if not text:
        return text

    # 1. Prefer markdown fence if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)

    # 2. Collect every balanced {…} candidate (skips braces inside strings).
    candidates: list[str] = []
    for i, ch in enumerate(text):
        if ch == "{":
            obj = _balanced_extract(text, i)
            if obj:
                candidates.append(obj)

    if not candidates:
        return text  # no { at all — let parser raise the error

    # 3. From last to first, return the first that json.loads successfully.
    for c in reversed(candidates):
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

    # 4. All candidates failed to parse — return the last (most likely target).
    return candidates[-1]


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
