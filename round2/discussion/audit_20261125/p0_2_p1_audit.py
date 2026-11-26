"""P0-2 历史轨迹可信度四分法 + P1 Baseline 资产关联审计。只读。"""
import glob
import json
import os
import collections
import datetime

WIN = 2 * 3600  # 同一运行窗口 (秒)


def mt(p):
    return os.path.getmtime(p)


def d(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


# ---------- P0-2 四分法 ----------
print("=== P0-2 历史轨迹四分法 ===")
dirs = sorted(d for d in glob.glob("output/trace/*/") if os.path.exists(d + "critique_iter1.json"))
cls = collections.Counter()
rows = []
for d_ in dirs:
    crit = sorted(glob.glob(d_ + "critique_iter*.json"))
    times = [mt(f) for f in crit]
    inverted = any(times[i + 1] < times[i] for i in range(len(times) - 1))
    span = max(times) - min(times)
    files = [f for f in glob.glob(d_ + "*") if os.path.isfile(f)]
    dir_dates = {datetime.datetime.fromtimestamp(mt(f)).strftime("%Y-%m-%d") for f in files}
    has_out = os.path.exists(d_ + "refined_full.txt")
    # 末轮 refine 输出是否可得
    n = len(crit)
    out_avail = has_out or (n >= 2 and os.path.exists(d_ + f"refined_iter{n}.txt"))

    if inverted:
        c = "确认污染"
    elif span <= WIN and len(dir_dates) == 1:
        c = "可信"
    elif span <= WIN and len(dir_dates) > 1:
        c = "疑似污染"
    else:
        c = "无法判定"
    if not out_avail:
        c = "无法判定" if c == "可信" else c
    cls[c] += 1
    rows.append({"case": os.path.basename(d_.rstrip("/")), "class": c, "crit_files": n,
                 "inverted": inverted, "span_h": round(span / 3600, 1),
                 "dir_dates": sorted(dir_dates), "refine_output_available": out_avail})

print(f"  总数 {len(dirs)}")
for k in ("可信", "疑似污染", "确认污染", "无法判定"):
    print(f"  {k}: {cls.get(k,0)}")

print("\n  四类各自的 refine 末轮输出可得率:")
for k in ("可信", "疑似污染", "确认污染", "无法判定"):
    sub = [r for r in rows if r["class"] == k]
    if not sub:
        continue
    ok = sum(1 for r in sub if r["refine_output_available"])
    print(f"    {k}: {ok}/{len(sub)}")

with open("discussion/audit_20261125/P0_2_trace_trust.json", "w") as f:
    json.dump({"window_hours": 2, "counts": dict(cls), "cases": rows}, f, ensure_ascii=False, indent=2)

# ---------- P1 Baseline 资产关联 ----------
print("\n=== P1 Baseline 资产与 J0 关联 ===")
j0 = glob.glob("output/**/judgment_pre_calibration.json", recursive=True)
by_top = collections.Counter(p.split("/")[1] for p in j0)
print(f"  J0 快照总数: {len(j0)}，分布在 {len(by_top)} 个顶层目录")
print("  数量前 10:")
for k, v in by_top.most_common(10):
    print(f"    {v:>5}  {k}")

print("\n  各 top 目录的 case 形态（前缀计数）:")
shape = collections.defaultdict(collections.Counter)
for p in j0:
    parts = p.split("/")
    top = parts[1]
    case = ""
    for seg in parts[2:]:
        if seg in ("trace", "private", "final_cases", "live"):
            continue
        if seg and not seg.startswith("output") and seg != "gc-case":
            case = seg
            break
    pref = ("T3" if case.startswith("T3") else "T1" if case.startswith("T1")
            else "T2" if case.startswith("T2") else "PT-pseudo" if case.startswith("PT-pseudo")
            else "other")
    shape[top][pref] += 1
for top, cnt in sorted(shape.items(), key=lambda kv: -sum(kv[1].values()))[:8]:
    print(f"    {top:52} {dict(cnt)}")
