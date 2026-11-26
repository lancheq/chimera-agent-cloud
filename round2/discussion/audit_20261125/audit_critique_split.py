"""A-0 前置审计 2：按样本来源与调用序列分层，定位矛盾归属。"""
import json, glob, os, collections

dirs = sorted(d for d in glob.glob("output/trace/*/") if os.path.exists(d + "critique_iter1.json"))


def bucket(name):
    if name.startswith("PT-pseudo"):
        return "PT-pseudo(伪病例)"
    if name.startswith("T1"):
        return "T1"
    if name.startswith("T2"):
        return "T2"
    if name.startswith("T3"):
        return "T3"
    if name.startswith("BX"):
        return "BX"
    return "其他"


stat = collections.defaultdict(lambda: {"cases": 0, "iters": 0, "issue_iters": 0, "contra": 0, "shapes": collections.Counter()})
for d in dirs:
    name = os.path.basename(d.rstrip("/"))
    b = bucket(name)
    fs = sorted(glob.glob(d + "critique_iter*.json"))
    seq = []
    for f in fs:
        try:
            o = json.load(open(f))
        except Exception:
            seq.append("X")
            continue
        seq.append("ISSUE" if o.get("parsed_issues") else "EMPTY")
    s = stat[b]
    s["cases"] += 1
    s["iters"] += len(fs)
    s["issue_iters"] += sum(1 for x in seq if x == "ISSUE")
    if len(seq) > 1 and seq[0] == "EMPTY":
        s["contra"] += 1
    s["shapes"][tuple(seq)] += 1

print(f"{'分组':<16}{'case':>6}{'critique':>10}{'refine轮':>9}{'矛盾':>6}   Top 序列")
for b, s in sorted(stat.items(), key=lambda kv: -kv[1]["cases"]):
    top = ", ".join(f"{'/'.join(k)}={v}" for k, v in s["shapes"].most_common(4))
    print(f"{b:<16}{s['cases']:>6}{s['iters']:>10}{s['issue_iters']:>9}{s['contra']:>6}   {top}")

print()
print("=== 矛盾样本是否全在伪病例 ===")
only_pt = all(os.path.basename(d.rstrip('/')).startswith("PT-pseudo")
              for d in dirs
              if len(glob.glob(d + "critique_iter*.json")) > 1
              and (json.load(open(d + "critique_iter1.json")).get("parsed_issues") or []) == [])
print("矛盾样本全部为 PT-pseudo:", only_pt)

print()
print("=== PT-pseudo 是否有独立的生成脚本 ===")
for p in ("scripts/g5_reflective_pseudo.py",):
    print(p, "存在" if os.path.exists(p) else "缺失", os.path.getsize(p) if os.path.exists(p) else "")
