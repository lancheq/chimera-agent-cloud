"""仲裁冻结与覆盖分类测试。零生成。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chimera_agent_baseline.agent.arbitration import (  # noqa: E402
    acceptance_summary, classify_receipt, load_policy, POLICY_PATH,
)


def test_policy_is_frozen_without_reflection_priority():
    p = load_policy()
    assert p["version"] == 1
    assert p["reflection_priority"] is False
    assert p["override_recording"] == "required"
    assert p["medical_judgement_deferred_to"] == "G2"


def test_policy_rejects_priority_true(tmp_path):
    bad = json.loads(Path(POLICY_PATH).read_text())
    bad["reflection_priority"] = True
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(RuntimeError, match="reflection_priority_forbidden"):
        load_policy(str(p))


def _receipt(fields, terminal="finalized_retained"):
    return SimpleNamespace(terminal=terminal, fields=tuple(fields))


def test_classify_counts_four_categories():
    r = _receipt([
        {"field": "/event", "status": "retained", "selected": 0, "final": 0, "history": []},
        {"field": "/months_to_recurrence", "status": "authorized_overwrite",
         "selected": 24.0, "final": 24.0,
         "history": [{"stage": "assembly", "rule_id": "R1"}]},
        {"field": "/free_text", "status": "unauthorized_overwrite",
         "selected": "a", "final": "b", "history": [{"stage": "t3_event_calibration"}]},
        {"field": "/repeat_test", "status": "internal_only", "selected": None,
         "final": None, "history": []},
    ])
    out = classify_receipt(r)
    assert out["counts"] == {"retained": 1, "overridden_authorized": 1,
                             "overridden_unexplained": 1, "dropped": 0,
                             "internal_only": 1}
    assert out["terminal"] == "finalized_retained"


def test_unauthorized_override_flags_investigation():
    r = _receipt([{"field": "/event", "status": "unauthorized_overwrite",
                   "selected": 1, "final": 0, "history": [{"stage": "t3_event_calibration"}]}],
                 terminal="consumption_contract_failed")
    s = acceptance_summary(r)
    assert s["needs_investigation"] is True
    assert s["fields"][0]["field"] == "/event"


def test_no_override_is_clean():
    r = _receipt([{"field": "/free_text", "status": "retained",
                   "selected": "x", "final": "x", "history": []}])
    s = acceptance_summary(r)
    assert s["needs_investigation"] is False
