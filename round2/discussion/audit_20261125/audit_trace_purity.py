"""A-0 前置审计 4：目录是否单次运行快照（残留文件污染范围）。"""
import glob, os, datetime, collections

dirs = sorted(d for d in glob.glob("output/trace/*/") if os.path.exists(d + "critique_iter1.json"))
mixed = []
single = []
alldates = collections.Counter()
for d in dirs:
    files = [f for f in glob.glob(d + "*") if os.path.isfile(f)]
    dates = collections.Counter(
        datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d") for f in files)
    alldates[len(dates)] += 1
    if len(dates) > 1:
        mixed.append((d, dict(sorted(dates.items()))))
    else:
        single.append(d)

print("case 目录数:", len(dirs))
print("单一日期的目录:", len(single))
print("跨多日期(疑似残留污染):", len(mixed))
print("每个目录文件修改日期的种类数分布:", dict(sorted(alldates.items())))

print("\n=== 污染最重的 6 例 ===")
for d, dates in sorted(mixed, key=lambda x: -len(x[1]))[:6]:
    print(" ", os.path.basename(d.rstrip("/")), dates)

print("\n=== 全体 case 的 iter1 落盘月份 ===")
mon = collections.Counter(
    datetime.datetime.fromtimestamp(os.path.getmtime(d + "critique_iter1.json")).strftime("%Y-%m")
    for d in dirs)
print(dict(sorted(mon.items())))

print("\n=== 最新一例 iter1 的日期（判断整个集合是否为旧版本）===")
latest = max(dirs, key=lambda d: os.path.getmtime(d + "critique_iter1.json"))
print(latest, datetime.datetime.fromtimestamp(os.path.getmtime(latest + "critique_iter1.json")))
