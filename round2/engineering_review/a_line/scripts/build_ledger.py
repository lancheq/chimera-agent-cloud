#!/usr/bin/env python3
"""A 线只读账本重建：逐例策略调用、预算状态与"可避免策略调用"可判定性。

只读既有 v2r1 产物；不写共享工程；不调用模型。
输出：ledger.json / avoidable_policy_calls.json / accounting.json
"""
import json
import glob
import os
from collections import Counter, defaultdict

ROOT = "/home/hedongze/CHIMERA-agent"
BASE = f"{ROOT}/output/s4_shared_plan_performance_20261115_v2r1"
OUT = f"{ROOT}/output/a_attribution_20261122"
ANALYSIS = f"{ROOT}/output/s4_v2r1_deployment_20261115_122757/analysis/analysis.json"
PAIRS = f"{ROOT}/output/s4_mechanism_review_20261115/pair_metadata_private.json"

os.makedirs(OUT, exist_ok=True)


def resolve(case_id):
    """返回 case_id -> {arm: case_dir}"""
    found = {}
    for arm in ("fixed", "adaptive"):
        hits = glob.glob(f"{BASE}/worker*/{arm}/task1/{case_id}")
        if len(hits) != 1:
            raise RuntimeError(f"{case_id}/{arm} 命中 {len(hits)} 个目录")
        found[arm] = hits[0]
    return found


def load_events(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


analysis = {r["case_id"]: r for r in json.load(open(ANALYSIS))["rows"]}
pairs = {r["case_id"]: r for r in json.load(open(PAIRS))}

cases = sorted(pairs)
if len(cases) != 91:
    raise RuntimeError(f"期望 91 例，实际 {len(cases)}")

ledger = []
for cid in cases:
    dirs = resolve(cid)
    a = analysis[cid]
    p = pairs[cid]
    rec = {
        "case_id": cid,
        "risk_group_id": p["risk_group_id"],
        "label": a["label"],
        "fixed_correct": bool(p["fixed_correct"]),
        "adaptive_correct": bool(p["adaptive_correct"]),
        "group": ("both_right" if p["fixed_correct"] and p["adaptive_correct"]
                  else "only_fixed" if p["fixed_correct"]
                  else "only_adaptive" if p["adaptive_correct"] else "both_wrong"),
        "fixed_only_sections": p["fixed_only_sections"],
        "adaptive_only_sections": p["adaptive_only_sections"],
        "shared_sections_with_different_payloads": p["shared_sections_with_different_payloads"],
        "information_saved": p["information_saved"],
        "arms": {},
    }
    for arm in ("fixed", "adaptive"):
        ev = load_events(os.path.join(dirs[arm], "candidate_events.jsonl"))
        stop = [e for e in ev if e["kind"] == "acquisition_stop"]
        pol = [e for e in ev if e["kind"] == "policy_decision"]
        treq = [e for e in ev if e["kind"] == "tool_request"]
        by_stage = a[arm]["compute"]["by_stage"]
        rec["arms"][arm] = {
            "final_correct": bool(a[arm]["final_correct"]),
            "information_units": a[arm]["information_units"],
            "admitted_attempts": a[arm]["admitted_attempts"],
            "model_calls_by_stage": by_stage,
            "model_calls_total": sum(by_stage.values()),
            "input_tokens": a[arm]["compute"]["input_tokens"],
            "output_tokens": a[arm]["compute"]["output_tokens"],
            "policy_turns": len(pol),
            "tool_requests": len(treq),
            "stop_count": len(stop),
            "stop_reason": stop[0]["reason"] if stop else None,
            "stop_explanation_present": bool(stop and stop[0].get("explanation")),
            "tool_budgets": [t["budget_before"] for t in treq],
        }
        if arm == "adaptive":
            b = (stop[0].get("budget") or {}) if stop else {}
            pol_actions = [e["decision"].get("action") for e in pol]
            rec["arms"][arm].update({
                "stop_ledger": b,
                "policy_actions": pol_actions,
                "stop_turn_remaining_plan": (pol[-1]["decision"].get("remaining_plan") if pol else None),
                "stop_turn_reason_chars": (len(pol[-1]["decision"].get("reason") or "") if pol else 0),
            })
    ledger.append(rec)

json.dump(ledger, open(f"{OUT}/ledger.json", "w"), ensure_ascii=False, indent=1)

# ---------- 可避免策略调用：可判定性 ----------
rows = []
for rec in ledger:
    ad = rec["arms"]["adaptive"]
    b = ad["stop_ledger"]
    spent, budget = b.get("spent_units"), b.get("budget_units")
    adm, mx = b.get("admitted_attempts"), b.get("max_attempts")
    info_exh = spent is not None and budget is not None and spent >= budget
    att_exh = adm is not None and mx is not None and adm >= mx
    # 预算耗尽后仍被实际派发的尝试（证明耗尽 ≠ 无合法动作）
    post = [t for t in ad["tool_budgets"]
            if t.get("spent_units") is not None and t.get("budget_units") is not None
            and t["spent_units"] >= t["budget_units"]]
    rows.append({
        "case_id": rec["case_id"],
        "policy_turns": ad["policy_turns"],
        "stop_reason": ad["stop_reason"],
        "spent_units": spent,
        "budget_units": budget,
        "admitted_attempts": adm,
        "max_attempts": mx,
        "information_budget_exhausted": info_exh,
        "attempt_budget_exhausted": att_exh,
        "controller_provable_no_legal_action": bool(info_exh and att_exh),
        "post_exhaustion_dispatched_attempts": len(post),
        "stop_explanation_present": ad["stop_explanation_present"],
        "stop_remaining_plan_empty": ad["stop_turn_remaining_plan"] == [],
    })

grp = Counter()
for r in rows:
    grp[("info_exh", r["information_budget_exhausted"])] += 1
    grp[("att_exh", r["attempt_budget_exhausted"])] += 1
    grp[("provable", r["controller_provable_no_legal_action"])] += 1

summary = {
    "n_cases": len(rows),
    "stop_reason_all_policy_stop": all(r["stop_reason"] == "policy_stop" for r in rows),
    "policy_calls_total": sum(r["policy_turns"] for r in rows),
    "model_calls_adaptive_total": sum(r["arms"]["adaptive"]["model_calls_total"] for r in ledger),
    "model_calls_fixed_total": sum(r["arms"]["fixed"]["model_calls_total"] for r in ledger),
    "information_budget_exhausted_cases": sum(1 for r in rows if r["information_budget_exhausted"]),
    "attempt_budget_exhausted_cases": sum(1 for r in rows if r["attempt_budget_exhausted"]),
    "controller_provable_cases": sum(1 for r in rows if r["controller_provable_no_legal_action"]),
    "post_exhaustion_attempt_cases": sum(1 for r in rows if r["post_exhaustion_dispatched_attempts"] > 0),
    "post_exhaustion_attempt_total": sum(r["post_exhaustion_dispatched_attempts"] for r in rows),
    "max_admitted_attempts": max(r["admitted_attempts"] for r in rows),
    "max_attempts_cap": rows[0]["max_attempts"],
    "stop_explanation_present_cases": sum(1 for r in rows if r["stop_explanation_present"]),
    "stop_remaining_plan_empty_cases": sum(1 for r in rows if r["stop_remaining_plan_empty"]),
    "policy_turns_distribution": dict(Counter(r["policy_turns"] for r in rows)),
}
json.dump({"summary": summary, "per_case": rows},
          open(f"{OUT}/avoidable_policy_calls.json", "w"), ensure_ascii=False, indent=1)

# ---------- 分组与计量 ----------
groups = Counter(rec["group"] for rec in ledger)
accounting = {
    "groups": dict(groups),
    "fixed_correct": sum(rec["fixed_correct"] for rec in ledger),
    "adaptive_correct": sum(rec["adaptive_correct"] for rec in ledger),
    "information_units_fixed": sum(rec["arms"]["fixed"]["information_units"] for rec in ledger),
    "information_units_adaptive": sum(rec["arms"]["adaptive"]["information_units"] for rec in ledger),
    "policy_calls_adaptive": sum(rec["arms"]["adaptive"]["policy_turns"] for rec in ledger),
    "risk_groups": len({rec["risk_group_id"] for rec in ledger}),
    "only_adaptive": [rec["case_id"] for rec in ledger if rec["group"] == "only_adaptive"],
    "only_fixed": [rec["case_id"] for rec in ledger if rec["group"] == "only_fixed"],
    "both_wrong": [rec["case_id"] for rec in ledger if rec["group"] == "both_wrong"],
}
json.dump(accounting, open(f"{OUT}/accounting.json", "w"), ensure_ascii=False, indent=1)

print(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps({k: v for k, v in accounting.items() if not isinstance(v, list)}, ensure_ascii=False, indent=1))
