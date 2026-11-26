"""G0-C1 正负对照：Structured Refine 补丁 → 确定性候选 → 独立审核 → 契约消费者。

覆盖任务卡 §6.1 八场景，外加多轮收敛、三层有效性、医学审核留位、端到端。
全部零模型调用。
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chimera_agent_baseline.agent import reflection_contract as rc  # noqa: E402
from chimera_agent_baseline.agent.reflection_patch import (  # noqa: E402
    Patch, PatchSet, UnavailableMedicalReviewer, apply_patches, collapse_rounds,
    new_fact_lint, parse_patchset, rule_based_review, to_correction_proposal,
)
from chimera_agent_baseline.runtime_audit import canonical, content_digest  # noqa: E402

MSG = """You are a clinical agent. Read the record below.

Clinical data (read-only — values loaded from the record):
Patient: 56y, PSA 4.8 in Sep 2022 rising to 13.5 in Jan 2025.
mpMRI: PI-RADS 5 lesion, 12 mm, right posterior peripheral zone.
Biopsy 2024: ISUP Grade Group 1, 2/12 cores.
PSA density: 0.452 ng/mL/mL.
Last follow-up: 24 months, no BCR observed.

Information you can request via tools
- prior biopsy history
"""
UPFRONT = rc.upfront_record(MSG)

J0_T2 = {
    "treatment_recommendation": {"primary": "active_surveillance"},
    "confidence": "clear",
    "variable_weights": {"psad": "minor"},
    "free_text": "Low-grade disease on biopsy supports surveillance.",
    "repeat_test": None,
}
J0_T3 = {"event": 1, "months_to_recurrence": 18.0,
         "free_text": "Recurrence projected at 18 months from treatment."}


def base(j0=None):
    j0 = j0 or J0_T2
    return j0, content_digest(j0)


def ps(task, j0, patches):
    return PatchSet(task, "C-001", content_digest(j0), tuple(patches))


def patch(path, before, after, etype="internal_inconsistency",
          quote="PSA density: 0.452 ng/mL/mL.", reason="record value differs"):
    return Patch(path, before, after, etype, quote, reason)


# --- 1 合法修改：构造完整候选，未提及字段保原值 -------------------------
def test_valid_patch_builds_complete_candidate():
    j0, _ = base()
    out = apply_patches(j0, ps(2, j0, [
        patch("/treatment_recommendation/primary", "active_surveillance", "active_treatment",
              etype="under_treatment", quote="PI-RADS 5 lesion, 12 mm"),
        patch("/free_text", j0["free_text"],
              "PI-RADS 5 with PSAD 0.452 supports active treatment."),
    ]), upfront=UPFRONT)
    assert out.status == "ok", out.rejects
    cand = json.loads(out.candidate_json)
    assert cand["treatment_recommendation"]["primary"] == "active_treatment"
    assert cand["confidence"] == "clear"
    assert out.changed_fields == ("/free_text", "/treatment_recommendation/primary")
    assert dict(out.field_report)["/free_text"] == "ok"


# --- 2 无修改意见 ------------------------------------------------------
def test_no_issues_yields_no_candidate():
    j0, _ = base()
    out = apply_patches(j0, ps(2, j0, []), upfront=UPFRONT)
    assert out.status == "rejected" and out.candidate_json is None
    assert out.rejects[0][0] == "no_patches"


# --- 3 含糊补丁不可转换 -------------------------------------------------
def test_vague_patch_is_unconvertible():
    set_, rejects = parse_patchset('{"patches":[{"path":"/free_text"}]}', 2, "C-001", "x")
    assert set_ is None and rejects[0][0] == "malformed_patchset"


# --- 4 未授权字段 ------------------------------------------------------
def test_unauthorized_field_rejected():
    j0, _ = base()
    out = apply_patches(j0, ps(2, j0, [
        patch("/repeat_test", None, "PSA at 3 months")]), upfront=UPFRONT)
    assert out.status == "rejected"
    assert ("field_not_allowed", "/repeat_test") in out.rejects


# --- 5 引用未获取证据（工具返回也算越界，本轮属实验边界）---------------
def test_evidence_not_in_record_rejected():
    j0, _ = base()
    out = apply_patches(j0, ps(2, j0, [
        patch("/free_text", j0["free_text"], "新理由", quote="DRE nodule cT2a confirmed")]),
        upfront=UPFRONT)
    assert out.status == "rejected"
    assert ("evidence_not_in_record", "/free_text") in out.rejects


# --- 6 候选与本次 J0 不匹配 ---------------------------------------------
def test_before_mismatch_and_stale_base():
    j0, _ = base()
    out = apply_patches(j0, ps(2, j0, [
        patch("/confidence", "borderline", "unclear")]), upfront=UPFRONT)
    assert ("before_mismatch", "/confidence") in out.rejects
    stale = PatchSet(2, "C-001", "deadbeef", (patch("/free_text", j0["free_text"], "x"),))
    assert apply_patches(j0, stale, upfront=UPFRONT).rejects[0][0] == "stale_base"


# --- 7 合法候选通过独立工程审核 -----------------------------------------
def test_independent_review_accepts_valid_candidate():
    j0, _ = base()
    set_ = ps(2, j0, [
        patch("/free_text", j0["free_text"],
              "PI-RADS 5 with PSAD 0.452 supports active treatment."),
        patch("/treatment_recommendation/primary", "active_surveillance", "active_treatment",
              etype="under_treatment", quote="PI-RADS 5 lesion, 12 mm"),
    ])
    out = apply_patches(j0, set_, upfront=UPFRONT)
    verdict = rule_based_review(json.loads(out.candidate_json), j0, set_, upfront=UPFRONT)
    assert verdict.status == "accepted"
    assert verdict.method == "rule_based_independent"


# --- 8 跨字段冲突：决策改了理由没改 -------------------------------------
def test_cross_field_conflict_rejected():
    j0, _ = base()
    out = apply_patches(j0, ps(2, j0, [
        patch("/treatment_recommendation/primary", "active_surveillance", "active_treatment",
              etype="under_treatment", quote="PI-RADS 5 lesion, 12 mm")]), upfront=UPFRONT)
    assert any(c == "cross_field_violation" for c, _ in out.rejects)


# --- 9 权重可补丁；枚举与数值守卫 ---------------------------------------
def test_weight_field_is_patchable():
    j0, _ = base()
    out = apply_patches(j0, ps(2, j0, [
        patch("/variable_weights/psad", "minor", "important")]), upfront=UPFRONT)
    assert out.status == "ok", out.rejects
    assert out.changed_fields == ("/variable_weights/psad",)


def test_enum_violation_rejected():
    j0, _ = base()
    out = apply_patches(j0, ps(2, j0, [
        patch("/confidence", "clear", "very_clear")]), upfront=UPFRONT)
    assert any(c == "enum_violation" for c, _ in out.rejects)


def test_no_implicit_numeric_coercion():
    out = apply_patches(J0_T3, PatchSet(3, "C-001", content_digest(J0_T3), (
        patch("/months_to_recurrence", 18.0, 24, quote="Last follow-up: 24 months"),
        patch("/free_text", J0_T3["free_text"], "Recurrence at 24 months."),
    )), upfront=UPFRONT)
    assert any(c == "type_mismatch" for c, _ in out.rejects)


# --- 10 T3：event/months 联动，months 必须为正，不接受 null -------------
def test_t3_event_months_must_move_together():
    out = apply_patches(J0_T3, PatchSet(3, "C-001", content_digest(J0_T3), (
        patch("/event", 1, 0, quote="Last follow-up: 24 months"),)), upfront=UPFRONT)
    assert any(c == "cross_field_violation" for c, _ in out.rejects)


def test_t3_null_months_rejected():
    """官方语义：event=0 时 months 为末次随访时长；null 是缺陷，会被 fallback 修补。"""
    out = apply_patches(J0_T3, PatchSet(3, "C-001", content_digest(J0_T3), (
        patch("/event", 1, 0, quote="Last follow-up: 24 months"),
        patch("/months_to_recurrence", 18.0, None, quote="Last follow-up: 24 months"),
        patch("/free_text", J0_T3["free_text"], "No recurrence observed."),
    )), upfront=UPFRONT)
    assert any(c == "months_not_positive" for c, _ in out.rejects)


def test_t3_valid_flip_uses_follow_up_months():
    out = apply_patches(J0_T3, PatchSet(3, "C-001", content_digest(J0_T3), (
        patch("/event", 1, 0, quote="Last follow-up: 24 months"),
        patch("/months_to_recurrence", 18.0, 24.0, quote="Last follow-up: 24 months"),
        patch("/free_text", J0_T3["free_text"],
              "No BCR observed through 24 months of follow-up."),
    )), upfront=UPFRONT)
    assert out.status == "ok", out.rejects
    cand = json.loads(out.candidate_json)
    assert cand["event"] == 0 and cand["months_to_recurrence"] == 24.0


def test_t3_months_alone_is_legal():
    out = apply_patches(J0_T3, PatchSet(3, "C-001", content_digest(J0_T3), (
        patch("/months_to_recurrence", 18.0, 21.5, quote="Last follow-up: 24 months"),
    )), upfront=UPFRONT)
    assert out.status == "ok", out.rejects


# --- 11 连带拒绝计量 ----------------------------------------------------
def test_collateral_rejection_accounted():
    j0, _ = base()
    out = apply_patches(j0, ps(2, j0, [
        patch("/confidence", "clear", "borderline"),
        patch("/free_text", j0["free_text"], "PSAD 0.452 noted."),
        patch("/biopsy_decision", "no", "yes"),
    ]), upfront=UPFRONT)
    assert out.status == "rejected"
    assert out.collateral_rejected == 2
    assert dict(out.field_report)["/confidence"] == "ok"
    assert dict(out.field_report)["/biopsy_decision"] == "field_not_allowed"


# --- 12 多轮收敛：J0 不可变，不拼接补丁 --------------------------------
def test_multiround_same_field_final_based_on_j0():
    j0, _ = base()
    r1 = ps(2, j0, [
        patch("/treatment_recommendation/primary", "active_surveillance", "active_treatment",
              etype="under_treatment", quote="PI-RADS 5 lesion, 12 mm"),
        patch("/free_text", j0["free_text"], "PI-RADS 5, active treatment."),
    ])
    j1 = json.loads(apply_patches(j0, r1, upfront=UPFRONT).candidate_json)
    r2 = PatchSet(2, "C-001", content_digest(j1), (
        patch("/treatment_recommendation/primary", "active_treatment",
              "continued_surveillance", quote="ISUP Grade Group 1"),
        patch("/free_text", j1["free_text"], "ISUP GG1, continued surveillance."),
    ))
    out = collapse_rounds(j0, [r1, r2], upfront=UPFRONT)
    assert out.status == "ok", out.rejects
    cand = json.loads(out.candidate_json)
    assert cand["treatment_recommendation"]["primary"] == "continued_surveillance"
    assert out.changed_fields == ("/free_text", "/treatment_recommendation/primary")


def test_multiround_revert_to_original_is_no_net_change():
    j0, _ = base()
    r1 = ps(2, j0, [patch("/confidence", "clear", "borderline")])
    j1 = json.loads(apply_patches(j0, r1, upfront=UPFRONT).candidate_json)
    r2 = PatchSet(2, "C-001", content_digest(j1), (
        patch("/confidence", "borderline", "clear"),))
    out = collapse_rounds(j0, [r1, r2], upfront=UPFRONT)
    assert out.status == "rejected" and out.rejects[0][0] == "no_patches"


def test_multiround_invalid_second_round_rejects_whole():
    j0, _ = base()
    r1 = ps(2, j0, [patch("/confidence", "clear", "borderline")])
    j1 = json.loads(apply_patches(j0, r1, upfront=UPFRONT).candidate_json)
    r2 = PatchSet(2, "C-001", content_digest(j1), (
        patch("/confidence", "borderline", "totally_sure"),))
    out = collapse_rounds(j0, [r1, r2], upfront=UPFRONT)
    assert out.status == "rejected" and out.rejects[0][0] == "round_rejected"


def test_multiround_empty_second_round_converges():
    j0, _ = base()
    r1 = ps(2, j0, [patch("/confidence", "clear", "borderline")])
    j1 = json.loads(apply_patches(j0, r1, upfront=UPFRONT).candidate_json)
    r2 = PatchSet(2, "C-001", content_digest(j1), ())
    out = collapse_rounds(j0, [r1, r2], upfront=UPFRONT)
    assert out.status == "ok", out.rejects
    assert out.changed_fields == ("/confidence",)


# --- 13 三层有效性：新事实检查 -----------------------------------------
def test_new_fact_lint_flags_unsupported_number_and_term():
    flags = new_fact_lint("PSA 4.8 rising to 13.5.",
                          "PSA 18 with lymph node metastasis.",
                          "PSA 4.8 rising to 13.5. mpMRI PI-RADS 5.")
    assert "number:18" in flags and "term:lymph node" in flags


def test_new_fact_lint_passes_when_supported():
    assert new_fact_lint("PSA 4.8.", "PSA 4.8 and PSAD 0.452.", "PSA 4.8, PSAD 0.452") == ()


def test_rule_review_blocks_unsupported_new_fact():
    """逐字 evidence_quote 合法，但重写引入记录外新事实 → 工程审核须拦。"""
    j0, _ = base()
    set_ = ps(2, j0, [
        patch("/free_text", j0["free_text"],
              "PI-RADS 5 with PSA 18 and lymph node metastasis on imaging."),
    ])
    out = apply_patches(j0, set_, upfront=UPFRONT)
    assert out.status == "ok", out.rejects
    verdict = rule_based_review(json.loads(out.candidate_json), j0, set_, upfront=UPFRONT)
    assert verdict.status == "rejected"
    assert any(c.startswith("unsupported_new_fact") for c in verdict.blocking_codes)


# --- 14 医学审核未实现时必须显式 not_run -------------------------------
def test_medical_reviewer_reports_not_run():
    j0, _ = base()
    set_ = ps(2, j0, [patch("/free_text", j0["free_text"], "PSAD 0.452 noted.")])
    out = apply_patches(j0, set_, upfront=UPFRONT)
    v = UnavailableMedicalReviewer().review(json.loads(out.candidate_json), j0, set_,
                                            upfront=UPFRONT)
    assert v.status == "not_run" and v.method == "not_implemented"
    assert "medical_review_not_implemented" in v.blocking_codes


# --- 15 端到端：Producer → 契约消费者 resolve() → 最终选择 -------------
def test_end_to_end_through_contract_consumer():
    from pydantic import create_model
    T3Model = create_model("T3Judgment", event=(int, ...),
                           months_to_recurrence=(float, ...), free_text=(str, ...))

    j0 = dict(J0_T3)
    set_ = PatchSet(3, "T3-001", content_digest(j0), (
        patch("/event", 1, 0, quote="Last follow-up: 24 months"),
        patch("/months_to_recurrence", 18.0, 24.0, quote="Last follow-up: 24 months"),
        patch("/free_text", j0["free_text"],
              "No BCR observed through 24 months of follow-up."),
    ))
    out = apply_patches(j0, set_, upfront=UPFRONT)
    assert out.status == "ok", out.rejects

    subject = rc.Subject("run-1", 3, "T3-001", "profile",
                         rc.DraftArtifact("d1", "d2"), content_digest(j0),
                         rc.upfront_digest(UPFRONT), "proc")
    proposal = to_correction_proposal(out, set_, subject)
    cand = json.loads(proposal.candidate_json)
    verdict = rc.AuditVerdict(subject, "external_graph", content_digest(asdict(proposal)),
                              proposal.candidate_digest, "accepted",
                              tuple(sorted(cand)), "rule_based_independent")
    batch = rc.ReviewBatch("detected", (rc.Review(proposal, verdict),))
    ctx = rc.Context(subject, canonical(j0), UPFRONT, ())
    sel = rc.resolve(ctx, batch, SimpleNamespace(method="rule_based_independent"), T3Model)

    assert sel.status == "accepted_candidate"
    assert sel.selected_json == proposal.candidate_json
    assert json.loads(sel.selected_json)["event"] == 0
