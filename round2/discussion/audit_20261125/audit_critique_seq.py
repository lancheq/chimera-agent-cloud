"""A-0 前置审计：critique 调用序列与统计矛盾核查。只读，零生成。"""
import json, glob, os, collections

dirs = sorted(d for d in glob.glob("output/trace/*/") if os.path.exists(d + "critique_iter1.json"))
print("含 iter1 的 case 数:", len(dirs))

iters_total = 0
iters_with_issues = 0
has2 = 0
contra = []
raw_but_empty = []
parse_fail = []
per_case = []

for d in dirs:
    fs = sorted(glob.glob(d + "critique_iter*.json"))
    n = len(fs)
    iters_total += n
    if n > 1:
        has2 += 1
    seq = []
    for i, f in enumerate(fs, 1):
        try:
            o = json.load(open(f))
        except Exception:
            seq.append("UNREADABLE")
            continue
        raw = str(o.get("critique_raw_full") or "")
        iss = o.get("parsed_issues")
        if iss:
            iters_with_issues += 1
            seq.append("ISSUE")
        else:
            seq.append("EMPTY")
            if i == 1 and n > 1:
                contra.append(d)
                # 原始文本里是否出现过 issues 字段（不是空数组）
                compact = raw.replace(" ", "").replace("\n", "")
                if '"issues"' in compact and '"issues":[]' not in compact:
                    raw_but_empty.append(d)
                elif '"issues"' not in compact:
                    parse_fail.append(d)
    per_case.append((d, n, tuple(seq)))

print("critique 调用总数:", iters_total)
print("issues 非空的迭代数 = 真实 refine 轮次:", iters_with_issues)
print("含 iter2 的 case:", has2)
print()
print("=== 矛盾样本：iter1 无 issues 却有 iter2 ===", len(contra))
for c in contra[:10]:
    print("   ", c)
print("其中 raw 出现 issues 字段但解析为空:", len(raw_but_empty))
print("其中 raw 根本不含 issues 字段(解析失败回退):", len(parse_fail))
print()
print("=== 序列形态分布 ===")
shape = collections.Counter(x[2] for x in per_case)
for k, v in shape.most_common(12):
    print(f"  {k}: {v}")


def mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0


print()
print("=== 同目录内 iter1/iter2 落盘时间差（跨运行累积的证据）===")
buckets = collections.Counter()
for d in dirs:
    fs = sorted(glob.glob(d + "critique_iter*.json"))
    if len(fs) < 2:
        continue
    span = (mtime(fs[-1]) - mtime(fs[0])) / 86400.0
    if span < 0.01:
        buckets["<15min 同一次运行"] += 1
    elif span < 1:
        buckets["同日内"] += 1
    else:
        buckets[f"{int(span)} 天以上"] += 1
for k, v in sorted(buckets.items()):
    print(f"  {k}: {v}")
