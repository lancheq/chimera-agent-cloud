"""只补元数据，不动文本与顺序（避免与正在进行的编码冲突）。

表格页若无正文，section_id 为空 → 从 caption 的编号（Table 6.2.8）回填 section 与场景。
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import sys

sys.path.insert(0, "/tmp")
from rechunk_v2 import clinical_context, CAP_RE  # 复用同一套映射

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]

# 页 → section（先用有 section 的记录建表）
page_sec = defaultdict(Counter)
for r in recs:
    if r.get("section_id") and r.get("page"):
        page_sec[r["page"]][r["section_id"]] += 1

CAP_NUM = re.compile(r"^(?:Table|Figure)\s*(\d+(?:\.\d+)*)")

patched = 0
for r in recs:
    if r["kind"] == "table" and not (r.get("section_id") or "").strip():
        m = CAP_NUM.match(r.get("caption") or "")
        if m:
            r["section_id"] = m.group(1)
            patched += 1
    if r["kind"] == "table" and not (r.get("section_id") or "").strip():
        # caption 无编号 → 用同页正文的 section
        cand = page_sec.get(r.get("page"))
        if cand:
            r["section_id"] = cand.most_common(1)[0][0]
            patched += 1
    sid = (r.get("section_id") or "").strip()
    r["clinical_context"] = clinical_context(sid)
    r["parent_id"] = f"parent-{sid}" if sid else ""

V2.joinpath("records_v2.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")

still_unknown = sum(1 for r in recs if r["kind"] == "table" and r["clinical_context"] == ["unknown"])
print(json.dumps({
    "patched_tables": patched,
    "tables_total": sum(1 for r in recs if r["kind"] == "table"),
    "tables_still_unknown": still_unknown,
    "by_context": dict(Counter(c for r in recs for c in r["clinical_context"])),
}, ensure_ascii=False, indent=1))
