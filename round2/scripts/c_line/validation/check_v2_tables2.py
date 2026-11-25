#!/usr/bin/env python3
"""表格章节归属复核 v2：用 PDF 字体信息找 caption 上方最近的真标题。

上一步用行正则找标题，在表格页会把单元格数字（"78"、"36.25"）当成标题，
20 个"跨章错配"全是这个假阳性。这里改成看 span 的字号与粗体：
rechunk 自己就是用 9.7pt Bold（二级）/ 8.8pt Italic（三级）认标题的，
所以直接复用同一组判据，比对比重建的章节号更接近事实。
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import fitz

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
PDF = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/raw/eau_2025_prostate.pdf")
OUT = Path("/tmp/v2_tables2.json")
CAP = re.compile(r"Table\s+([0-9]+(?:\.[0-9]+)*)", re.I)
NUM_ONLY = re.compile(r"^\s*\d+(?:\.\d+)*\s*$")
NUM_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s+(.+)$")


def core_records():
    recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]
    return [r for r in recs if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]


def heading_spans(page):
    """返回 [(y, size, bold, text)]，只保留像标题的 span。"""
    out = []
    for blk in page.get_text("dict")["blocks"]:
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                txt = sp["text"].strip()
                if not txt or len(txt) < 2:
                    continue
                size = round(sp["size"], 1)
                bold = bool(sp["flags"] & 16) or "Bold" in sp["font"]
                out.append((sp["bbox"][1], size, bold, txt, sp["font"]))
    return out


def heading_in_effect(page, cap_y):
    spans = [s for s in heading_spans(page) if s[0] < cap_y]
    # 先按 rechunk 的判据：9.7 Bold / 8.8 Italic 或 BoldItalic / 17.5 Bold
    cand = [s for s in spans if (abs(s[1] - 9.7) < 0.4 and s[2]) or (abs(s[1] - 8.8) < 0.4)
            or (abs(s[1] - 17.5) < 0.4 and s[2])]
    if not cand:
        return None
    # 取最后一个"像标题"的（编号开头或非句末标点），跳过纯数字
    for y, size, bold, txt, font in sorted(cand, key=lambda s: -s[0]):
        if NUM_ONLY.match(txt):
            continue
        m = NUM_PREFIX.match(txt)
        return {"y": round(y, 1), "size": size, "bold": bold, "font": font,
                "text": txt[:90], "number": m.group(1) if m else None}
    return None


def main():
    doc = fitz.open(PDF)
    core = core_records()
    tables = [r for r in core if r["kind"] == "table"]

    # 各小节的正规标题（来自叙述/推荐记录）
    canon = defaultdict(Counter)
    for r in core:
        if r["kind"] in ("narrative", "recommendation") and (r.get("section_title") or "").strip():
            canon[r["section_id"]][r["section_title"]] += 1
    canon = {k: v.most_common(1)[0][0] for k, v in canon.items()}

    rows = []
    for r in tables:
        p = r.get("page")
        page = doc[p - 1]
        cap_txt = r.get("caption") or ""
        cap_y = None
        for y, size, bold, txt, font in heading_spans(page):
            if CAP.search(txt) and cap_txt[:20].lower() in txt.lower():
                cap_y = y
                break
        if cap_y is None:
            cap_y = 1e9
        eff = heading_in_effect(page, cap_y)
        sid = r.get("section_id")
        eff_num = eff["number"] if eff else None
        if eff_num and sid:
            if eff_num == sid:
                rel = "exact"
            elif sid.startswith(eff_num + ".") or eff_num.startswith(sid + "."):
                rel = "parent_child"
            elif eff_num.split(".")[0] == sid.split(".")[0]:
                rel = "same_chapter"
            else:
                rel = "other_chapter"
        else:
            rel = "unknown"
        rows.append({
            "id": r["id"], "page": p, "caption": cap_txt[:80],
            "caption_num": (CAP.search(cap_txt).group(1) if CAP.search(cap_txt) else None),
            "assigned_section": sid, "assigned_title": r.get("section_title"),
            "canon_title": canon.get(sid),
            "page_heading": eff, "relation": rel,
            "context": r.get("clinical_context"),
            "title_empty": not (r.get("section_title") or "").strip(),
            "title_is_canon": ((r.get("section_title") or "").strip() == (canon.get(sid) or "").strip()),
        })

    rel = Counter(x["relation"] for x in rows)
    # 表号与小节号同前缀（chapter.section 级）
    prefix_ok = 0
    prefix_bad = []
    for x in rows:
        cn, sid = x["caption_num"], x["assigned_section"]
        if not cn or not sid:
            continue
        a, b = cn.split("."), sid.split(".")
        if a[:2] == b[:2] or (len(a) == 1 and a[0] == b[0]):
            prefix_ok += 1
        else:
            prefix_bad.append({"id": x["id"], "caption_num": cn, "section": sid,
                              "assigned_title": x["assigned_title"], "page": x["page"]})

    rep = {
        "tables": len(rows),
        "relation_counts": dict(rel),
        "caption_prefix_matches_section": prefix_ok,
        "caption_prefix_mismatch": prefix_bad,
        "title_empty": [x["id"] for x in rows if x["title_empty"]],
        "title_not_canonical": [{"id": x["id"], "assigned": x["assigned_title"],
                                 "canon": x["canon_title"], "section": x["assigned_section"]}
                                for x in rows if not x["title_is_canon"]][:20],
    }
    sample = [x for x in rows if x["caption_num"] in ("6.2.8", "5.8", "3.1", "6.4.3", "3.3",
                                                      "5.1", "6.3.2", "6.2.10")]
    rep["sample"] = sample
    rep["rows"] = rows

    print(f"表 {rep['tables']} 个；caption 与实际标题关系 {rep['relation_counts']}")
    print(f"表号前两级与小节号一致：{prefix_ok}；不一致 {len(prefix_bad)}")
    for x in prefix_bad[:10]:
        print("   ", x)
    print(f"section_title 为空：{len(rep['title_empty'])}")
    print(f"section_title 与小节正规标题不一致：{len(rep['title_not_canonical'])}")
    for x in rep["title_not_canonical"][:8]:
        print("   ", x)
    print("\n抽样：")
    for x in sample:
        eff = x["page_heading"] or {}
        print(f"   {x['caption_num']:8s} p{x['page']:<4} 分配 {x['assigned_section']}({x['assigned_title'] or '∅'}) "
              f"| 页内标题 {eff.get('number')}({ (eff.get('text') or '')[:45] }) | {x['relation']}")
        print(f"            caption: {x['caption'][:70]}")
        print(f"            context: {x['context']}  canon: {x['canon_title']}")

    OUT.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n")
    print("\n写入", OUT)


if __name__ == "__main__":
    main()
