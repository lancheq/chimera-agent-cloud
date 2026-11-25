#!/usr/bin/env python3
"""A 线留出资格：91 例 T1 所属保守风险组的排除足迹。"""
import json
import glob
from collections import Counter, defaultdict

ROOT = "/home/hedongze/CHIMERA-agent"
OUT = f"{ROOT}/output/a_attribution_20261122"
POOL = f"{ROOT}/output/round2_data_pool_20261113_v2/patient_version_map.json"
BASE = f"{ROOT}/output/s4_shared_plan_performance_20261115_v2r1"

pool = json.load(open(POOL))
maps = pool["mappings"]
groups = {g["risk_group_id"]: g for g in pool["groups"]}

t1_cases = sorted({p.strip("/").split("/")[-1]
                   for p in glob.glob(f"{BASE}/worker*/fixed/task1/*/")})

# task1 的 PT-pseudo 记录
by_case = defaultdict(list)
for m in maps:
    by_case[(m["task"], m["case_id"])].append(m)

missing = [c for c in t1_cases if (1, c) not in by_case]
grp_of = {}
for c in t1_cases:
    recs = by_case.get((1, c), [])
    grp_of[c] = sorted({r["risk_group_id"] for r in recs})

excluded = sorted({g for v in grp_of.values() for g in v})
member_tasks = Counter()
member_total = 0
cross = []
for gid in excluded:
    g = groups.get(gid, {})
    idxs = set(g.get("record_indices") or [])
    tasks = Counter()
    for m in maps:
        if m["record_index"] in idxs:
            tasks[m["task"]] += 1
            member_total += 1
    for t, n in tasks.items():
        member_tasks[t] += n
    if g.get("cross_tasks"):
        cross.append(gid)

# 未落入排除组的记录数（可作后续留出候选的池子，仍需其他排除）
kept = [m for m in maps if m["risk_group_id"] not in set(excluded)]
kept_by_task = Counter(m["task"] for m in kept)

report = {
    "t1_cases": len(t1_cases),
    "t1_cases_without_pool_record": missing,
    "excluded_risk_groups": len(excluded),
    "excluded_group_records_total": member_total,
    "excluded_group_records_by_task": dict(sorted(member_tasks.items())),
    "excluded_cross_task_groups": len(cross),
    "total_pool_groups": len(pool["groups"]),
    "total_pool_records": len(maps),
    "remaining_records_after_group_exclusion": len(kept),
    "remaining_records_by_task": dict(sorted(kept_by_task.items())),
    "group_size_distribution_of_excluded": dict(Counter(
        groups[g]["n_records"] for g in excluded if g in groups)),
    "t1_groups_shared_within_t1": sum(
        1 for gid in excluded
        if sum(1 for m in maps if m["risk_group_id"] == gid and m["task"] == 1) > 1),
    "limitation": pool.get("limitation"),
    "patient_identity_confirmed": any(m.get("patient_identity_confirmed") for m in maps),
}
json.dump(report, open(f"{OUT}/holdout_eligibility.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(report, ensure_ascii=False, indent=1))
