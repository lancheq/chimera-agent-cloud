"""Structured Refine 接入 self_refine 的集成测试：桩模型驱动，零真实调用。

覆盖多轮状态传递（J0 → J1 → J2 → 相对 J0 收敛）、补丁被拒、无修改收敛、
开关默认关闭、trace 产物完整。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chimera_agent_baseline.agent.reflection_contract import upfront_record  # noqa: E402
from chimera_agent_baseline.agent.self_refine import (  # noqa: E402
    run_self_refine, run_structured_refine, structured_refine_enabled,
)
from chimera_agent_baseline.runtime_audit import content_digest  # noqa: E402

MSG = """System prompt.

Clinical data (read-only — values loaded from the record):
PSA density: 0.452 ng/mL/mL.
mpMRI: PI-RADS 5 lesion, 12 mm.
Biopsy 2024: ISUP Grade Group 1, 2/12 cores.

Information you can request via tools
- prior biopsy history
"""
UPFRONT = upfront_record(MSG)

J0 = {
    "treatment_recommendation": {"primary": "active_surveillance"},
    "confidence": "clear",
    "free_text": "Low-grade disease supports surveillance.",
    "variable_weights": {"psad": "minor"},
}


class _Resp:
    def __init__(self, content):
        self.content = content


class StubModel:
    """按序返回预置内容，模拟 Critique 与 Structured Refine 两类调用。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if not self.replies:
            raise AssertionError("桩模型回复已用尽，调用次数超预期")
        return _Resp(self.replies.pop(0))


def patch_json(*patches):
    return json.dumps({"patches": list(patches)})


CRITIQUE_HIT = json.dumps({"issues": ["PSAD weight understated given PI-RADS 5"],
                           "severity": "mild"})
CRITIQUE_NONE = json.dumps({"issues": [], "severity": "low"})


def test_switch_defaults_off(monkeypatch):
    monkeypatch.delenv("CHIMERA_STRUCTURED_REFINE", raising=False)
    assert structured_refine_enabled() is False
    monkeypatch.setenv("CHIMERA_STRUCTURED_REFINE", "1")
    assert structured_refine_enabled() is True


def test_legacy_path_untouched():
    """默认路径仍返回 (text, warnings)，不被补丁逻辑污染。"""
    model = StubModel([CRITIQUE_NONE])
    text, warnings = run_self_refine(model=model, task=2, case_id="T2-001",
                                     transcript="original reasoning")
    assert text == "original reasoning"
    assert any("no issues" in w for w in warnings)


def test_single_round_then_converge(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_OUTPUT_DIR", str(tmp_path))
    model = StubModel([
        CRITIQUE_HIT,
        patch_json({"path": "/variable_weights/psad", "before": "minor", "after": "important",
                    "error_type": "under_treatment",
                    "evidence_quote": "PSA density: 0.452 ng/mL/mL.",
                    "reason": "PSAD 0.452 with PI-RADS 5 warrants higher weight"}),
        CRITIQUE_NONE,
    ])
    out, warnings, meta = run_structured_refine(model, 2, "T2-001", "reasoning", J0, UPFRONT)
    assert out.status == "ok", out.rejects
    assert out.changed_fields == ("/variable_weights/psad",)
    assert meta["model_calls"] == 3 and meta["rounds"] == 1
    assert json.loads(out.candidate_json)["confidence"] == "clear"   # 未提及字段保原值


def test_trace_products_written(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_OUTPUT_DIR", str(tmp_path))
    model = StubModel([CRITIQUE_HIT, patch_json(
        {"path": "/confidence", "before": "clear", "after": "borderline",
         "error_type": "internal_inconsistency",
         "evidence_quote": "PI-RADS 5 lesion, 12 mm", "reason": "mixed risk"}),
        CRITIQUE_NONE])
    run_structured_refine(model, 2, "T2-777", "reasoning", J0, UPFRONT)
    d = tmp_path / "trace" / "T2-777"
    for name in ("critique_iter1.json", "patch_iter1.json", "apply_iter1.json",
                 "run_manifest.json"):
        assert (d / name).exists(), name
    man = json.loads((d / "run_manifest.json").read_text())
    assert man["model_calls"] == 3 and man["final_status"] == "ok"


def test_two_rounds_final_candidate_relative_to_j0(tmp_path, monkeypatch):
    """第二轮基于 J1 提出补丁，但最终候选的 before 必须来自 J0。"""
    monkeypatch.setenv("CHIMERA_OUTPUT_DIR", str(tmp_path))
    model = StubModel([
        CRITIQUE_HIT,
        patch_json({"path": "/confidence", "before": "clear", "after": "borderline",
                    "error_type": "internal_inconsistency",
                    "evidence_quote": "PI-RADS 5 lesion, 12 mm", "reason": "mixed risk"}),
        CRITIQUE_HIT,
        patch_json({"path": "/confidence", "before": "borderline", "after": "unclear",
                    "error_type": "internal_inconsistency",
                    "evidence_quote": "PSA density: 0.452 ng/mL/mL.", "reason": "still mixed"}),
        CRITIQUE_NONE,
    ])
    out, _, meta = run_structured_refine(model, 2, "T2-002", "reasoning", J0, UPFRONT)
    assert out.status == "ok", out.rejects
    assert json.loads(out.candidate_json)["confidence"] == "unclear"
    assert out.changed_fields == ("/confidence",)
    assert meta["rounds"] == 2


def test_rejected_patches_stop_and_keep_j0(tmp_path, monkeypatch):
    """补丁被拒时不得产出候选，J0 保持原样。"""
    monkeypatch.setenv("CHIMERA_OUTPUT_DIR", str(tmp_path))
    model = StubModel([
        CRITIQUE_HIT,
        patch_json({"path": "/free_text", "before": J0["free_text"], "after": "rewritten",
                    "error_type": "other",
                    "evidence_quote": "DRE nodule cT2a", "reason": "unsupported quote"}),
    ])
    out, warnings, meta = run_structured_refine(model, 2, "T2-003", "reasoning", J0, UPFRONT)
    assert out.status == "rejected"
    assert out.candidate_json is None
    assert meta["rounds"] == 0
    assert any("rejected" in w for w in warnings)
    apply_log = json.loads((tmp_path / "trace" / "T2-003" / "apply_iter1.json").read_text())
    assert apply_log["status"] == "rejected"
    assert any(c == "evidence_not_in_record" for c, _ in apply_log["rejects"])


def test_unparseable_patch_stops_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_OUTPUT_DIR", str(tmp_path))
    model = StubModel([CRITIQUE_HIT, "I think the PSAD should be higher."])
    out, warnings, meta = run_structured_refine(model, 2, "T2-004", "reasoning", J0, UPFRONT)
    assert out.status == "rejected"
    assert any("parse failed" in w for w in warnings)
    assert meta["rounds"] == 0


def test_empty_patches_is_valid_convergence(tmp_path, monkeypatch):
    monkeypatch.setenv("CHIMERA_OUTPUT_DIR", str(tmp_path))
    model = StubModel([CRITIQUE_HIT, json.dumps({"patches": []})])
    out, _, meta = run_structured_refine(model, 2, "T2-005", "reasoning", J0, UPFRONT)
    assert out.status == "rejected" and out.rejects[0][0] == "no_patches"
    assert meta["rounds"] == 0
