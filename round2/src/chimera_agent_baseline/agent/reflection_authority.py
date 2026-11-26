"""生产版 Reflection authority：挂在 form_fill 的 G1R 入口，替代测试注入。

为什么不接在 graph 的 reflect 节点：
    graph.py 的边是 ``reflect -> form_fill``，J0 由 form_fill 解析产生
    （form_fill.py:320 校验、410 记 parsed_judgment）。reflect 节点运行时
    尚不存在完整 Judgment；要给它 J0 就得再调一次 Form Fill，那等于隐式
    增加一次模型调用并改变实验流程。

因此真实入口是 form_fill.py:479-488 的 ``authority.review(context)``：
此处 J0（context.initial_json）、upfront 证据、subject 均已就绪，
且下游 resolve() 已在消费 ReviewBatch。本模块只实现一个 authority，
不新增调用点。

审核失败、超时、未审核、解析不出结论，一律不得默认通过：
- 任何异常 -> ReviewBatch('error')，下游回退保留 J0
- 医学审核 uncertain / 未实现 -> AuditVerdict 非 accepted，下游保留 J0
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from chimera_agent_baseline.agent.reflection_contract import (
    AuditVerdict, CorrectionProposal, Review, ReviewBatch,
)
from chimera_agent_baseline.agent.reflection_patch import (
    STRUCTURED_REFINE_SYSTEM, MedicalReviewer, apply_patches, new_fact_lint,
    parse_patchset, to_correction_proposal,
)
from chimera_agent_baseline.runtime_audit import canonical, content_digest

CRITIQUE_SYSTEM = """You are a clinical reasoning auditor. You output ONLY a JSON object.

Audit the judgment below against the case record. Check: fabricated facts,
internal inconsistency, wrong arithmetic on values present in the record,
guideline misquotation, over- or under-treatment relative to stated risk.

Output (first character must be '{'):
{"issues": ["<specific issue>", ...], "severity": "none|mild|moderate|severe"}
If nothing is wrong: {"issues": [], "severity": "none"}
Do NOT restate these instructions. Do NOT use facts absent from the record.
"""

MEDICAL_REVIEW_SYSTEM = """You are reviewing a proposed correction to a clinical judgment.
You see ONLY the case record and the proposed changes. You do NOT see ground truth.

Answer four questions for each proposed change:
1 FACT      Is every clinical fact after the change consistent with the record?
2 NECESSITY Was the stated problem real in the original judgment?
3 SUPPORT   Does the record actually support the new value, or is it unsupported?
4 COHERENCE Do the changed fields and the rationale agree with each other?
Watch specifically for reversed negation (no metastasis -> metastasis), changed
time points, unit confusion (free vs total PSA), and misattributed findings.

Output (first character must be '{'):
{"verdict": "accept|reject|uncertain", "blocking": ["<code>", ...],
 "notes": "<one sentence>"}
- reject    : at least one change is medically wrong or unsupported
- uncertain : the record is insufficient to judge
- accept    : every change is supported by the record
"""


@dataclass
class ReflectionRunMeta:
    model_calls: int = 0
    issues: int = 0
    patches: int = 0
    producer_status: str = "not_run"
    medical_status: str = "not_run"
    blocking: tuple[str, ...] = ()


class StructuredReflectionAuthority:
    """form_fill 侧 authority 的生产实现。method 会写进 AuditVerdict。

    ``review(context)`` 是唯一入口，签名与契约一致，不额外新增调用点。
    """

    method = "structured_refine_independent_llm_review"

    def __init__(self, model, *, max_iterations: int = 1,
                 medical_reviewer: MedicalReviewer | None = None,
                 medical_with_model: bool = True):
        self.model = model
        self.max_iterations = max(1, int(max_iterations))
        self.medical_reviewer = medical_reviewer
        self.medical_with_model = medical_with_model
        self.last_meta = ReflectionRunMeta()

    # -- 内部 ------------------------------------------------------------
    def _ask(self, system: str, user: str) -> str:
        resp = self.model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        self.last_meta.model_calls += 1
        return resp.content if isinstance(resp.content, str) else json.dumps(resp.content)

    def _critique(self, j0: dict, upfront: str) -> tuple[str, ...]:
        raw = self._ask(CRITIQUE_SYSTEM,
                        "Case record:\n\"\"\"\n" + (upfront or "") + "\n\"\"\"\n\n"
                        "Judgment under audit:\n" + json.dumps(j0, ensure_ascii=False, indent=2))
        try:
            obj = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        except Exception:  # noqa: BLE001
            return ()
        issues = obj.get("issues") or []
        return tuple(str(i) for i in issues if str(i).strip())

    def _medical(self, candidate: dict, j0: dict, patches, upfront: str,
                 *, task: int) -> tuple[str, tuple[str, ...]]:
        if self.medical_reviewer is not None:
            v = self.medical_reviewer.review(candidate, j0, patches, upfront=upfront)
            self.last_meta.medical_status = v.status
            return v.status, v.blocking_codes
        if not self.medical_with_model:
            self.last_meta.medical_status = "not_run"
            return "not_run", ("medical_review_not_run",)

        diffs = [{"path": p.path, "before": p.before, "after": p.after,
                  "reason": p.reason, "quote": p.evidence_quote} for p in patches.patches]
        raw = self._ask(MEDICAL_REVIEW_SYSTEM,
                        "Case record:\n\"\"\"\n" + (upfront or "") + "\n\"\"\"\n\n"
                        "Original judgment:\n" + json.dumps(j0, ensure_ascii=False, indent=2)
                        + "\n\nProposed changes:\n" + json.dumps(diffs, ensure_ascii=False, indent=2))
        try:
            obj = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            verdict = str(obj.get("verdict", "")).lower()
            blocking = tuple(str(b) for b in (obj.get("blocking") or ()))
        except Exception:  # noqa: BLE001
            verdict, blocking = "reject", ("medical_review_unparseable",)
        if verdict not in ("accept", "reject", "uncertain"):
            verdict, blocking = "reject", blocking + ("medical_review_invalid_verdict",)
        self.last_meta.medical_status = verdict
        return verdict, blocking

    # -- 契约入口 --------------------------------------------------------
    def review(self, context) -> ReviewBatch:
        self.last_meta = ReflectionRunMeta()
        try:
            return self._review(context)
        except Exception as exc:  # noqa: BLE001
            self.last_meta.blocking = (f"authority_error:{type(exc).__name__}",)
            return ReviewBatch("error")

    def _review(self, context) -> ReviewBatch:
        subject = context.subject
        task = subject.task
        j0 = json.loads(context.initial_json)
        if content_digest(j0) != subject.base_digest:
            return ReviewBatch("error")          # J0 身份不符，不审
        upfront = context.upfront or ""

        issues = self._critique(j0, upfront)
        self.last_meta.issues = len(issues)
        if not issues:
            return ReviewBatch("no_issue")

        raw_patch = self._ask(
            STRUCTURED_REFINE_SYSTEM,
            "Current judgment (copy `before` from here):\n"
            + json.dumps(j0, ensure_ascii=False, indent=2)
            + "\n\nCase record (quote evidence from here):\n\"\"\"\n" + upfront
            + "\n\"\"\"\n\nAuditor issues:\n"
            + "\n".join(f"  {i+1}. {x}" for i, x in enumerate(issues)))
        pset, _ = parse_patchset(raw_patch, task, subject.case_id, content_digest(j0))
        if pset is None:
            return ReviewBatch("detected")       # 检测到问题但无可执行提议
        self.last_meta.patches = len(pset.patches)

        out = apply_patches(j0, pset, upfront=upfront)
        self.last_meta.producer_status = out.status
        if out.status != "ok":
            return ReviewBatch("detected")       # 补丁被拒，保留 J0

        candidate = json.loads(out.candidate_json)
        lint = new_fact_lint(j0.get("free_text") or "",
                             candidate.get("free_text") or "", upfront)
        medical, blocking = self._medical(candidate, j0, pset, upfront, task=task)

        codes = tuple(f"unsupported_new_fact:{f}" for f in lint) + tuple(blocking)
        accepted = (medical == "accept") and not lint and not blocking
        proposal = to_correction_proposal(out, pset, subject)
        verdict = AuditVerdict(
            subject=subject,
            mechanism="external_graph",
            proposal_digest=content_digest(asdict(proposal)),
            candidate_digest=proposal.candidate_digest,
            status="accepted" if accepted else "rejected",
            reviewed_fields=tuple(sorted(candidate)),
            method=self.method,
            blocking_codes=() if accepted else (codes or ("medical_review_rejected",)),
        )
        self.last_meta.blocking = verdict.blocking_codes
        return ReviewBatch("detected", (Review(proposal, verdict),))


def allowed_evidence_preview(upfront: str | None, limit: int = 120) -> str:
    """调试/日志用：只暴露 upfront 前若干字符，避免把工具返回混进证据视图。"""
    return (upfront or "")[:limit]
