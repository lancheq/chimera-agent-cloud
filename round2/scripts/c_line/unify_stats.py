"""统一统计口径：把 top5 与 P1 新增分开，四类互斥。

分类（互斥，按顺序判定）：
  nonclinical    doc_type == nonclinical          真正的非临床内容（方法学/未来计划）
  unknown_scene  clinical_context == ["unknown"]   含全部 97 个表格（真实章节未知）
                且 doc_type != nonclinical
  mismatch       有明确场景但与本病例所需场景无交集
  applicable     场景有交集

之前三处差异的来源：
  1. 14 vs 15：14 是 5 条 query 的合计（漏 Q05），15 是 6 条 query 的合计
  2. 噪声 0 vs 6：0 是表格降级前跑的（表格当时有 clinical_context），
     6 是降级后跑的（表格 context 变 unknown，被判成噪声）
  3. Q05 未列入：拆分脚本用了 EXPECT 里没有的 key，被静默跳过
"""

import json
import re
from collections import Counter
from pathlib import Path

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]
pos = {r["id"]: i for i, r in enumerate(core)}
rv = json.load(open("/tmp/retrieve_v2.json"))

EXPECT = {
    "Q01": ["mri_biopsy_decision"],
    "Q02": ["mri_biopsy_decision"],
    "Q03": ["repeat_biopsy_prior_result", "repeat_biopsy_prior_negative"],
    "Q04": ["repeat_biopsy_prior_result", "repeat_biopsy_prior_negative"],
    "Q05": ["active_surveillance_confirmed"],
    "Q06": ["active_surveillance_confirmed"],
}


def judge(rid, exp):
    r = core[pos[rid]]
    if r["doc_type"] == "nonclinical":
        return "nonclinical"
    if r["clinical_context"] == ["unknown"]:
        return "unknown_scene"
    return "applicable" if set(r["clinical_context"]) & set(exp) else "mismatch"


CATS = ["applicable", "mismatch", "unknown_scene", "nonclinical"]
grand_top, grand_p1 = Counter(), Counter()

print("%-5s %-14s %-6s %-6s %-6s %-6s %-12s %-6s" %
      ("q", "scope", "n", "适用", "错配", "场景未知", "非临床", "token"))
rows = []
for qid in sorted(rv):
    if qid not in EXPECT:
        print("SKIP (no EXPECT):", qid)
        continue
    exp = EXPECT[qid]
    d = rv[qid]
    top5 = d["top5"]
    ctx = d["ctx_ids"]
    p1 = [i for i in ctx if i not in top5]
    a = Counter(judge(i, exp) for i in top5)
    b = Counter(judge(i, exp) for i in p1)
    grand_top.update(a)
    grand_p1.update(b)
    for name, c, n in (("top5", a, len(top5)), ("P1+", b, len(p1))):
        print("%-5s %-14s %-6d %-6d %-6d %-6d %-6d %-12s" %
              (qid, name, n, c["applicable"], c["mismatch"], c["unknown_scene"], c["nonclinical"], ""))
    rows.append((qid, a, b, d["tokens"]))

print("\n=== 合计")
for name, c in (("原始 top5", grand_top), ("P1 新增", grand_p1)):
    tot = sum(c.values())
    print("%-10s n=%-4d 适用=%-3d 错配=%-3d 场景未知=%-3d 非临床=%-3d" %
          (name, tot, c["applicable"], c["mismatch"], c["unknown_scene"], c["nonclinical"]))
allc = grand_top + grand_p1
print("%-10s n=%-4d 适用=%-3d 错配=%-3d 场景未知=%-3d 非临床=%-3d" %
      ("全部", sum(allc.values()), allc["applicable"], allc["mismatch"],
       allc["unknown_scene"], allc["nonclinical"]))

print("\n=== 口径对照")
print("5 条 query 的 top5 错配（剔除 Q05）:", sum(a["mismatch"] for q, a, b, t in rows if q != "Q05"))
print("6 条 query 的 top5 错配（含 Q05）  :", sum(a["mismatch"] for q, a, b, t in rows))
