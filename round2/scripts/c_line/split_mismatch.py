"""两件事：
1. 表格 table_id 与 section_id 分离（caption 编号不再冒充真实章节）
2. 把上下文里的错配分为「原始 top5 就有」与「P1 回填新增」
"""

import json
import re
from collections import Counter
from pathlib import Path

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
CAP_NUM = re.compile(r"^(?:Table|Figure)\s*(\d+(?:\.\d+)*)")

recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]

# --- 1. table_id / section_id 分离 ---
n = 0
for r in recs:
    if r["kind"] != "table":
        continue
    m = CAP_NUM.match(r.get("caption") or "")
    r["table_id"] = f"Table {m.group(1)}" if m and r["caption"].lower().startswith("table") else (
        f"Figure {m.group(1)}" if m else "")
    r["caption_num"] = m.group(1) if m else ""
    # 真实章节无法确定 → section_id 置空，保留 section_title
    r["section_id"] = ""
    r["section_unknown"] = True
    r["parent_id"] = ""
    r["clinical_context"] = ["unknown"]
    r["context_confidence"] = "unknown"
    n += 1

V2.joinpath("records_v2.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
print("tables downgraded to unknown section:", n)

# --- 2. 错配分解 ---
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]
pos = {r["id"]: i for i, r in enumerate(core)}
rv = json.load(open("/tmp/retrieve_v2.json"))

EXPECT = {
    "Q01": ["mri_biopsy_decision"], "Q02": ["mri_biopsy_decision"],
    "Q03": ["repeat_biopsy_prior_result", "repeat_biopsy_prior_negative"],
    "Q04": ["repeat_biopsy_prior_result", "repeat_biopsy_prior_negative"],
    "Q05": ["active_surveillance_confirmed"], "Q06": ["active_surveillance_confirmed"],
}


def judge(rid, exp):
    r = core[pos[rid]]
    if r["doc_type"] == "nonclinical" or r["clinical_context"] == ["unknown"]:
        return "noise_or_unknown"
    return "ok" if set(r["clinical_context"]) & set(exp) else "mismatch"


rows = []
tot = Counter()
for qid, d in rv.items():
    exp = EXPECT[qid]
    top5 = d["top5"]
    ctx = d["ctx_ids"]
    p1 = [i for i in ctx if i not in top5]
    a = Counter(judge(i, exp) for i in top5)
    b = Counter(judge(i, exp) for i in p1)
    tot.update(a)
    tot.update(b)
    rows.append((qid, a, b))
    print(f"{qid}: top5 ok={a['ok']} mismatch={a['mismatch']} noise={a['noise_or_unknown']} | "
          f"P1(+{len(p1)}) ok={b['ok']} mismatch={b['mismatch']} noise={b['noise_or_unknown']}")

print("\n合计:", dict(tot))
print("top5 内错配:", sum(a["mismatch"] for _, a, _ in rows))
print("P1 新增错配:", sum(b["mismatch"] for _, _, b in rows))
