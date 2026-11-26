"""Structured Refine 的字段级补丁：解析、校验、构造完整候选、多轮收敛。无模型调用。

设计依据（H04，34 例 T2 可信轨迹）：
- 历史 issues 已是 `<error_type>: <描述>` 形式，但无字段路径与目标值；
- 72 条 issues 中 61 条引用具体数值，PSA/ISUP/PSAD 共出现 67 次，primary 只出现 1 次；
  说明模型检出的错误绝大多数在证据与理由层，不在决策字段；
- Refine 输出是整段重写（21 段→10 段），对应 free_text 全量替换。

T3 字段语义来自仓库现有契约，非本模块假设：
- `prompts.py:202-203` — event = 0 时 months_to_recurrence 取末次随访时长，不是零。
- `postprocess.py:74-76` — event=0 且 months=None 时被 fallback 修补为 36.0，
  说明 null 是待修缺陷而非合法状态。故本模块不接受 months=null。

证据范围是实验边界，不是能力判断：
- 契约 `reflection_contract.py:212-215` 只接受 record_id='rendered_upfront' 且 quote 逐字
  出现在 upfront 记录区的引用。ReAct 工具返回的合法证据无法用于 citations。
  因来源限制被拒的候选单独计数，不得据此判定模型不具备纠错能力。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from chimera_agent_baseline.runtime_audit import canonical, content_digest

T2_PRIMARY = ("active_surveillance", "active_treatment",
              "continued_surveillance", "watchful_waiting")

ALLOWED_PATHS: dict[int, tuple[str, ...]] = {
    1: ("/biopsy_decision", "/confidence", "/free_text"),
    2: ("/treatment_recommendation/primary", "/confidence", "/free_text"),
    3: ("/event", "/months_to_recurrence", "/free_text"),
}

# variable_weights 的键由平台白名单决定，不能在此硬编码。
ALLOWED_PREFIXES: dict[int, tuple[str, ...]] = {
    1: ("/variable_weights/",),
    2: ("/variable_weights/",),
    3: (),
}

# 决策字段：变更时必须同步 free_text。权重与置信度不算决策字段。
DECISION_PATHS: dict[int, tuple[str, ...]] = {
    1: ("/biopsy_decision",),
    2: ("/treatment_recommendation/primary",),
    3: ("/event",),
}

ENUM_DOMAINS: dict[str, tuple[str, ...]] = {
    "/biopsy_decision": ("yes", "no"),
    "/treatment_recommendation/primary": T2_PRIMARY,
    "/confidence": ("clear", "borderline", "unclear"),
    "/event": (0, 1),
}

KNOWN_ERROR_TYPES = frozenset({
    "fabricated_facts", "internal_inconsistency", "under_treatment",
    "over_treatment", "incomplete_rationale", "guideline_misquote",
    "arithmetic_error", "other",
})

REJECT_CODES = frozenset({
    "malformed_patchset", "stale_base", "no_patches", "field_not_allowed",
    "path_missing", "before_mismatch", "type_mismatch", "enum_violation",
    "evidence_missing", "evidence_not_in_record", "unknown_error_type",
    "duplicate_path", "no_change", "cross_field_violation", "months_not_positive",
    "round_rejected",
})

EVIDENCE_SCOPE = "rendered_upfront_only"
MONTHS_SOFT_RANGE = (6.0, 60.0)   # 表单 prompt 声明 6-60，仅记录不拒绝


@dataclass(frozen=True)
class Patch:
    path: str
    before: Any
    after: Any
    error_type: str
    evidence_quote: str
    reason: str


@dataclass(frozen=True)
class PatchSet:
    task: int
    case_id: str
    base_digest: str
    patches: tuple[Patch, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Outcome:
    status: str                       # ok | rejected
    candidate_json: str | None
    changed_fields: tuple[str, ...]
    rejects: tuple[tuple[str, str], ...] = ()
    field_report: tuple[tuple[str, str], ...] = ()   # (path, ok|拒绝码)，逐字段合法性
    collateral_rejected: int = 0                     # 因他字段非法被连带丢弃的合法补丁数
    months_out_of_range: tuple[str, ...] = ()        # 软约束提示，不影响通过


def _walk(value: Any, path: str):
    if not path.startswith("/"):
        raise KeyError(path)
    cur = value
    for seg in path[1:].split("/"):
        seg = seg.replace("~1", "/").replace("~0", "~")
        if not isinstance(cur, dict) or seg not in cur:
            raise KeyError(path)
        cur = cur[seg]
    return cur


def _allowed(task: int, path: str) -> bool:
    if path in ALLOWED_PATHS.get(task, ()):
        return True
    return any(path.startswith(pfx) and len(path) > len(pfx)
               for pfx in ALLOWED_PREFIXES.get(task, ()))


def _type_ok(before: Any, after: Any) -> bool:
    """禁止隐式强转：类型严格一致；bool 不当作 int；null 一律不接受。"""
    if after is None:
        return False
    if isinstance(before, bool) or isinstance(after, bool):
        return isinstance(before, bool) and isinstance(after, bool)
    if isinstance(before, int) and isinstance(after, float):
        return False
    if isinstance(before, float) and isinstance(after, int):
        return False
    return type(before) is type(after)


def parse_patchset(payload: str | dict, task: int, case_id: str, base_digest: str):
    """严格解析。接受纯 JSON 或 JSON 代码块；其余形态一律拒绝并给出代码。"""
    text = payload if isinstance(payload, dict) else str(payload).strip()
    if isinstance(text, str):
        if text.startswith("```"):
            body = text.split("```")[1] if "```" in text[3:] else text
            text = body[body.find("{"):]
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, (("malformed_patchset", f"json: {exc.msg}"),)
    else:
        obj = text

    if not isinstance(obj, dict) or not isinstance(obj.get("patches"), list):
        return None, (("malformed_patchset", "缺少 patches 数组"),)
    out = []
    for i, raw in enumerate(obj["patches"]):
        if not isinstance(raw, dict):
            return None, (("malformed_patchset", f"patch[{i}] 非对象"),)
        try:
            out.append(Patch(path=raw["path"], before=raw["before"], after=raw["after"],
                             error_type=raw["error_type"],
                             evidence_quote=raw["evidence_quote"], reason=raw["reason"]))
        except KeyError as exc:
            return None, (("malformed_patchset", f"patch[{i}] 缺字段 {exc}"),)
    return PatchSet(task, case_id, base_digest, tuple(out)), ()


def _check_patch(p: Patch, base: dict, task: int, upfront: str | None) -> str | None:
    """单补丁校验。None 表示合法，否则返回拒绝码。"""
    if not _allowed(task, p.path):
        return "field_not_allowed"
    try:
        before_val = _walk(base, p.path)
    except KeyError:
        return "path_missing"
    if not _type_ok(before_val, p.after):
        return "type_mismatch"
    if canonical(before_val) != canonical(p.before):
        return "before_mismatch"
    if p.path in ENUM_DOMAINS and p.after not in ENUM_DOMAINS[p.path]:
        return "enum_violation"
    if canonical(before_val) == canonical(p.after):
        return "no_change"
    if not isinstance(p.evidence_quote, str) or not p.evidence_quote.strip():
        return "evidence_missing"
    if not upfront or p.evidence_quote not in upfront:
        return "evidence_not_in_record"
    if p.error_type not in KNOWN_ERROR_TYPES:
        return "unknown_error_type"
    if p.path == "/months_to_recurrence":
        if not isinstance(p.after, (int, float)) or isinstance(p.after, bool) or p.after <= 0:
            return "months_not_positive"
    return None


def apply_patches(base: dict, patchset: PatchSet, *, upfront: str | None) -> Outcome:
    """把补丁打到 base 上，产出完整候选。任何一项不合规即整体拒绝，不做部分应用。"""
    if content_digest(base) != patchset.base_digest:
        return Outcome("rejected", None, (), (("stale_base", "J0 摘要不匹配"),))
    if not patchset.patches:
        return Outcome("rejected", None, (), (("no_patches", "空补丁集"),))

    seen: set[str] = set()
    report: list[tuple[str, str]] = []
    rejects: list[tuple[str, str]] = []
    valid: list[Patch] = []
    for p in patchset.patches:
        if p.path in seen:
            report.append((p.path, "duplicate_path"))
            rejects.append(("duplicate_path", p.path))
            continue
        seen.add(p.path)
        code = _check_patch(p, base, patchset.task, upfront)
        report.append((p.path, code or "ok"))
        if code:
            rejects.append((code, p.path))
        else:
            valid.append(p)

    # 跨字段一致性按「全部补丁」判断意图，不因个别补丁非法而漏判
    claimed = {p.path for p in patchset.patches}
    if patchset.task == 3 and "/event" in claimed:
        if "/months_to_recurrence" not in claimed:
            rejects.append(("cross_field_violation", "event 变更但未同步 months_to_recurrence"))
        else:
            by = {p.path: p for p in patchset.patches}
            mo = by["/months_to_recurrence"].after
            if not isinstance(mo, (int, float)) or isinstance(mo, bool) or mo <= 0:
                rejects.append(("months_not_positive", "event 变更后 months 必须为正"))
    if set(DECISION_PATHS.get(patchset.task, ())) & claimed and "/free_text" not in claimed:
        rejects.append(("cross_field_violation", "决策字段变更但未同步 free_text"))

    if rejects:
        # 连带拒绝：本批全部合法补丁都因他项非法而未被采用
        return Outcome("rejected", None, (), tuple(rejects), tuple(report), len(valid))

    candidate = json.loads(json.dumps(base))
    for p in valid:
        node = candidate
        segs = p.path[1:].split("/")
        for seg in segs[:-1]:
            node = node[seg.replace("~1", "/").replace("~0", "~")]
        node[segs[-1].replace("~1", "/").replace("~0", "~")] = p.after

    changed_fields = tuple(sorted("/" + "/".join(k) for k in _diff_paths(base, candidate)))
    oor = ()
    if "/months_to_recurrence" in changed_fields:
        v = candidate.get("months_to_recurrence")
        if isinstance(v, (int, float)) and not (MONTHS_SOFT_RANGE[0] <= v <= MONTHS_SOFT_RANGE[1]):
            oor = (f"months={v} 超出表单声明的 6-60",)
    return Outcome("ok", canonical(candidate), changed_fields, (), tuple(report), 0, oor)


def _diff_paths(a: Any, b: Any, prefix: tuple[str, ...] = ()):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            yield from _diff_paths(a.get(k), b.get(k), (*prefix, k))
        return
    if canonical(a) != canonical(b):
        yield prefix


# ---------------------------------------------------------------------------
# 多轮收敛：J0 不可变，中间态只作反思工作区
# ---------------------------------------------------------------------------

def collapse_rounds(j0: dict, rounds: list[PatchSet], *, upfront: str | None) -> Outcome:
    """逐轮在临时工作区推进，最终以 J0 为基准重建单一候选。

    不拼接两轮补丁：同一字段可能被重复修改甚至改回原值，串接会产出 before
    与 J0 不符的补丁。这里一律用 J0 当前值作为最终 before，并按 J0 重新校验。
    """
    if not rounds:
        return Outcome("rejected", None, (), (("no_patches", "无轮次"),))
    work = json.loads(json.dumps(j0))
    for ps in rounds:
        if not ps.patches:
            continue          # 该轮无修改意见＝已收敛
        out = apply_patches(work, ps, upfront=upfront)
        if out.status != "ok":
            return Outcome("rejected", None, (), (("round_rejected", str(out.rejects)),),
                           out.field_report, out.collateral_rejected)
        work = json.loads(out.candidate_json)

    changed = sorted("/" + "/".join(k) for k in _diff_paths(j0, work))
    if not changed:
        return Outcome("rejected", None, (), (("no_patches", "多轮净改动为空"),))

    last: dict[str, Patch] = {}
    for ps in rounds:
        for p in ps.patches:
            last[p.path] = p
    blank = Patch("", None, None, "other", "", "")
    collapsed = tuple(
        Patch(path=path,
              before=_walk(j0, path),
              after=_walk(work, path),
              error_type=last.get(path, blank).error_type,
              evidence_quote=last.get(path, blank).evidence_quote,
              reason=last.get(path, blank).reason)
        for path in changed)
    final = PatchSet(rounds[0].task, rounds[0].case_id, content_digest(j0), collapsed)
    return apply_patches(j0, final, upfront=upfront)


# ---------------------------------------------------------------------------
# 三层有效性：Schema / 证据绑定 / 医学合理性
# ---------------------------------------------------------------------------

_NUM = re.compile(r"\d+(?:\.\d+)?")
WATCH_TERMS = ("metastasis", "metastatic", "lymph node", "lymph-node", "bone scan",
               "seminal vesicle", "extracapsular", "invasion", "positive margin")


def new_fact_lint(old_text: str, new_text: str, upfront: str | None) -> tuple[str, ...]:
    """free_text 全量替换的幻觉检查。

    只查「新增的事实性陈述」，不查模型自带的 evidence_quote——后者可能逐字合法
    却与修改内容无关。返回被标记片段；空元组表示未发现新事实。
    """
    flags: list[str] = []
    old_nums = set(_NUM.findall(old_text or ""))
    for n in _NUM.findall(new_text or ""):
        if n in old_nums:
            continue
        if not upfront or n not in upfront:
            flags.append(f"number:{n}")
    low_old = (old_text or "").lower()
    low_new = (new_text or "").lower()
    low_up = (upfront or "").lower()
    for term in WATCH_TERMS:
        if term in low_new and term not in low_old and term not in low_up:
            flags.append(f"term:{term}")
    return tuple(sorted(set(flags)))


@dataclass(frozen=True)
class RuleVerdict:
    status: str                       # accepted | rejected
    reviewed_fields: tuple[str, ...]
    blocking_codes: tuple[str, ...] = ()
    method: str = "rule_based_independent"


def rule_based_review(candidate: dict, base: dict, patchset: PatchSet,
                      *, upfront: str | None) -> RuleVerdict:
    """第一、二层（Schema 合法性、证据绑定）的独立复核。不判断医学合理性。

    method 如实标为工程级审核；医学审核由 MedicalReviewer 承担。
    """
    codes: list[str] = []
    for p in patchset.patches:
        if not _allowed(patchset.task, p.path):
            codes.append(f"field_not_allowed:{p.path}")
        if not upfront or p.evidence_quote not in upfront:
            codes.append(f"evidence_not_in_record:{p.path}")
    if not any(True for _ in _diff_paths(base, candidate)):
        codes.append("no_effective_change")
    for flag in new_fact_lint(base.get("free_text") or "", candidate.get("free_text") or "", upfront):
        codes.append(f"unsupported_new_fact:{flag}")
    return RuleVerdict("rejected" if codes else "accepted",
                       tuple(sorted({p.path for p in patchset.patches})),
                       tuple(sorted(set(codes))))


@dataclass(frozen=True)
class MedicalVerdict:
    status: str                       # accepted | rejected | not_run
    reviewed_fields: tuple[str, ...] = ()
    blocking_codes: tuple[str, ...] = ()
    method: str = "not_implemented"


class MedicalReviewer:
    """第三层：医学合理性。接口留位，实现必须有独立依据。

    约束：即使使用同一 35B 模型的独立调用，也只能称为「独立审核步骤」，
    不得表述为独立医学专家或独立可靠信息源。
    """

    method = "unspecified"

    def review(self, candidate: dict, base: dict, patchset: PatchSet,
               *, upfront: str | None) -> MedicalVerdict:   # pragma: no cover
        raise NotImplementedError


class UnavailableMedicalReviewer(MedicalReviewer):
    """当前无已批准的医学审核实现。返回 not_run，下游不得当作 accepted。"""

    method = "not_implemented"

    def review(self, candidate, base, patchset, *, upfront):   # noqa: D102
        return MedicalVerdict("not_run", tuple(p.path for p in patchset.patches),
                              ("medical_review_not_implemented",), self.method)


# ---------------------------------------------------------------------------
# Structured Refine prompt
# ---------------------------------------------------------------------------

STRUCTURED_REFINE_SYSTEM = """You are a clinical reasoning corrector. You output ONLY a JSON patch set.

INPUTS: the case record (read-only), the original reasoning transcript, and the
auditor's issue list. You may NOT request new information and may NOT use facts
that are absent from the record.

OUTPUT (first character must be '{'):
{"patches": [{
  "path": "<one allowed field path>",
  "before": "<the CURRENT value copied verbatim from the judgment you were given>",
  "after": "<the corrected value>",
  "error_type": "fabricated_facts|internal_inconsistency|under_treatment|over_treatment|incomplete_rationale|guideline_misquote|arithmetic_error|other",
  "evidence_quote": "<a substring copied verbatim from the case record>",
  "reason": "<one sentence>"
}]}

ALLOWED PATHS
  Task 1: /biopsy_decision, /confidence, /variable_weights/<key>, /free_text
  Task 2: /treatment_recommendation/primary, /confidence, /variable_weights/<key>, /free_text
  Task 3: /event, /months_to_recurrence, /free_text

RULES
1. "before" must equal the current value exactly; a mismatch invalidates the patch.
2. Do NOT change the type. Task 3 /event is 0 or 1. Numbers stay numbers.
3. "evidence_quote" must appear verbatim in the case record. Paraphrase is rejected.
4. If a decision field changes, /free_text MUST be patched in the same set.
5. Task 3: if /event changes, /months_to_recurrence MUST change with it and stay a
   positive number. For event = 0, months is the last follow-up duration, not zero
   and not null. For event = 1, months is the projected time to recurrence.
6. Every number and clinical finding you introduce in /free_text must already exist
   in the case record. Do not add new findings while rewriting.
7. If nothing is wrong, output {"patches": []}. An empty set is a valid answer.
8. Do NOT output reasoning, markdown, or commentary outside the JSON object.
"""


def to_correction_proposal(outcome: Outcome, patchset: PatchSet, subject, *,
                           mechanism="external_graph"):
    """把已通过校验的候选包装成契约对象。延迟导入避免循环依赖。"""
    from chimera_agent_baseline.agent.reflection_contract import (
        Citation, CorrectionProposal,
    )
    if outcome.status != "ok":
        raise ValueError("只能包装已接受的候选")
    seen: list = []
    for p in patchset.patches:
        cite = Citation(record_id="rendered_upfront", quote=p.evidence_quote, claim=p.reason)
        if all(c.quote != cite.quote for c in seen):
            seen.append(cite)
    return CorrectionProposal(
        subject=subject,
        mechanism=mechanism,
        candidate_json=outcome.candidate_json,
        candidate_digest=content_digest(json.loads(outcome.candidate_json)),
        # 契约的 changed_fields 是顶层键粒度（reflection_contract.py:205），
        # 本模块内部用 JSON pointer 更精确，跨边界时必须降级映射。
        changed_fields=tuple(sorted({p.split("/")[1] for p in outcome.changed_fields})),
        reason="; ".join(p.reason for p in patchset.patches)[:1000],
        citations=tuple(seen),
    )
