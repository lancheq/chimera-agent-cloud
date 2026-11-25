"""把 ABCD 结果整理成对照表。/tmp/abcd_result.json -> 可读 MD。"""

import json

d = json.load(open("/tmp/abcd_result.json"))
tm = d["target_map"]
R = d["results"]

GNAME = {
    "A_old_mixed": "A 原混合库(2889)",
    "B_new2025_plus_2026": "B 新2025+原2026(2510)",
    "C_old_2025": "C 纯2025旧(1375)",
    "D_new_2025": "D 纯2025新(996)",
}
QIDS = ["Q01", "Q02", "Q03", "Q04", "Q05", "Q06"]

L = ["# A/B/C/D 语料对照结果\n"]
L.append("判据：目标证据是否进入最终正文前 5 / 8 / 12 条。所有组共用同一 dense/BM25/RRF/重排。\n")

L.append("\n## 一、目标证据命中数（按组）\n")
L.append("| 组 | top5 | top8 | top12 | 判定目标总数 |")
L.append("|---|---:|---:|---:|---:|")
for g in GNAME:
    c5 = c8 = c12 = tot = 0
    for q in QIDS:
        for tid, t in R[g][q]["targets"].items():
            tot += 1
            c5 += t["in_top5"]
            c8 += t["in_top8"]
            c12 += t["in_top12"]
    L.append(f"| {GNAME[g]} | {c5} | {c8} | {c12} | {tot} |")

L.append("\n## 二、Q01 三条目标在各组的排名\n")
L.append("| 目标 | 性质 | " + " | ".join(GNAME[g] for g in GNAME) + " |")
L.append("|---|---|" + "---|" * len(GNAME))
qid = "Q01"
for tid in tm:
    row = []
    for g in GNAME:
        t = R[g][qid]["targets"].get(tid)
        if not t:
            row.append("—")
        else:
            rk = t["rank_after_rerank"]
            mark = "✓" if t["in_top5"] else ("~" if t["in_top8"] else ("…" if t["in_top12"] else ""))
            row.append(f"{rk if rk else '未进'}{mark}")
    L.append(f"| `{tid}` | {tm[tid]['review'][:26]} | " + " | ".join(row) + " |")

L.append("\n## 三、逐 query 明细（目标排名 / 是否进前 5）\n")
L.append("| query | 组 | " + " | ".join(f"`{t}`" for t in tm) + " |")
L.append("|---|---|" + "---|" * len(tm))
for q in QIDS:
    for g in GNAME:
        cells = []
        for tid in tm:
            t = R[g][q]["targets"].get(tid)
            if not t:
                cells.append("—")
            else:
                rk = t["rank_after_rerank"]
                cells.append(str(rk) if rk else "未进")
        L.append(f"| {q} | {GNAME[g].split()[0]} | " + " | ".join(cells) + " |")

L.append("\n## 四、目标映射（旧 id → 新库单元）\n")
L.append("| 旧 id | 版本 | 新库 id | 余弦 | 人工标注 |")
L.append("|---|---|---|---:|---|")
for tid, m in tm.items():
    L.append(f"| `{tid}` | {m['old_version']} | `{m['new_id']}` | {m['cos']} | {m['review'][:30]} |")

open("/tmp/abcd_tables.md", "w").write("\n".join(L))
print("\n".join(L[:40]))
