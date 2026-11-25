"""P0-2b：predictor 节点 —— 放在 ReAct 之后、form_fill 之前。

**为什么必须搬位置**：predictor 的「成本对齐」要求它只消费 LLM 已经揭示
（= 已经为 tool 分付过费）的段落，而"LLM 揭示了哪些段落"只在 ReAct 循环
结束后才存在。因此把 predictor 的调用从 run.py 的「图之前」搬到「ReAct 之后」。

**行为不变性**：form_fill 仍然只从 ``state["predictor_decision"]`` 读，
所以 ``_predictor_note`` 与 T2/T3 的 override 逻辑一行都不用改。

本节点只做三件事：算 reveal 掩码 → ``predict()`` → ``explain()`` → 写回 state。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import ToolMessage

from chimera_agent_baseline.output.schema import reveal_info_for_tool
from chimera_agent_baseline.runtime_audit import component_event
from chimera_agent_baseline.agent.evidence_features import assemble_t1_observed_features, decode_payload

log = logging.getLogger(__name__)

# reveal 的 key（section_s3-*）→ clinical-data.json 里的段落名
SECTION_KEY_TO_CD: dict[str, str] = {
    "section_s3-mri": "radiology_report",
    "section_s3-psa": "psa_trend",
    "section_s3-labs": "laboratory_results",
    "section_s3-prev": "previous_notes",
    "section_s3-fh": "family_history",
    "section_s3-path": "pathology_report",
}


def revealed_sections(messages: list) -> set[str]:
    """从 ToolMessage 反推 LLM 已揭示的 clinical-data 段落集合。

    与 form_fill._build_reveal_sequence 使用同一个 reveal_info_for_tool()，
    保证"成本对齐用的集合"和"提交给评分的 reveal_sequence"是同一套语义。
    """
    out: set[str] = set()
    for m in messages or []:
        name = getattr(m, "name", None)
        if not (isinstance(m, ToolMessage) and name):
            continue
        try:
            key, _label = reveal_info_for_tool(name)
        except Exception:  # noqa: BLE001 — 未知工具名不应中断整例
            continue
        cd = SECTION_KEY_TO_CD.get(str(key))
        if cd:
            out.add(cd)
    return out


def make_predictor_node(predictor: Any, cost_align: bool = True, t1_evidence_features: bool = False):
    """返回 LangGraph 节点闭包。cost_align=False 时退化为"读全文件"（历史行为）。"""

    def predictor_node(state: dict[str, Any]) -> dict[str, Any]:
        task = int(state.get("task", 1))
        case_id = state.get("case_id", "unknown")
        case_dir_raw = state.get("case_dir")

        if predictor is None or case_dir_raw is None:
            return {"predictor_decision": {},
                    "predictor_trace": {"skipped": "no predictor or missing state['case_dir']"}}
        if task not in predictor.available_tasks:
            return {"predictor_decision": {},
                    "predictor_trace": {"skipped": f"task {task} not in available_tasks"}}

        case_dir = Path(str(case_dir_raw))
        reveal = revealed_sections(state.get("messages") or [])
        mask = reveal if cost_align else None
        feature_evidence = None
        extra = {}
        if task == 1 and t1_evidence_features:
            feature_evidence = assemble_t1_observed_features(state.get("messages") or [], case_id)
            # A failed/name-only reveal does not authorize reading a section.
            if cost_align:
                successful = []
                for msg in state.get("messages") or []:
                    if not isinstance(msg, ToolMessage) or getattr(msg, "status", "success") == "error":
                        continue
                    payload = decode_payload(msg.content)
                    if not payload or payload.get("case_id") != case_id or "error" in payload:
                        continue
                    section = revealed_sections([msg])
                    if any(name in payload for name in section):
                        successful.append(msg)
                mask = revealed_sections(successful)
            extra["observed_t1_fields"] = feature_evidence["fields"]
            component_event("t1_observed_feature_assembly", configured=True, status="executed",
                            task=task, case_id=case_id, fields=sorted(feature_evidence["fields"]),
                            conflicts=[name for name, item in feature_evidence["evidence"].items() if item["status"] == "conflict"])

        try:
            dec = predictor.predict(case_dir, task, reveal_mask=mask, **extra)
        except Exception as exc:  # noqa: BLE001 — 失败不静默、也不中断该例
            component_event("predictor_inference", configured=True, status="fallback", reason=str(exc),
                            fallback="pure_llm", required=True, task=task, case_id=case_id)
            log.warning("predictor node: predict failed for %s: %s", case_id, exc)
            return {"predictor_decision": {},
                    "predictor_trace": {"predict_failed": f"{type(exc).__name__}: {exc}",
                                        "cost_align": bool(cost_align),
                                        "revealed_sections": sorted(reveal)}}

        component_event("predictor_inference", configured=True, status="executed", task=task, case_id=case_id)
        try:
            ex = predictor.explain(case_dir, task, reveal_mask=mask, **extra)
            if feature_evidence is not None:
                ex["observed_feature_evidence"] = feature_evidence
            ex["decision"] = (dec.get("decision") if dec.get("decision") is not None
                              else dec.get("event"))
            ex["probabilities"] = dec.get("probabilities") or dec.get("p_event")
            ex["at_threshold"] = dec.get("at_threshold")
            ex["cost_align"] = bool(cost_align)
            ex["revealed_sections"] = sorted(mask if mask is not None else reveal)
            return {"predictor_decision": dec, "predictor_trace": ex}
        except Exception as exc:  # noqa: BLE001
            log.warning("predictor node: explain failed for %s: %s", case_id, exc)
            return {"predictor_decision": dec,
                    "predictor_trace": {"explain_failed": f"{type(exc).__name__}: {exc}",
                                        "decision": dec.get("decision"),
                                        "cost_align": bool(cost_align),
                                        "revealed_sections": sorted(reveal)}}

    return predictor_node
