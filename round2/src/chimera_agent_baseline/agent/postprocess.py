"""Ordered post-judgment stages. Clinical rules stay in the legacy helpers.

Dependencies are explicit so extraction preserves the existing functions,
configuration, resource caches and external replay imports without duplicates.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from chimera_agent_baseline.runtime_audit import ProcessingAudit


@dataclass(frozen=True)
class PostprocessOps:
    decision_override: Callable
    case_fields: Callable
    t1_decision_layer: Callable
    numeric_verify: Callable
    treatment_floor: Callable
    t3_event: Callable
    t3_tau: Callable
    load_weight_table: Callable
    weight_table: Callable
    calibrate_weights: Callable
    calibrate_confidence: Callable
    t3_salient: Callable
    t1_mode: str
    weight_table_enabled: bool
    force_conf_clear: bool


def apply_postprocessing(judgment: dict, *, task: int, state: dict, predictor_dec: dict,
                         decision_override: bool, transcript: str, called: set,
                         warnings: list, ops: PostprocessOps, audit: ProcessingAudit) -> dict:
    def execute(name, enabled, action):
        before, start = deepcopy(judgment), len(warnings)
        if enabled:
            action()
        audit.record(name, before, judgment, warnings[start:], configured=bool(enabled),
                     status="executed" if enabled else "disabled")

    execute("predictor_override", decision_override and bool(predictor_dec),
            lambda: ops.decision_override(task, judgment, predictor_dec, warnings))

    def decision_layer():
        cf = ops.case_fields(state.get("case_dir")) if ops.t1_mode == "v2" else None
        ops.t1_decision_layer(task, judgment, predictor_dec, warnings, case_fields=cf)

    start = len(warnings)
    execute("t1_decision_layer", task == 1 and ops.t1_mode != "off", decision_layer)
    if task == 1 and ops.t1_mode != "off":
        failures = [w for w in warnings[start:] if "LOAD FAILED" in w or "SKIPPED" in w]
        if failures:
            audit.record("t1_decision_layer_availability", judgment, judgment, failures,
                         status="fallback", reason="; ".join(failures), fallback="keep_llm_decision", required=True)

    def numeric():
        ft = judgment.get("free_text", "")
        verified = ops.numeric_verify(ft, state.get("patient") or {}, task, judgment, warnings)
        if verified != ft:
            judgment["free_text"] = verified

    execute("numeric_verification", bool(judgment.get("free_text", "")), numeric)
    execute("t2_treatment_floor", task == 2, lambda: ops.treatment_floor(judgment, transcript, warnings))
    execute("t3_event_calibration", task == 3, lambda: ops.t3_event(judgment, transcript, warnings))
    execute("t3_time_calibration", task == 3, lambda: ops.t3_tau(judgment, warnings))

    def months_fallback():
        judgment["months_to_recurrence"] = 36.0
        warnings.append("t3_months_fallback: event=0 & months=None → 36.0")

    needs_months = task == 3 and judgment.get("event") == 0 and judgment.get("months_to_recurrence") is None
    execute("t3_months_fallback", needs_months, months_fallback)
    if needs_months:
        audit.record("t3_months_default", judgment, judgment, [], status="fallback",
                     reason="Missing recurrence time", fallback="36_months", required=True)

    def weights():
        dec = judgment.get("biopsy_decision") or ""
        if task == 2:
            rec = judgment.get("treatment_recommendation", {})
            if isinstance(rec, dict):
                dec = rec.get("primary", "")
        table_ok = ops.weight_table_enabled and bool(ops.load_weight_table(task).get("table"))
        if table_ok:
            cf = ops.case_fields(state.get("case_dir"))
            warnings.extend(ops.weight_table(judgment, task, called, cf))
        else:
            audit.record("weight_table_availability", judgment, judgment, [],
                         configured=ops.weight_table_enabled, status="fallback" if ops.weight_table_enabled else "disabled",
                         reason="Weight table not available" if ops.weight_table_enabled else None,
                         fallback="statistical_calibrator", required=ops.weight_table_enabled)
            warnings.extend(ops.calibrate_weights(judgment, task, dec, judgment.get("confidence", "clear"),
                                                 state.get("patient") or {}))
        ops.calibrate_confidence(judgment, task, dec, warnings)

    execute("weight_and_confidence_calibration", task in (1, 2) and bool(judgment.get("variable_weights")), weights)

    def force_clear():
        if judgment.get("confidence") != "clear":
            warnings.append(f"confidence: {judgment.get('confidence')} → clear (FORCE_CONF_CLEAR)")
            judgment["confidence"] = "clear"

    execute("force_confidence_clear", ops.force_conf_clear and bool(judgment.get("confidence")), force_clear)
    execute("t3_rationale_elements", task == 3, lambda: ops.t3_salient(judgment, transcript, warnings))
    return judgment
