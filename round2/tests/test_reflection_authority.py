"""生产入口集成测试：form_fill 的 authority.review(context) → resolve()。

零真实调用，全部用桩模型。验证真实契约入口可达，而非仅模块间接口。
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from pydantic import create_model

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chimera_agent_baseline.agent import reflection_contract as rc  # noqa: E402
from chimera_agent_baseline.agent.reflection_authority import (  # noqa: E402
    StructuredReflectionAuthority,
)
from chimera_agent_baseline.runtime_audit import canonical, content_digest  # noqa: E402

T3Model = create_model("T3Judgment", event=(int, ...),
                       months_to_recurrence=(float, ...), free_text=(str, ...))

MSG = """System prompt.

Clinical data (read-only — values loaded from the record):
Last follow-up: 24 months, no BCR observed.
PSA density: 0.452 ng/mL/mL.

Information you can request via tools
- prior biopsy history
"""
UPFRONT = rc.upfront_record(MSG)
J0 = {"event": 1, "months_to_recurrence": 18.0,
      "free_text": "Recurrence projected at 18 months from treatment."}

GOOD_PATCH = {"patches": [
    {"path": "/event", "before": 1, "after": 0, "error_type": "fabricated_facts",
     "evidence_quote": "Last follow-up: 24 months", "reason": "no BCR observed"},
    {"path": "/months_to_recurrence", "before": 18.0, "after": 24.0,
     "error_type": "fabricated_facts", "evidence_quote": "Last follow-up: 24 months",
     "reason": "follow-up duration is 24 months"},
    {"path": "/free_text", "before": J0["free_text"],
     "after": "No BCR observed through 24 months of follow-up.",
     "error_type": "fabricated_facts", "evidence_quote": "Last follow-up: 24 months",
     "reason": "align rationale with observed follow-up"},
]}


class _Resp:
    def __init__(self, content):
        self.content = content


class StubModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if not self.replies:
            raise AssertionError("桩回复用尽，调用次数超预期")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return _Resp(r if isinstance(r, str) else json.dumps(r))


def make_context(j0=None):
    j0 = j0 or J0
    subject = rc.Subject("run-1", 3, "T3-001", "profile",
                         rc.DraftArtifact("d1", "d2"), content_digest(j0),
                         rc.upfront_digest(UPFRONT), "proc")
    return rc.Context(subject, canonical(j0), UPFRONT, ())


def run_authority(replies, **kw):
    model = StubModel(replies)
    auth = StructuredReflectionAuthority(model, **kw)
    return auth, auth.review(make_context()), model


CRITIQUE_HIT = {"issues": ["claims recurrence although follow-up shows none"],
                "severity": "moderate"}
CRITIQUE_NONE = {"issues": [], "severity": "none"}
MED_ACCEPT = {"verdict": "accept", "blocking": [], "notes": "consistent with record"}


def test_no_issues_returns_no_issue():
    auth, batch, model = run_authority([CRITIQUE_NONE])
    assert batch.status == "no_issue" and not batch.reviews
    assert model.calls == 1                       # 只有 critique 一次


def test_valid_flow_reaches_consumer():
    auth, batch, model = run_authority([CRITIQUE_HIT, GOOD_PATCH, MED_ACCEPT])
    assert batch.status == "detected" and len(batch.reviews) == 1
    assert model.calls == 3
    sel = rc.resolve(make_context(), batch,
                     SimpleNamespace(method=auth.method), T3Model)
    assert sel.status == "accepted_candidate", (sel.status, sel.reason)
    assert json.loads(sel.selected_json)["event"] == 0


def test_medical_reject_keeps_initial():
    auth, batch, model = run_authority(
        [CRITIQUE_HIT, GOOD_PATCH, {"verdict": "reject", "blocking": ["reversed_negation"]}])
    assert batch.reviews[0].verdict.status == "rejected"
    sel = rc.resolve(make_context(), batch, SimpleNamespace(method=auth.method), T3Model)
    assert sel.status == "audit_rejected"
    assert json.loads(sel.selected_json)["event"] == 1     # J0 保留


def test_medical_uncertain_is_not_accepted():
    auth, batch, _ = run_authority(
        [CRITIQUE_HIT, GOOD_PATCH, {"verdict": "uncertain", "blocking": []}])
    sel = rc.resolve(make_context(), batch, SimpleNamespace(method=auth.method), T3Model)
    assert sel.status == "audit_rejected"


def test_unparseable_medical_review_does_not_pass():
    auth, batch, _ = run_authority([CRITIQUE_HIT, GOOD_PATCH, "looks fine to me"])
    assert batch.reviews[0].verdict.status == "rejected"
    assert any("medical_review_unparseable" in c
               for c in batch.reviews[0].verdict.blocking_codes)


def test_producer_rejection_yields_no_executable_proposal():
    bad = json.loads(json.dumps(GOOD_PATCH))
    bad["patches"][0]["evidence_quote"] = "DRE nodule cT2a"     # 记录里没有
    auth, batch, _ = run_authority([CRITIQUE_HIT, bad])
    assert batch.status == "detected" and not batch.reviews
    sel = rc.resolve(make_context(), batch, SimpleNamespace(method=auth.method), T3Model)
    assert sel.status == "detected_no_executable_proposal"


def test_model_failure_degrades_to_error_batch():
    auth, batch, _ = run_authority([RuntimeError("endpoint timeout")])
    assert batch.status == "error"
    sel = rc.resolve(make_context(), batch, SimpleNamespace(method=auth.method), T3Model)
    assert sel.status == "error_fallback_initial"
    assert json.loads(sel.selected_json)["event"] == 1


def test_new_fact_lint_blocks_even_if_medical_accepts():
    """医学审核放过，但重写引入记录外数字 -> 仍不得接受。"""
    risky = json.loads(json.dumps(GOOD_PATCH))
    risky["patches"][2]["after"] = "No BCR through 24 months; PSA 31 noted at follow-up."
    auth, batch, _ = run_authority([CRITIQUE_HIT, risky, MED_ACCEPT])
    verdict = batch.reviews[0].verdict
    assert verdict.status == "rejected"
    assert any("unsupported_new_fact" in c for c in verdict.blocking_codes)


def test_method_string_does_not_claim_independent_expert():
    assert "expert" not in StructuredReflectionAuthority.method
    assert "structured_refine" in StructuredReflectionAuthority.method


def test_meta_reports_calls_and_statuses():
    auth, _, _ = run_authority([CRITIQUE_HIT, GOOD_PATCH, MED_ACCEPT])
    meta = auth.last_meta
    assert meta.model_calls == 3 and meta.issues == 1 and meta.patches == 3
    assert meta.producer_status == "ok" and meta.medical_status == "accept"


def test_j0_digest_mismatch_refuses_to_review():
    """J0 与 subject 绑定的摘要不符时不得审核，避免审错对象。"""
    ctx = make_context()
    ctx = rc.Context(ctx.subject, canonical({"event": 0, "months_to_recurrence": 1.0,
                                             "free_text": "x"}), UPFRONT, ())
    model = StubModel([CRITIQUE_HIT])
    auth = StructuredReflectionAuthority(model)
    assert auth.review(ctx).status == "error"
    assert model.calls == 0


# --- 生产接线：authority 解析优先级 ---------------------------------------
def test_resolve_authority_priority():
    """合成注入优先于生产实现；两者都无则 None。"""
    from chimera_agent_baseline.agent.reflection_authority import resolve_authority

    assert resolve_authority(None) is None

    class R:
        reflection_test = None
        reflection_authority = None

    r = R()
    assert resolve_authority(r) is None

    sentinel_test, sentinel_prod = object(), object()
    r.reflection_authority = sentinel_prod
    assert resolve_authority(r) is sentinel_prod

    r.reflection_test = sentinel_test
    assert resolve_authority(r) is sentinel_test      # 合成注入优先

    del r.reflection_test                              # 老 runtime 无该属性也不炸
    assert resolve_authority(r) is sentinel_prod
