"""A-0 前置审计 3：矛盾样本的时间分布与版本归属。"""
import json, glob, os, datetime, collections

dirs = sorted(d for d in glob.glob("output/trace/*/") if os.path.exists(d + "critique_iter1.json"))
CONTR = []
OK = []
for d in dirs:
    fs = sorted(glob.glob(d + "critique_iter*.json"))
    if len(fs) < 2:
        continue
    o = json.load(open(fs[0]))
    (CONTR if not (o.get("parsed_issues") or []) else OK).append(d)

CUT = datetime.datetime(2026, 10, 28, 17, 35).timestamp()  # 33a961a 提交时刻


def dstr(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d")


def report(tag, arr):
    print(f"\n=== {tag} ({len(arr)} 例) ===")
    before = sum(1 for d in arr if os.path.getmtime(d + "critique_iter1.json") < CUT)
    print(f"  iter1 落盘早于 33a961a(10-28 17:35): {before}  晚于: {len(arr)-before}")
    m = collections.Counter(dstr(os.path.getmtime(d + "critique_iter1.json")) for d in arr)
    print("  按月:", dict(sorted(collections.Counter(k[:7] for k in m).items())))
    print("  最早/最晚:", min(m), max(m))


report("矛盾样本 iter1 无 issues 却有 iter2", CONTR)
report("自洽样本 iter1 有 issues", OK)

print("\n=== 矛盾样本抽样（含 iteration 字段与 raw 长度）===")
for d in CONTR[:4]:
    print(" ", d)
    for f in sorted(glob.glob(d + "critique_iter*.json")):
        o = json.load(open(f))
        raw = str(o.get("critique_raw_full") or "")
        print(f"    {os.path.basename(f):22} iteration={o.get('iteration')} "
              f"issues={len(o.get('parsed_issues') or [])} rawlen={len(raw)} "
              f"mtime={dstr(os.path.getmtime(f))}")
