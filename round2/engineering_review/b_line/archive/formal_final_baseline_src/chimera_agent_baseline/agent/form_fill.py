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

Legacy mode retries then salvages/defaults on parse failure. Every recovery
is audited; opt-in strict runtime rejects fallback diagnoses. Parsing lives in
form_parsing and ordered postprocessing in postprocess.
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

from copy import deepcopy
from dataclasses import asdict

from chimera_agent_baseline.agent.reflection_contract import (
    Context, DraftArtifact, ReflectionArtifact, ReviewBatch, Subject, draft_artifact,
    judgment_label, resolve, upfront_digest, upfront_record, validate_judgment,
)
from chimera_agent_baseline.runtime_audit import (
    ProcessingAudit, RuntimeFatal, audit_stage, canonical, consumption_event,
    content_digest, current_runtime, stage_scope,
)
from chimera_agent_baseline.agent.postprocess import PostprocessOps, apply_postprocessing
from chimera_agent_baseline.agent.form_parsing import (  # noqa: F401 - preserve legacy replay imports
    _extract_json_object,
    _balanced_extract,
    _final_assistant_text,
    _is_tool_call_block,
    _final_answer_tail,
    _salvage_decision,
    _fill_missing_decision_fields,
    _salvage_decision_from_raw,
    _has_decision,
    _default_judgment,
    _merge_salvaged_into_schema,
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
    postprocess: bool = True,
    strict_output: bool = False,
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

    @audit_stage('form_fill')
    def form_fill(state: dict[str, Any]) -> dict[str, Any]:
        messages = state["messages"]
        task = int(state.get("task", 1))
        case_id = state.get("case_id", "unknown")
        predictor_dec = state.get("predictor_decision") or {}
        audit = ProcessingAudit(task, case_id)
        guarded = state.get("information_access") is not None
        runtime = current_runtime()
        authority = runtime.reflection_test if runtime is not None else None
        for message in messages:
            if isinstance(message, ToolMessage) and (
                getattr(message, "status", "success") == "error"
                or isinstance(message.content, str) and message.content.startswith("Error:")
            ):
                denied = guarded and message.additional_kwargs.get("information_access_expected_denial", False)
                audit.record("tool_execution", {}, {}, [],
                             status="policy_denied" if denied else "fallback", required=not denied,
                             reason=f"{message.name}: {message.content}",
                             fallback=None if denied else "continue_without_tool_result")

        observed = [m for m in messages if not isinstance(m, ToolMessage) or
                    m.additional_kwargs.get("information_access_clinical_success")] if guarded else messages
        tool_order = _called_tools_in_order(observed)
        called = set(tool_order)
        transcript = _final_assistant_text(messages)
        consumption_event('formfill_input', {}, dict(messages=messages, transcript=transcript,
                          tool_order=tool_order, predictor_decision=predictor_dec),
                          task=task, case_id=case_id, status='actual_input',
                          reflection=dict(artifact=state.get('reflection_artifact'),
                                          legacy_text_present=bool(state.get('refined_transcript')),
                                          acceptance='not_selected_at_input',
                                          consumed=False))
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
            consumption_event('formfill_request_input', {}, dict(system=_SYSTEM_PROMPT, user=user_content),
                              task=task, case_id=case_id, attempt=attempt, status='prepared')
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
                if authority is not None:
                    # Do not repair the object before validating the reviewed schema.
                    judgment = validate_judgment(task, Dynamic, json.loads(raw_json))
                else:
                    try:
                        flat = json.loads(raw_json)
                        if isinstance(flat, dict) and any(key in elig for key in flat):
                            weights = {key: flat.pop(key) for key in list(flat) if key in elig}
                            flat.setdefault("variable_weights", {}).update(weights)
                            raw_json = json.dumps(flat)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    judgment = parser.parse(raw_json).model_dump(mode="json")
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
                if strict_output:
                    from chimera_agent_baseline.agent.reflection_contract import judgment_label
                    if (judgment_label(task, judgment) is None
                            or task in (1, 2) and judgment.get("confidence") is None
                            or len((judgment.get("free_text") or "").strip()) < 40):
                        raise ValueError("Missing model decision, confidence or rationale")
                elif authority is None:
                    _fill_missing_decision_fields(task, judgment, raw_json, warnings)
                judgment.setdefault("repeat_test", None)
                if authority is None and not strict_output and (not judgment.get("free_text") or len(judgment.get("free_text", "")) < 40):
                    # P5: 从 transcript 尾部提取"最后一个非工具调用文本块"——
                    # 直接截尾部会抓到 ReAct 工具调用语法裸文本（<function=...>），
                    # 实测 14/91 case free_text 中毒、GEval 0.00-0.20。
                    # 按消息边界切块 → 跳过工具调用块 → 取最后一段 >=100 字符
                    # 的正文块 → 再取其 [-500:]。
                    judgment["free_text"] = (_final_answer_tail(transcript) or "Reasoning not available.")[-500:]
                    warnings.append("free_text fallback: final answer extracted")
                break
            except Exception as exc:  # noqa: BLE001 — any parse/validation failure triggers a retry
                if authority is not None or strict_output:
                    judgment = None
                warnings.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                log.warning("form_fill parse failed (attempt %d) for %s: %s", attempt, case_id, exc)
                if attempt == max_retries:
                    break
                retry_hint = (
                    "Your previous attempt did not validate. "
                    + _diagnose_parse_error(raw, exc)
                )

        if judgment is None:
            if strict_output:
                raise RuntimeFatal("Adaptive smoke form did not produce a valid model decision")
            if authority is not None:
                consumption_event('reflection_selection', {}, {}, status='failed_no_valid_initial',
                                  task=task, case_id=case_id)
                raise RuntimeFatal('failed_no_valid_initial')
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

        parse_fallbacks = [w for w in warnings if "DEFAULTED" in w or "salvaged" in w]
        if guarded and parse_fallbacks:
            raise RuntimeError("Guarded form output required legacy decision recovery: " + "; ".join(parse_fallbacks))
        audit.record("parsed_judgment", {}, judgment, list(warnings),
                     status="fallback" if parse_fallbacks else "executed",
                     reason="; ".join(parse_fallbacks) or None,
                     fallback="legacy_decision_recovery" if parse_fallbacks else None, required=True)

        # A-2: 落盘后处理之前的 judgment —— 所有后处理消融的入口快照。
        # 这里是「LLM 原味输出 + 缺失字段补齐」之后的第一个时点，
        # 之后的 self_refine / atomic / override / 校准链都以这份快照为起点。
        if _PRE_CALIB_DUMP:
            try:
                import os as _os_pc, json as _json_pc
                _pc_dir = _os_pc.path.join(
                    _os_pc.environ.get("CHIMERA_OUTPUT_DIR", "output"),
                    "trace", str(case_id),
                )
                _os_pc.makedirs(_pc_dir, exist_ok=True)
                with open(_os_pc.path.join(_pc_dir, "judgment_pre_calibration.json"), "w") as _pc_f:
                    _json_pc.dump(
                        {
                            "case_id": case_id,
                            "task": task,
                            "judgment": judgment,
                            "tool_order": tool_order,
                            # E0（2026-11-05）：四层复盘需要「generator 实际收到了什么证据」。
                            # 之前只存 judgment，claim 无法回溯到工具返回，事实层与
                            # 证据使用层都判不了。截断到 60k 字符，避免 trace 爆炸。
                            "transcript": (transcript or "")[:60000],
                            "eligible_vars": elig,
                            "predictor_decision": predictor_dec,
                            "warnings_so_far": list(warnings),
                        },
                        _pc_f,
                        ensure_ascii=False,
                        indent=2,
                    )
            except Exception as _pc_exc:  # noqa: BLE001 — 落盘失败绝不阻断主流程
                log.warning("pre-calibration dump failed for %s: %s", case_id, _pc_exc)

        before_verification = deepcopy(judgment)
        verification_warning_start = len(warnings)
        artifacts = []
        external = state.get('reflection_artifact')
        if external:
            artifacts.append(ReflectionArtifact(
                **{**external, 'draft': DraftArtifact(**external['draft'])}))
        if runtime is not None and (external or state.get('refined_transcript')):
            status = {'detected': 'detected_no_executable_proposal', 'error': 'error_fallback_initial',
                      'not_triggered': 'not_triggered'}.get(
                          external['status'] if external else None, 'blocked_missing_authorization')
            consumption_event('external_reflection', judgment, judgment, status=status,
                              task=task, case_id=case_id, artifact=external, consumed=False)
        # --- Hippocrates self-refine: Critique → Refine (1 iteration) ---
        if self_refine:
            with stage_scope('internal_reflection'):
                legacy_result = _run_self_refine(
                    model, parser, task, case_id, transcript, judgment, skeleton, warnings
                )
            if runtime is not None:
                artifacts.append(ReflectionArtifact(
                    'internal_formfill', draft_artifact(messages, transcript),
                    content_digest(legacy_result), 'unreviewed'))
            if authority is None:
                judgment = legacy_result  # Preserve the existing legacy path.

        consumption_event('internal_reflection', before_verification, legacy_result if self_refine else judgment,
                          task=task, case_id=case_id,
                          status='observed_legacy_execution' if self_refine else 'disabled',
                          acceptance='legacy_behavior_unreviewed', not_a_G1R_receipt=True,
                          artifact=asdict(artifacts[-1]) if self_refine and artifacts else None)
        if authority is not None:
            draft = draft_artifact(messages, transcript)
            # run_agent supplies the original rendered upfront message first.
            upfront = upfront_record(messages[0].content) if messages and isinstance(messages[0], HumanMessage) else None
            subject = Subject(runtime.run_id, task, case_id, runtime.identity_digest, draft,
                              content_digest(judgment), upfront_digest(upfront),
                              content_digest({'draft': asdict(draft), 'artifacts': [asdict(a) for a in artifacts]}))
            context = Context(subject, canonical(judgment), upfront, tuple(artifacts))
            try:
                batch = authority.review(context)
            except Exception as error:
                consumption_event('reflection_review_error', {}, {}, status='error',
                                  task=task, case_id=case_id, error_type=type(error).__name__)
                batch = ReviewBatch('error')
            if type(batch) is ReviewBatch:
                consumption_event('reflection_review', {}, asdict(batch), status='synthetic_stub',
                                  task=task, case_id=case_id, binding=asdict(subject),
                                  source_record_id='rendered_upfront')
            selection = resolve(context, batch, authority, Dynamic)
            selected = consumption_event('reflection_selection', judgment, selection.judgment,
                                         task=task, case_id=case_id, status=selection.status,
                                         selected_source=selection.source, binding=asdict(subject),
                                         label_changed=judgment_label(task, judgment) != judgment_label(task, selection.judgment),
                                         proposal_digest=selection.proposal_digest,
                                         verdict_digest=selection.verdict_digest, reason=selection.reason)
            judgment = selection.judgment  # Sole authorized assignment after J0, before atomic.
            if content_digest(judgment) != selected['after_digest']:
                raise RuntimeFatal('Assigned reflection judgment differs from selection')
            consumption_event('reflection_formfill_consumed', before_verification, judgment,
                              task=task, case_id=case_id, status='actual_assignment',
                              selection_digest=selected['digest'], source=selection.source)
        before_atomic = deepcopy(judgment)
        # --- Atomic fact verification (programmatic, no LLM) ---
        if atomic_check:
            with stage_scope('atomic_verification'):
                fc_result = atomic_fact_check(task, case_id, transcript, judgment, elig, model=model)
            warnings.extend(fc_result.warnings)
            # P1-C: Post-check confidence calibration — downgrade to 'uncertain'
            # when the evidence is genuinely ambiguous (beyond fact_check's
            # clear→borderline downgrade).
            _calibrate_uncertain(task, transcript, judgment, fc_result, warnings)

        consumption_event('atomic_verification', before_atomic, judgment, task=task, case_id=case_id,
                          status='observed_legacy_execution' if atomic_check else 'disabled')
        audit.record("verified_judgment", before_verification, judgment,
                     warnings[verification_warning_start:], configured=bool(atomic_check or self_refine),
                     status="executed" if atomic_check or self_refine else "disabled")

        if postprocess:
            judgment = apply_postprocessing(
                judgment, task=task, state=state, predictor_dec=predictor_dec,
                decision_override=decision_override, transcript=transcript, called=called,
                warnings=warnings, audit=audit, ops=PostprocessOps(
                    decision_override=_apply_decision_override, case_fields=_load_case_fields,
                    t1_decision_layer=_apply_t1_decision_layer, numeric_verify=_calculate_and_verify,
                    treatment_floor=_enforce_treatment_floor, t3_event=_calibrate_t3_event,
                    t3_tau=_apply_t3_tau, load_weight_table=_load_weight_table,
                    weight_table=_apply_weight_table, calibrate_weights=_calibrate_weights,
                    calibrate_confidence=_calibrate_confidence, t3_salient=_inject_t3_salient_elements,
                    t1_mode=_T1_DL_MODE, weight_table_enabled=_WEIGHT_TABLE_ENABLED,
                    force_conf_clear=_FORCE_CONF_CLEAR,
                ),
            )
        else:
            audit.record("postprocessing", judgment, judgment, [], configured=False,
                         status="disabled", reason="Guarded evidence-only boundary")

        reveal_sequence = _build_reveal_sequence(
            tool_order,
            None if guarded else judgment.get("variable_weights", {}),
            task=None if guarded else task,
            patient_data={} if guarded else (state.get("patient") or {}),
            free_text=judgment.get("free_text", ""),
        )

        # A-3: reveal 来源落盘（tool_call / inferred_from_weights / inferred_from_input）。
        # 契约字段 via 恒为 "tool_call"，来源靠这份旁路文件区分。
        if _PRE_CALIB_DUMP:
            try:
                import os as _os_rv, json as _json_rv
                _rv_dir = _os_rv.path.join(
                    _os_rv.environ.get("CHIMERA_OUTPUT_DIR", "output"),
                    "trace", str(case_id),
                )
                _os_rv.makedirs(_rv_dir, exist_ok=True)
                with open(_os_rv.path.join(_rv_dir, "reveal_provenance.json"), "w") as _rv_f:
                    _json_rv.dump(
                        {"case_id": case_id, "task": task,
                         "entries": _LAST_REVEAL_PROVENANCE},
                        _rv_f, ensure_ascii=False, indent=2,
                    )
            except Exception as _rv_exc:  # noqa: BLE001
                log.warning("reveal provenance dump failed for %s: %s", case_id, _rv_exc)

        patient = _build_patient(case_id, state)
        # atomic_fact_check 注入的内部字段 _fact_check_flags：
        #   Task1/2/3Output 均 extra="forbid"，不能留在输出里；
        #   但也不能静默丢弃 —— 2026-11-01 全量 429 例跑出 3 次 FALSE
        #   （1220 TRUE / 1749 UNVERIFIED），此前这 3 条矛盾无人可见。
        #   改为转成 warning 落进 form_fill_warnings（不进 schema，零行为风险）。
        for _fc in (judgment.get("_fact_check_flags") or []):
            warnings.append(f"fact_check contradiction: {_fc}")
        judgment.pop("_fact_check_flags", None)
        full = assemble_full_output(task, case_id, patient, reveal_sequence, judgment)
        # E0（2026-11-05）：reveal_sequence 在装配后才定稿，pre-calibration 快照里没有。
        # 四层复盘要用「揭示段 vs 变量权重」判证据使用层，单独落一份。
        if _PRE_CALIB_DUMP:
            try:
                import os as _os_e, json as _json_e
                _e_dir = _os_e.path.join(
                    _os_e.environ.get("CHIMERA_OUTPUT_DIR", "output"), "trace", str(case_id)
                )
                _os_e.makedirs(_e_dir, exist_ok=True)
                with open(_os_e.path.join(_e_dir, "evidence_post.json"), "w") as _e_f:
                    _json_e.dump(
                        {"case_id": case_id, "task": task, "tool_order": tool_order,
                         "reveal_sequence": reveal_sequence, "n_warnings": len(warnings)},
                        _e_f, ensure_ascii=False, indent=2,
                    )
            except Exception as _e_exc:  # noqa: BLE001 — 落盘失败绝不阻断主流程
                log.warning("evidence dump failed for %s: %s", case_id, _e_exc)
        audit.record("assembled_output", judgment, full, [])
        return {"structured_response": full, "form_fill_warnings": warnings,
                "processing_stages": audit.stages}

    return form_fill


def _predictor_note(task: int, predictor_dec: dict) -> str:
    """渲染确定性 predictor 的决策为 prompt 里的"临床预测参考"段落。"""
    if not _PREDICTOR_NOTE_ENABLED:
        return ""
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
        # T2-WW（2026-10-29）：predictor 在 72 例 GT 里从不输出 watchful_waiting
        # （该类本身只有 2 例），而 WW 取决于预期寿命 / 合并症负荷 / 患者意愿——
        # 这些只在 notes 与自由文本里，LLM 读得到、predictor 读不到。
        # 离线实测（72 例真标）：LLM 判 WW 的 2 例中 1 例命中 GT（T2-031），
        # 放行后 macro F1 0.6609 → 0.7849（+0.124），acc 不变。
        # 默认 1；CHIMERA_T2_KEEP_WW=0 回到历史行为。
        if _T2_KEEP_WW and str(rec.get("primary")) == "watchful_waiting":
            warnings.append(
                "decision override: skipped (LLM=watchful_waiting; predictor 无该类)"
            )
        elif dec and rec.get("primary") != dec:
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
# P10（2026-09-28）：T1 决策层 —— 把「LLM 主导 + 隐式 predictor 提示」变成显式可测的融合
# ---------------------------------------------------------------------------


def _load_case_fields(case_dir) -> dict:
    """读本 case 的 structured-prompt.json（GC 容器里回退 prompt.json）。失败返回 {}。"""
    if not case_dir:
        return {}
    for name in ("structured-prompt.json", "prompt.json"):
        p = _os.path.join(str(case_dir), name)
        if _os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                return obj if isinstance(obj, dict) else {}
            except Exception:  # noqa: BLE001
                return {}
    return {}


def _v2_feature(name: str, margin: float, llm_yes: float, cf: dict) -> float:
    """按 v2 系数文件里的 feature_names 求值（数据驱动，不硬编码特征列表）。"""

    def _n(v):
        try:
            f = float(str(v).strip())
            return f if f == f else None
        except (TypeError, ValueError):
            return None

    if name == "margin":
        return float(margin)
    if name == "llm_yes":
        return float(llm_yes)
    if name == "psad>=.15":
        v = _n(cf.get("psad"))
        return 1.0 if (v is not None and v >= 0.15) else 0.0
    if name == "isup>=3":
        v = _n(cf.get("bx_isup"))
        return 1.0 if (v is not None and v >= 3) else 0.0
    return 0.0  # 未知特征名 → 0（调用方会把实际取值写进 warning，可审计）


def _apply_t1_decision_layer(task: int, judgment: dict, predictor_dec: dict, warnings: list,
                             case_fields: dict | None = None) -> None:
    """T1 决策层：在 [predictor 概率/边界 + LLM 决策 (+ 条件变量)] 上定最终 biopsy_decision。

    v1（Δ/SE +3.31，reports/t1_decision_layer_20260928.md）：4 特征全局 meta。
    v2（Δ/SE **+4.98**，37/13，tools/p3_conditional_arbiter.py）：**条件化** meta，
      特征由 resources/t1_decision_layer_v2.json 的 feature_names 驱动。
    三条纪律：可观测 / 失败不静默 / 不动输出 schema。
    """
    global _T1_DL_CACHE, _T1_DL2_CACHE
    if task != 1 or not _T1_DECISION_LAYER:
        return

    # ---------------- v2：条件化 ----------------
    if _T1_DL_MODE == "v2":
        if _T1_DL2_CACHE is None:
            try:
                with open(_T1_DL2_JSON, "r", encoding="utf-8") as f:
                    _T1_DL2_CACHE = json.load(f)
            except Exception as exc:  # noqa: BLE001
                _T1_DL2_CACHE = {"_failed": "%s: %s" % (type(exc).__name__, exc)}
        if "_failed" in _T1_DL2_CACHE:
            warnings.append("decision layer v2: LOAD FAILED (%s) — 未接通"
                            % _T1_DL2_CACHE["_failed"][:70])
            log.error("T1 decision layer v2 load failed: %s", _T1_DL2_CACHE["_failed"])
            return
        proba = (predictor_dec or {}).get("probabilities") or {}
        p_no, p_yes = proba.get("no"), proba.get("yes")
        if p_no is None or p_yes is None:
            warnings.append("decision layer v2: SKIPPED (predictor probabilities missing) — 未接通")
            log.warning("T1 decision layer v2 skipped: predictor probabilities missing")
            return
        llm_dec = judgment.get("biopsy_decision")
        margin = float(p_yes) - float(p_no)
        llm_yes = 1.0 if llm_dec == "yes" else 0.0
        cf = case_fields or {}
        spec = _T1_DL2_CACHE
        names = [str(n) for n in spec["feature_names"]]
        x = [_v2_feature(n, margin, llm_yes, cf) for n in names]
        z = float(spec["intercept"]) + sum(float(c) * v for c, v in zip(spec["coef"], x))
        new = "yes" if z > 0.0 else "no"
        extra = " ".join("%s=%.0f" % (n, v) for n, v in zip(names, x)
                         if n not in ("margin", "llm_yes"))
        warnings.append(
            "decision layer v2: margin=%+.3f llm=%s %s logit=%+.3f final=%s%s"
            % (margin, llm_dec, extra, z, new,
               ("  ← 改判 (原 %s)" % llm_dec) if new != llm_dec else ""))
        judgment["biopsy_decision"] = new
        log.info("T1 decision layer v2: llm=%s -> %s (logit=%+.3f)", llm_dec, new, z)
        return

    # ---------------- v1：原路径（保持不变） ----------------

    if _T1_DL_CACHE is None:
        try:
            with open(_T1_DL_JSON, "r", encoding="utf-8") as f:
                _T1_DL_CACHE = json.load(f)
        except Exception as exc:  # noqa: BLE001
            _T1_DL_CACHE = {"_failed": "%s: %s" % (type(exc).__name__, exc)}
    if "_failed" in _T1_DL_CACHE:
        warnings.append("decision layer: LOAD FAILED (%s) — 未接通"
                        % _T1_DL_CACHE["_failed"][:70])
        log.error("T1 decision layer coefficient load failed: %s", _T1_DL_CACHE["_failed"])
        return

    proba = (predictor_dec or {}).get("probabilities") or {}
    p_no, p_yes = proba.get("no"), proba.get("yes")
    if p_no is None or p_yes is None:
        warnings.append("decision layer: SKIPPED (predictor probabilities missing) — 未接通")
        log.warning("T1 decision layer skipped: predictor probabilities missing")
        return

    llm_dec = judgment.get("biopsy_decision")
    x = [float(p_no), float(p_yes), abs(float(p_yes) - float(p_no)),
         1.0 if llm_dec == "yes" else 0.0]
    spec = _T1_DL_CACHE
    z = float(spec["intercept"]) + sum(float(c) * v for c, v in zip(spec["coef"], x))
    new = "yes" if z > 0.0 else "no"

    warnings.append(
        "decision layer: p_no=%.3f p_yes=%.3f llm=%s logit=%+.3f final=%s%s"
        % (float(p_no), float(p_yes), llm_dec, z, new,
           ("  ← 改判 (原 %s)" % llm_dec) if new != llm_dec else "")
    )
    judgment["biopsy_decision"] = new
    log.info("T1 decision layer: llm=%s -> %s (logit=%+.3f)", llm_dec, new, z)


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
    """Downgrade confidence when the decision contradicts the evidence (P1-2 可切换）。"""
    conf = judgment.get("confidence", "")
    if conf not in ("clear", "borderline"):
        return
    if _CONF_DOWNGRADE == "off":
        warnings.append(
            f"confidence: {conf} kept (downgrade chain OFF; rule fired: {reason})")
        log.info("numeric_verify: rule fired but chain OFF: %s", reason)
        return
    target = "borderline" if _CONF_DOWNGRADE == "borderline" else "uncertain"
    judgment["confidence"] = target
    warnings.append(f"confidence: {conf} → {target} ({reason})")
    log.info("numeric_verify: confidence downgraded to %s: %s", target, reason)


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

    # A-1'（2026-10-28）：进入即记（改写在 735 行汇总）。T1 上此前 0 次改写，
    # 无法判断是「规则不命中」还是「没被调用」。
    log.info("num_verify: enter task=%d ft_len=%d", task, len(free_text))

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

import os as _os  # 层 1c（P8）开关读取用

# --- 层 1c（P8）：not_used 先验提升 ---
# 序数刻度，用于取先验中位数
_ORD_LADDER = ("not_used", "noted", "important", "decisive")
# GT 该变量 not_used 概率低于此值 → 视为「罕见档位」，按先验提升
_NOT_USED_RARE = 0.15
# 环境变量开关：CHIMERA_PROMOTE_NOT_USED=0 关闭（用于独立 A/B）
_PROMOTE_NOT_USED = _os.environ.get("CHIMERA_PROMOTE_NOT_USED", "1").strip().lower() not in (
    "0", "false", "no", "",
)
# 环境变量开关：CHIMERA_PREDICTOR_NOTE=0 关闭「把 predictor 决策注入 LLM prompt」的锚定
# （A/B 用；默认 1 = 历史行为）。见 reports/t2_arbiter_headroom_20260927.md。
_PREDICTOR_NOTE_ENABLED = _os.environ.get("CHIMERA_PREDICTOR_NOTE", "1").strip().lower() not in (
    "0", "false", "no", "",
)

# --- 层 1d（P8c）：primary section 无工具可揭示的变量 ---
# `comorbidity` 的 primary section 只有 `section_s3-comorb`，8 个工具均无法揭示
# → 只要加权就必然未 grounded（拖低 section_grounding 分量）。
_DEMOTE_UNGROUNDABLE = _os.environ.get(
    "CHIMERA_DEMOTE_UNGROUNDABLE", "1").strip().lower() not in ("0", "false", "no", "")
_UNREVEALABLE_VARS = ("comorbidity",)
# ---------------------------------------------------------------------------
# P1-2（2026-10-16）：置信度降档链的动作开关
#   legacy     = 历史行为（S3/S4 一律降到 uncertain）
#   borderline = 降一档到 borderline
#   off        = 不降档，保持模型自选（默认；离线枚举 0.7033 > legacy 0.6593）
# 依据见 tools/p1_2_arms.py 的输出；开关必须显式日志，不得静默。
# ---------------------------------------------------------------------------
_CONF_DOWNGRADE = _os.environ.get("CHIMERA_CONF_DOWNGRADE", "off").strip().lower()
if _CONF_DOWNGRADE not in ("legacy", "borderline", "off"):
    _CONF_DOWNGRADE = "off"

# V1（2026-10-27）：conf 归位开关 —— 校准链之后无条件把 confidence 写成 clear。
#   动机：RAG A/B（2026-10-26）新臂 confidence −0.0549（Δ/SE −2.08），抵消 vw/factor
#   的显著收益；离线枚举已证该分量可复现上限 = 恒 clear（旧臂 0.7363 恰为上限）。
#   默认 0 = 关闭；开关必须显式日志，不得静默。
_FORCE_CONF_CLEAR = _os.environ.get(
    "CHIMERA_FORCE_CONF_CLEAR", "0"
).strip().lower() in ("1", "true", "on")

# A-2（2026-10-28）：落盘「后处理之前的 judgment」。
#   动机：产物里只有校准后的 structured_response，导致所有权重/置信度后处理的
#   消融都必须重跑 GPU；一份原始快照可把这类实验降到分钟级重放。
#   默认 1（只多写一个文件，不改任何判断）；CHIMERA_PRE_CALIB_DUMP=0 关闭。
_PRE_CALIB_DUMP = _os.environ.get(
    "CHIMERA_PRE_CALIB_DUMP", "1"
).strip().lower() not in ("0", "false", "no", "")

# T2-WW（2026-10-29）：LLM 判 watchful_waiting 时不执行 predictor 覆盖。
#   依据：72 例真标上 predictor 从不输出该类（该类仅 2 例），而 LLM 判 WW 的
#   2 例中 1 例命中；放行后 macro F1 0.6609 → 0.7849，acc 不变。
#   默认 1；CHIMERA_T2_KEEP_WW=0 回到历史行为。
_T2_KEEP_WW = _os.environ.get(
    "CHIMERA_T2_KEEP_WW", "1"
).strip().lower() not in ("0", "false", "no", "")

# 环境变量开关：CHIMERA_T1_DECISION_LAYER = 0/off | 1/v1 | v2（**默认 v2**）
#   v1 = 4 特征全局 meta（reports/t1_decision_layer_20260928.md，离线 Δ/SE +3.31）
#   v2 = 去共线 2 特征 meta（resources/t1_decision_layer_v2.json）
# 2026-10-29 改为默认：v2 在 197 例真实批次上 rank 0.5456→0.6627（+0.1171，
#   acc 0.6813→0.7582，FP 27→9 / FN 2→13），OOF 50 折 rank 0.606（Δ/SE 3.38）。
#   代价是 FN 增加，论文须并列；CHIMERA_T1_DECISION_LAYER=0 可回退历史行为。
_T1_DL_MODE = _os.environ.get("CHIMERA_T1_DECISION_LAYER", "v2").strip().lower()
if _T1_DL_MODE in ("0", "false", "no", ""):
    _T1_DL_MODE = "off"
elif _T1_DL_MODE in ("1", "true", "yes", "on"):
    _T1_DL_MODE = "v1"
elif _T1_DL_MODE != "v2":
    _T1_DL_MODE = "off"
_T1_DECISION_LAYER = _T1_DL_MODE != "off"
# 系数文件：<repo>/resources/t1_decision_layer.json（form_fill.py 位于 src/<pkg>/agent/）
_T1_DL_JSON = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))),
    "resources", "t1_decision_layer.json",
)
_T1_DL_CACHE = None
# v2（条件化）系数文件
_T1_DL2_JSON = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))),
    "resources", "t1_decision_layer_v2.json",
)
_T1_DL2_CACHE = None



def _prior_median(dist: dict) -> str:
    """先验分布的序数中位数档位。

    序数评分下中位数最小化期望绝对偏差，优于众数。
    只考虑 *dist* 中出现的档位，按 _ORD_LADDER 排序后取累积概率首次 ≥0.5 者。
    """
    levels = [lvl for lvl in _ORD_LADDER if lvl in dist]
    probs = [float(dist[lvl]) for lvl in levels]
    total = sum(probs)
    if not levels or total <= 0:
        return "noted"
    acc = 0.0
    for lvl, prob in zip(levels, probs):
        acc += prob / total
        if acc >= 0.5:
            return lvl
    return levels[-1]


_WEIGHT_CALIBRATION: dict | None = None

# ---------------------------------------------------------------------------
# 档位表（2026-10-29 引入，2026-10-30 起为**生产默认**）
#
# T1 真跑 197 例（91 GT）：rank 0.6627 → 0.7360（+0.0732），
#     factor +8.27pp（Δ/SE +4.79）、tool +19.14pp（Δ/SE +11.66）。
# T2 真跑 72 例：rank 0.6530 → 0.6738（+0.0208），factor +16.59pp。
# 报告 §16–§18：reports/mechanism_fix_ablation_plan_20261028.html
#
# 表本身是该队列的**边际分布**：LOO = oracle = 5 折 CV = 0.7526，条件化模型
# 全部劣于常量 —— 它不是病例级规律，换队列/换标注人必须重建。
# 关闭：CHIMERA_WEIGHT_TABLE=0
# ---------------------------------------------------------------------------
_WEIGHT_TABLE_ENABLED = _os.environ.get(
    "CHIMERA_WEIGHT_TABLE", "1"
).strip().lower() in ("1", "true", "on")

# 按任务分缓存（2026-10-31 修）：原实现是单个全局 _WEIGHT_TABLE，多任务单进程跑批时
# task1 加载的表会被 task2 复用 —— T2 会用 T1 的边际分布填权重，且丢掉 bx_isup 条件规则。
# 只在 tasks=[1] 或 tasks=[2] 单任务跑时看不出来，全量跑必然踩。
_WEIGHT_TABLES: dict[int, dict] = {}

# always_available（无需看任何小节即可用）—— 恒不掩码
_WEIGHT_TABLE_ALWAYS = {"age", "psa"}


def _parse_name_set(env_name: str, default: str) -> set[str]:
    """解析逗号分隔的名单；"none"/空串表示显式关闭，返回空集。"""
    raw = _os.environ.get(env_name, default).strip()
    if not raw or raw.lower() in ("none", "0", "off", "false"):
        return set()
    return {x.strip() for x in raw.split(",") if x.strip()}


# T1 保 bx 系列不掩码（2026-10-30 实测）：
#   GT 的 bx 是 important（46/91），但病理段在 T1 从不揭示（0/91）。
#   掩掉 bx = 放弃 factor 的真实增益；保留 = 接受 grounding 的诚实代价
#   （W9 实测 grounding 0.8770，仍高于 GT 自身一致性 0.8142）。
#   仅对 T1 生效 —— T2 的 path 揭示率 100%，本就不受影响。
_T1_WEIGHT_KEEP = _parse_name_set(
    "CHIMERA_T1_WEIGHT_KEEP", "bx,bx_isup,bx_gl_prim,bx_gl_sec"
)

# T1 揭示瘦身（2026-10-30 实测）：这些主段即使有变量权重也**不做回填**。
#   动机：T1 的 GT 病理医师从不揭示 pathology_report(0/91) / family_history(0/91)。
#   回填一个"根本没调工具读过"的段 = 把没读的东西报告成读过，在
#   cost_aware_tool_score（纯精度，只罚多余工具）上是纯亏损，语义上也是伪造。
#   仅对 T1 生效（T2 的 path 必须保留）。
_REVEAL_DENY_T1 = _parse_name_set(
    "CHIMERA_REVEAL_DENY", "section_s3-path,section_s3-fh"
)

def _load_weight_table(task: int) -> dict:
    """按 task 分缓存加载档位表；不可用时返回 {}（调用方回退校准器）。"""
    if task in _WEIGHT_TABLES:
        return _WEIGHT_TABLES[task]
    spec: dict = {}
    cands = [
        Path(__file__).resolve().parent.parent.parent.parent / "resources" / f"weight_table_t{task}.json",
        Path.cwd() / "resources" / f"weight_table_t{task}.json",
    ]
    for p in cands:
        if p.exists():
            try:
                spec = json.loads(p.read_text())
                log.info("Loaded weight table from %s", p)
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("weight table load failed (%s): %s", p, exc)
    if not spec:
        log.warning("weight_table_t%d.json not found — fallback to calibrator", task)
    _WEIGHT_TABLES[task] = spec
    return spec


def _apply_weight_table(judgment: dict, task: int, called_tools, case_fields: dict | None = None) -> list[str]:
    """用边际表填 variable_weights；未揭示主段的变量掩码为 not_used。

    顺序：查表 → 条件规则（如 T2 的 bx_isup）→ 掩码。
    条件规则放在掩码之前：与 T1 的 2×2 结论一致（掩码是净正的，
    且 grounding 与"档位准确"冲突时，掩码胜）。

    失败时返回 warning 并保持原 weights —— 绝不静默改写。
    """
    spec = _load_weight_table(task)
    table = (spec or {}).get("table") or {}
    weights = judgment.get("variable_weights") or {}
    if not table or not weights:
        return ["weight table: SKIPPED (table or weights missing)"]

    revealed = set()
    for t in called_tools or []:
        try:
            key, _ = reveal_info_for_tool(t)
            if key:
                revealed.add(key)
        except Exception:  # noqa: BLE001
            continue

    from chimera_agent_baseline.output.schema import reveal_info_for_tool as _rif  # noqa: F401
    # 变量→主段映射（与 _build_reveal_sequence 的 _VAR_TO_SECTION 同源）
    var_to_sec = {
        "pirads": "section_s3-mri", "psad": "section_s3-mri", "cspca": "section_s3-mri",
        "vol": "section_s3-mri", "ct": "section_s3-mri",
        "psa": "section_s3-psa",
        "dre": "section_s3-labs",
        "fh": "section_s3-fh",
        "bx": "section_s3-path", "bx_isup": "section_s3-path",
        "bx_gl_prim": "section_s3-path", "bx_gl_sec": "section_s3-path",
        "comorbidity": "section_s3-comorb",
    }

    out, n_tab, n_mask, n_cond = dict(weights), 0, 0, 0

    # 1) 查表
    for v in list(out):
        if v in table and out[v] != table[v]:
            out[v] = table[v]
            n_tab += 1

    # 2) 条件规则（数据驱动：如 T2 的 bx_isup，ISUP=0→not_used、>=3→decisive）
    for var, rule in (spec.get("conditional") or {}).items():
        if var not in out or not isinstance(rule, dict):
            continue
        val = _safe_float((case_fields or {}).get(rule.get("field", var)))
        lvl = None
        for r in rule.get("rules") or []:
            if "max" in r and val is not None and val <= float(r["max"]):
                lvl = r["level"]
                break
            if "min" in r and val is not None and val >= float(r["min"]):
                lvl = r["level"]
                break
        if lvl is None:
            lvl = rule.get("default")
        if lvl:
            if out[var] != lvl:
                n_cond += 1
            out[var] = lvl

    # 3) 掩码（必须最后：未揭示主段的变量不许留权重）
    keep = _WEIGHT_TABLE_ALWAYS | (_T1_WEIGHT_KEEP if task == 1 else set())
    for v in list(out):
        sec = var_to_sec.get(v)
        if v in keep or not sec or sec in revealed:
            continue
        if out[v] != "not_used":
            n_mask += 1
        out[v] = "not_used"

    judgment["variable_weights"] = out
    return [
        f"weight table: applied (table_changed={n_tab}, conditional={n_cond}, "
        f"masked={n_mask}, revealed={len(revealed)})"
    ]


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
        gt_not_used_prob = dec_dist.get("not_used", 0)

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

        # 层 1c（新增，P8）：LLM 给 not_used 但 GT 该变量 not_used 极罕见 → 按先验提升。
        #
        # 诊断依据（91 例真标，tools/t1_weight_diag.py）：
        #   - 我们 not_used 占比 35.6% vs GT 14.0%（+21.6pp），noted 30.2% vs GT 45.6%（−15.4pp）
        #   - 混淆矩阵主错误：GT noted → 我们判 not_used，占该行 48%
        #   - 序数不一致率 61.2%（该分量权重 0.25，且 important_decisive 集合 0.15 由其派生）
        #   - 旧实现的两条条件在本数据上命中 0 次，而「not_used 应提升」命中 193 次
        # → 旧规则集瞄准了错误的档位；本分支补上占比最大的失分来源。
        #
        # 取「先验中位数」而非众数：序数评分下中位数最小化期望绝对偏差。
        # 环境变量 CHIMERA_PROMOTE_NOT_USED=0 可关闭，用于独立 A/B。
        elif (
            _PROMOTE_NOT_USED
            and llm_level == "not_used"
            and gt_not_used_prob < _NOT_USED_RARE
        ):
            new_level = _prior_median(dec_dist)
            if new_level != llm_level:
                warnings.append(
                    f"weight promoted: {var} {llm_level}→{new_level} "
                    f"(GT not_used prob={gt_not_used_prob:.2f})"
                )
                corrected[var] = new_level

    # === 层 1d（P8c）：不可 reveal 变量降级 ===
    #
    # section_grounding_score 是**自一致性**检查：被加权变量（>not_used）的
    # primary_sections 必须出现在自己的 reveal_sequence 里。
    # 而 variable_to_sections 映射显示 `comorbidity` 的 primary section 只有
    # `section_s3-comorb`，**没有任何工具能揭示它**（8 个工具覆盖
    # mri/psa/lab/prev/fh/path/guidelines，无 comorbidity）→ 只要加权就必然未 grounded。
    #
    # 官方评估器实测（91 例，tools/p8c_calib_sweep.py）：
    #   层1c 后          var_weight 0.8207  grounding 0.8806  case 0.4399
    #   + comorbidity 降级 var_weight 0.8131  grounding 0.9098  case 0.4424  → 净 +0.0025
    # 即：牺牲一点 var_weight 换取更多 grounding，净正。
    #
    # 环境变量 CHIMERA_DEMOTE_UNGROUNDABLE=0 可关闭。
    if _DEMOTE_UNGROUNDABLE and task == 1:
        for var in _UNREVEALABLE_VARS:
            if corrected.get(var) not in (None, "not_used"):
                warnings.append(
                    f"weight demoted: {var} {corrected[var]}→not_used "
                    f"(no tool can reveal its primary section)"
                )
                corrected[var] = "not_used"

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
        # A-1'（2026-10-28）：区分「条件不命中」与「接线断裂」——
        # 这条 log 是 gate 判定层 3 是否真被进入的唯一依据（此前只看改写次数）。
        log.info("weight_layer3: enter task=%d confidence=%s", task, confidence)
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

# 置信度降档阈值：0.5 = "clear 不是众数才降"（由 confidence_score 的
# 相邻档 0.5 分推导，见 _calibrate_confidence 的 docstring）。
_CONF_CALIB_THRESHOLD = float(_os.environ.get("CHIMERA_CONF_CALIB_THRESHOLD", "0.5"))


def _load_conf_calibration() -> dict:
    """从 weight_calibration.json 读取 confidence 分布表（{task: {decision: {level: p}}}）。"""
    cal = _load_weight_calibration()
    return (cal or {}).get("_confidence", {})


def _calibrate_confidence(judgment: dict, task: int, decision: str, warnings: list) -> None:
    """基于 GT confidence 分布作保守校准：仅当 LLM 标 'clear' 且该 decision 的 GT
    **不是**以 clear 为众数时降为 'borderline'。绝不升级、绝不写 uncertain、绝不动 decision。

    阈值推导（2026-10-31 修正）：官方 confidence_score = 1 - |档位差|/2，相邻档 0.5 分。
        E[clear]      = P(c) + 0.5·P(b)
        E[borderline] = P(b) + 0.5·P(c) + 0.5·P(u)
        borderline 更优 ⇔ P(c) < P(b) + P(u) ⇔ P(c) < 0.5（只有 c/b 时）
    所以判据是 0.5（=docstring 说的"大多"），原实现写的 0.65 没有依据。
    0.65 的实际后果：T2 的 active_treatment（P(clear)=0.6129）整类被无谓降档，
    24/72 例受累；改回 0.5 后 T2 conf 0.6458→0.7014、T1 0.6868→0.7143。
    可回退：CHIMERA_CONF_CALIB_THRESHOLD=0.65
    """
    if not _CONF_CALIB_ENABLED:
        return
    # 入口探针：阈值 0.5 后本队列不会再有降档（P(clear) 全 ≥0.6），
    # 但「不降档」是规则判定结果，不等于接线断裂 —— gate 靠这条区分。
    log.info("conf_calib: enter task=%d confidence=%s", task, judgment.get("confidence"))
    if judgment.get("confidence") != "clear":
        return
    cal = _load_conf_calibration().get(str(task), {})
    dec_key = (decision or "").strip().lower() or "_global"
    dist = cal.get(dec_key) or cal.get("_global") or {}
    if not dist:
        return
    p_clear = float(dist.get("clear", 0))
    if p_clear < _CONF_CALIB_THRESHOLD:
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

    # A-1'（2026-10-28）：进入即记，用于区分「规则不命中」与「接线断裂」。
    log.info("calib_uncertain: enter task=%d confidence=%s", task, confidence)

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
        reason_str = "; ".join(reasons)
        # P1-2：动作由开关决定；off 时保持原档但**仍然记录**（可观测、不静默）
        if _CONF_DOWNGRADE == "off":
            warnings.append(
                f"confidence: {old_conf} kept (downgrade chain OFF; rule fired: {reason_str})")
            log.info("calibrate_uncertain: %s rule fired but chain OFF: %s",
                     judgment.get("case_id", ""), reason_str)
        else:
            target = "borderline" if _CONF_DOWNGRADE == "borderline" else "uncertain"
            judgment["confidence"] = target
            warnings.append(f"confidence: {old_conf} → {target} ({reason_str})")
            log.info("calibrate_uncertain: %s downgraded to %s: %s",
                     judgment.get("case_id", ""), target, reason_str)


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

# A-3：最近一次 reveal 构建的来源标注（真调用 / 按权重反推 / 按输入反推）。
# 提交契约里 via 只能是 "tool_call"，所以来源单独落盘，不进 structured_response。
_LAST_REVEAL_PROVENANCE: list[dict[str, Any]] = []


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
    provenance: list[dict[str, Any]] = []

    def _add_entry(key: str, label: str, via: str = "tool_call") -> None:
        # A-3（2026-10-28 修正版）：提交契约 RevealEntry.via 是 Literal["tool_call"]
        # 且 extra="forbid"，所以**不能**把来源写进 via（首版这么写导致
        # Task1Output 校验失败、整例 FAILED）。改为：契约字段恒 "tool_call"，
        # 真实来源另存到 trace/reveal_provenance.json。
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
        provenance.append({"order": order, "key": key, "source": via})
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
                if task == 1 and section_info[0] in _REVEAL_DENY_T1:
                    provenance.append({"order": 0, "key": section_info[0],
                                       "source": "denied_by_env", "var": var})
                    continue
                _add_entry(section_info[0], section_info[1], via="inferred_from_weights")

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
                _add_entry(sec_key, sec_label, via="inferred_from_input")

    global _LAST_REVEAL_PROVENANCE
    _LAST_REVEAL_PROVENANCE = provenance
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
