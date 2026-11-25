"""旧知识库（resources/guidelines_db）质量审计 — 只读 sqlite，不启动 chroma。

输出可直接与结构感知重切分结果对照的硬指标。
"""

import json
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

DB = sys.argv[1] if len(sys.argv) > 1 else "chroma.sqlite3"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()
docs = defaultdict(dict)
for cid, key, sv, iv in cur.execute(
    "SELECT m.id, m.key, m.string_value, m.int_value FROM embedding_metadata m "
    "WHERE m.key IN ('chroma:document','section','source','page')"
):
    docs[cid][key] = sv if sv is not None else iv

rows = [{**v, "id": k} for k, v in docs.items() if "chroma:document" in v]
rows = [r for r in rows if len(r["chroma:document"]) > 1]

JOURNAL = re.compile(r"(Lancet|Eur Urol|J Clin Oncol|N Engl J Med|J Urol|BJU Int|Radiology|Cancers|Oncol|Urol)", re.I)
SENT_MID = re.compile(r"^[a-z,;)]")


def is_junk_section(sec):
    """'3 trial. Lancet Oncol, 2016. 17: 727.' 这类来自参考文献的伪标题。"""
    if not sec:
        return True
    if len(sec) < 24:
        return True
    return bool(re.match(r"^\d+\s", sec) and JOURNAL.search(sec))


toc, junk_sec, mid_sent, has_rating, has_rec_hdr = 0, 0, 0, 0, 0
lens = []
for r in rows:
    text = r["chroma:document"]
    page = r.get("page") or 0
    lens.append(len(text))
    if isinstance(page, int) and page <= 9:
        toc += 1
    if r.get("source") == "EAU" and is_junk_section(r.get("section", "")):
        junk_sec += 1
    if SENT_MID.match(text.strip()):
        mid_sent += 1
    if re.search(r"\b(Strong|Weak)\b", text):
        has_rating += 1
    if "Recommendations" in text:
        has_rec_hdr += 1

n = len(rows)
report = {
    "chunks_total": n,
    "by_source": dict(Counter(r.get("source") for r in rows)),
    "chunk_len": {"min": min(lens), "median": statistics.median(lens), "max": max(lens)},
    "toc_page_chunks": toc,
    "toc_share": round(toc / n, 3),
    "junk_section_chunks": junk_sec,
    "junk_section_share": round(junk_sec / n, 3),
    "distinct_sections": len({r.get("section") for r in rows}),
    "chunk_starts_mid_sentence": mid_sent,
    "mid_sentence_share": round(mid_sent / n, 3),
    "chunks_containing_strong_or_weak": has_rating,
    "chunks_with_rating_but_no_header": has_rating - has_rec_hdr,
}
print(json.dumps(report, indent=2, ensure_ascii=False))
