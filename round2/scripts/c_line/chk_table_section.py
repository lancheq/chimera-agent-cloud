"""检查表格 caption 编号与所属章节是否一致。

对抽样表格：caption 编号（Table 6.2.8 → 6.2.8）、库内 section_id、PDF 该页最近的上层标题，
三者对照。同时打印该页正文首句，便于人工判断表的真实归属。
"""

import json
import re

import fitz

PDF = "/home/hedongze/CHIMERA-agent/guideline_dataset/raw/eau_2025_prostate.pdf"
V2 = "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2"

recs = [json.loads(l) for l in open(V2 + "/records_v2.jsonl")]
tabs = [r for r in recs if r["kind"] == "table"]
doc = fitz.open(PDF)

CAP_NUM = re.compile(r"^(?:Table|Figure)\s*(\d+(?:\.\d+)*)")


def headings_on_page(pno):
    """返回该页所有 9.7pt Bold（H2）标题。"""
    out = []
    for b in doc[pno - 1].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            spans = l["spans"]
            if not spans:
                continue
            t = "".join(s["text"] for s in spans).strip()
            if not t:
                continue
            if 9.4 <= max(s["size"] for s in spans) < 12 and "Bold" in spans[0]["font"]:
                out.append(t)
    return out


def prev_heading(pno, max_back=2):
    for p in range(pno, max(pno - max_back, 0), -1):
        h = headings_on_page(p)
        if h:
            return p, h[:3]
    return None, []


# 抽样：跨不同章节、含 caption 编号与所在章节可能不一致的情况
samples = []
seen_sec = set()
for t in tabs:
    sid = t.get("section_id", "")
    if sid in seen_sec:
        continue
    seen_sec.add(sid)
    samples.append(t)
samples = samples[:10]

print("caption | cap_num | lib_section | 该页/前页 H2 | 一致?")
for t in samples:
    cap = t.get("caption", "")
    m = CAP_NUM.match(cap)
    num = m.group(1) if m else ""
    pno, heads = prev_heading(t["page"])
    lib = t.get("section_id", "")
    ok = "一致" if (num and lib and (num == lib or lib.startswith(num + ".") or num.startswith(lib + "."))) else "不一致"
    print("p%-4d %-38s %-9s %-9s p%s:%s -> %s"
          % (t["page"], cap[:38], num, lib, pno, heads[:2], ok))
    print("      ctx=%s rows=%dx%d" % (t["clinical_context"], t["n_rows"], t["n_cols"]))
