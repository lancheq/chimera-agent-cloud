"""H04 取样前置：可信 58 例中实际发生 Refine 的数量与形态。"""
import json
import os
import collections

trust = json.load(open("discussion/audit_20261125/P0_2_trace_trust.json"))
cases = trust["cases"]
trusted = [c for c in cases if c["class"] == "可信"]

print(f"可信样本 {len(trusted)} 例")


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


shape = collections.Counter()
refined_cases = []
for c in trusted:
    d = os.path.join("output", "trace", c["case"]) + "/"
    o = load(d + "critique_iter1.json")
    iss = o.get("parsed_issues") or []
    if iss:
        shape["首轮有问题(发生Refine)"] += 1
        refined_cases.append((c["case"], len(iss), o.get("severity")))
    else:
        shape["首轮无问题(未Refine)"] += 1

print("形态:", dict(shape))
print()
print("=== 发生 Refine 的可信样本 ===")
for name, n, sev in refined_cases[:20]:
    print(f"  {name:26} issues={n} severity={sev}")
if not refined_cases:
    print("  （无）")

print()
print("=== 全库中含 problems 且可定位 refined 输出的样本（不限于可信）===")
allwithissue = 0
for c in cases:
    d = os.path.join("output", "trace", c["case"]) + "/"
    o = load(d + "critique_iter1.json")
    if o.get("parsed_issues"):
        allwithissue += 1
print("  含首轮 issues 的总数:", allwithissue)

print()
print("=== 可信样本的 refined_full.txt 是否真的被改写过（与输入长度对比）===")
same_len = 0
diff_len = 0
for c in trusted:
    d = os.path.join("output", "trace", c["case"]) + "/"
    if not os.path.exists(d + "refined_full.txt"):
        continue
    full = open(d + "refined_full.txt", encoding="utf-8", errors="ignore").read()
    inp = None
    for cand in ("refined_iter1.txt", "plan_full.txt"):
        if os.path.exists(d + cand):
            inp = open(d + cand, encoding="utf-8", errors="ignore").read()
            break
    if inp is None:
        continue
    if abs(len(full) - len(inp)) < 30:
        same_len += 1
    else:
        diff_len += 1
print(f"  与输入长度接近(<30字符): {same_len}   明显不同: {diff_len}")

# 输出候选样本清单供 H04 人工/规则抽取
out = [c["case"] for c in trusted if load(os.path.join("output", "trace", c["case"]) + "/critique_iter1.json").get("parsed_issues")]
json.dump({"trusted": len(trusted), "shape": dict(shape), "refined_cases": out},
          open("discussion/audit_20261125/H04_sample_gate.json", "w"), ensure_ascii=False, indent=2)
