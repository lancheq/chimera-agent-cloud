import json
import math

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from chimera_agent_baseline.agent.evidence_features import assemble_t1_observed_features
from chimera_agent_baseline.agent.predictor_node import make_predictor_node
from chimera_agent_baseline.predictor import build_case_features


def msg(tool, **fields):
    status = fields.pop("_status", "success")
    payload = {"case_id": "case", **fields}
    blocks = [{"type": "text", "text": json.dumps(payload)}]
    return ToolMessage(content=str(blocks), name=tool, tool_call_id="test", status=status)


def test_successful_report_maps_only_direct_observed_fields():
    frame = assemble_t1_observed_features([msg("get_mri_report", radiology_report=(
        "PI-RADS v2.1 clinical indication score 5. Prostate volume measures 30 cc. "
        "PSA density .14 ng/mL/cc. IMPRESSION: PI-RADS 3."
    ))], "case")
    assert frame["fields"] == {"pirads_int": 3, "vol": 30, "psad": .14}
    assert "psa" not in frame["fields"] and "cspca" not in frame["fields"]
    assert frame["evidence"]["vol"]["candidates"][0]["source_field"] == "radiology_report"


@pytest.mark.parametrize("messages", [
    [AIMessage(content="Prostate volume 30 mL. PI-RADS 5. PSA density 1.0")],
    [msg("get_mri_report", radiology_report="PI-RADS 5", _status="error")],
    [msg("get_mri_report", radiology_report="PI-RADS 5", case_id="other")],
])
def test_unobserved_failed_and_other_patient_evidence_is_rejected(messages):
    assert assemble_t1_observed_features(messages, "case")["fields"] == {}


def test_report_disagreement_stays_missing_instead_of_selecting_a_side():
    frame = assemble_t1_observed_features([msg("get_mri_report", radiology_report=(
        "Prostate volume 30 mL, prostate volume 40 mL. PSA density 0.1, PSAD 0.2. PI-RADS 3 and PI-RADS 4."
    ))], "case")
    assert frame["fields"] == {}
    assert frame["evidence"]["vol"]["status"] == "conflict"


def test_explicit_volume_prose_and_measurement_bounds():
    for text in ("The gland measures 57.28 mL in total volume.",
                 "The prostate measures 57.28 mL in volume.",
                 "Prostatic dimensions yield a total volume of 57.28 mL."):
        assert assemble_t1_observed_features([msg("get_mri_report", radiology_report=text)], "case")["fields"] == {"vol": 57.28}
    frame = assemble_t1_observed_features([msg("get_mri_report", radiology_report=(
        "PSAD <0.15 ng/mL/mL. Prostate volume ~30 mL. PI-RADS 3-4."))], "case")
    assert frame["fields"] == {}


def test_latest_psa_uses_dates_not_array_order():
    frame = assemble_t1_observed_features([msg("get_psa_trend", psa_trend=[
        {"date": "Jan 2025", "val": 7}, {"date": "Jan 2024", "val": 3},
    ])], "case")
    assert frame["fields"]["psa"] == 7
    assert len(frame["evidence"]["psa"]["candidates"]) == 2


def test_overlapping_month_day_and_undated_conflicts_are_not_invented_order():
    for other in ("10 Jan 2025", None):
        frame = assemble_t1_observed_features([
            msg("get_psa_trend", psa_trend=[{"date": "Jan 2025", "val": 7}]),
            msg("get_lab_results", laboratory_results=[{"name": "PSA", "date": other, "val": "8 ng/mL"}]),
        ], "case")
        assert "psa" not in frame["fields"]
        assert frame["evidence"]["psa"]["status"] == "conflict"


def test_unsupported_units_bounds_and_free_psa_are_not_total_psa():
    frame = assemble_t1_observed_features([
        msg("get_lab_results", laboratory_results=[
            {"name": "Free PSA", "val": "1.2 ng/mL"}, {"name": "PSA", "val": "<4 ng/mL"},
            {"name": "PSA", "val": "8 ug/L"},
        ]),
        msg("get_mri_report", radiology_report="PSA density 0.14 ng/L/mL. Prostate volume -30 mL."),
    ], "case")
    assert frame["fields"] == {}


def case_dir(tmp_path, prompt):
    case = tmp_path / "case"
    case.mkdir()
    (case / "prompt.json").write_text(json.dumps({"case_id": "case", "age": 67, **prompt}))
    (case / "prostate-biopsy-decision-clinical-data.json").write_text(json.dumps({
        "radiology_report": "PI-RADS 5. Prostate volume 30 mL. PSA density 0.2.", "cspca": 99,
    }))
    return case


def test_bridge_fills_missing_without_reading_hidden_headlines(tmp_path):
    case = case_dir(tmp_path, {})
    row, _ = build_case_features(case, 1, allowed_sections=set(), observed_t1_fields={"pirads_int": 3})
    assert row["pirads_int"] == 3
    assert math.isnan(row["psad"]) and math.isnan(row["cspca"])
    old, _ = build_case_features(case, 1, allowed_sections=set())
    assert math.isnan(old["pirads_int"])


def test_existing_prompt_values_are_preserved_and_t2_scope_is_rejected(tmp_path):
    case = case_dir(tmp_path, {"psa": 0, "pirads": "4", "psad": .1, "vol": 30})
    row, _ = build_case_features(case, 1, observed_t1_fields={"psa": 9, "pirads_int": 5})
    assert row["psa"] == 0 and row["pirads_int"] == 4
    with pytest.raises(ValueError, match="restricted"):
        build_case_features(case, 2, observed_t1_fields={"psa": 9})


def test_node_passes_same_features_to_predict_and_explain_and_rejects_failed_reveals(tmp_path):
    captured = []

    class Predictor:
        available_tasks = [1]

        def predict(self, path, task, **kwargs):
            captured.append(kwargs)
            return {"decision": "no"}

        def explain(self, path, task, **kwargs):
            captured.append(kwargs)
            return {}

    node = make_predictor_node(Predictor(), t1_evidence_features=True)
    result = node({"task": 1, "case_id": "case", "case_dir": str(tmp_path), "messages": [
        msg("get_mri_report", radiology_report="PI-RADS 5", _status="error"),
        msg("get_psa_trend", psa_trend=[{"date": "Jan 2025", "val": 7}]),
    ]})
    assert captured[0] == captured[1]
    assert captured[0]["observed_t1_fields"] == {"psa": 7}
    assert captured[0]["reveal_mask"] == {"psa_trend"}
    assert result["predictor_trace"]["observed_feature_evidence"]["fields"] == {"psa": 7}


def test_research_trace_can_preserve_complete_tool_payloads():
    from chimera_agent_baseline.run import _serialize_messages
    from tools.evaluate_t1_evidence_contract import restore_messages
    tool = msg("get_mri_report", radiology_report="x" * 9000 + " PI-RADS 5.")
    capped = _serialize_messages([tool])
    assert capped[0]["content_truncated"]
    with pytest.raises(ValueError, match="truncated"):
        restore_messages(capped)
    complete = _serialize_messages([tool], content_limit=0)
    assert not complete[0]["content_truncated"]
    restored = restore_messages(complete)
    assert restored[0].content == tool.content
    assert assemble_t1_observed_features(restored, "case")["fields"]["pirads_int"] == 5


def test_replay_after_collection_off_mode_actually_runs_the_gate(monkeypatch):
    from chimera_agent_baseline.agent import form_fill
    from tools.evaluate_t1_evidence_contract import enable_v2_replay, require_v2_execution
    monkeypatch.setattr(form_fill, "_T1_DL_MODE", "off")
    monkeypatch.setattr(form_fill, "_T1_DECISION_LAYER", False)
    with pytest.raises(RuntimeError, match="no actual"):
        require_v2_execution({"form_fill_warnings": []})
    enable_v2_replay()
    judgment, warnings = {"biopsy_decision": "yes"}, []
    form_fill._apply_t1_decision_layer(1, judgment, {"probabilities": {"yes": .1, "no": .9}}, warnings)
    require_v2_execution({"form_fill_warnings": warnings})
    assert judgment["biopsy_decision"] == "no"
