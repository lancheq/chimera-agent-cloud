"""H04：Refine 修改形态与 issues 表达抽取。只读。"""
import json
import os
import re
import collections

trust = json.load(open("discussion/audit_20261125/P0_2_trace_trust.json"))
gate = json.load(open("discussion/audit_20261125/H04_sample_gate.json"))
cases = list(gate["refined_cases"])
print(f"样本 {len(cases)} 例，全部 T2")


def rd(p):
    return open(p, encoding="utf-8", errors="ignore").read() if os.path.exists(p) else ""


allissues = []
sev = collections.Counter()
for name in cases:
    d = os.path.join("output", "trace", name) + "/"
    o = json.load(open(d + "critique_iter1.json"))
    sev[str(o.get("severity"))] += 1
    for i in o.get("parsed_issues") or []:
        allissues.append((name, i))

print("severity 取值分布:", dict(sev))
print(f"issues 总数: {len(allissues)}  平均 {len(allissues)/len(cases):.1f}/例")
print("\n=== issues 首 12 条原文 ===")
for n, i in allissues[:12]:
    print(f"  [{n}] {i[:150]}")

# 是否出现字段名 / 具体数值 (Schema 可用性的关键判据)
FIELDS = ["primary", "active_treatment", "confidence", "variable_weights", "free_text",
          "treatment_recommendation", "repeat_test", "PSA", "Gleason", "ISUP", "PSAD", "cspca"]
FIELD_HITS = collections.Counter()
num_hits = 0
for _, i in allissues:
    low = i.lower()
    for f in FIELDS:
        if f.lower() in low:
            FIELD_HITS[f] += 1
    if re.search(r"\d", i):
        num_hits += 1
print("\n=== issues 中出现的字段/变量提及次数 ===")
for k, v in FIELD_HITS.most_common():
    print(f"  {k:24} {v}")
print(f"  含数字的 issues: {num_hits}/{len(allissues)}")

print("\n=== 两例完整对照（输入 -> 输出 的首段差异）===")


def diff_summary(a, b):
    sa = [s for s in re.split(r"\n+", a) if s.strip()]
    sb = [s for s in re.split(r"\n+", b) if s.strip()]
    only_b = [s for s in sb if s not in sa]
    return len(sa), len(sb), only_b[:3]


for name in cases[:2]:
    d = os.path.join("output", "trace", name) + "/"
    inp = rd(d + "refined_iter1.txt") or rd(d + "plan_full.txt")
    out = rd(d + "refined_full.txt")
    na, nb, new = diff_summary(inp, out)
    print(f"\n--- {name}  输入 {na} 段 / 输出 {nb} 段")
    for s in new:
        print("   新增:", s[:180])
