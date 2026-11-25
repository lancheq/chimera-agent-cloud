"""EAU 前列腺癌指南 — 结构感知重切分（C 线方向一）

目的：用 PDF 排版结构（字号/字体/栏位）而不是字符滑窗来切分，保证
      ① 每条推荐与其所属小节的叙述不分离
      ② 推荐条目永不跨 chunk（左栏正文 + 右栏 Strength rating 绑定）
      ③ 目录页、参考文献、页眉页码不入库
      ④ section_id / section_title 逐字来自 PDF，不再靠正则猜

只读原始 PDF，输出到独立目录，不修改 resources/ 下任何文件。

用法：
    python rechunk_eau.py --pdf <pdf> --out <dir>
"""

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import fitz

SOURCE = "EAU"
VERSION = "2025-LimitedUpdate-March"

# 版面阈值（由 251 页抽样标定）
Y_HEADER_MIN = 800.0        # 页眉/页脚在此之下
SIZE_H1 = 16.0              # 一级标题（"5." + "DIAGNOSTIC EVALUATION"）
SIZE_H2 = 9.4               # 二级标题（"4.5 Recommendations for ..."）
X_RIGHT_COL = 400.0         # 推荐表右栏：单栏版 x=474，双栏版 x=417
MAX_CHUNK = 900             # 叙述段打包上限（字符）

SECTION_NO = re.compile(r"^\d+(?:\.\d+)+\.?$|^\d+\.$")
REC_HEADER = re.compile(r"^(recommendations?|strength rating)$", re.IGNORECASE)
NUM_TITLE = re.compile(r"^(\d+(?:\.\d+)*\.?)\s+(.+)$")
RATING = re.compile(r"^(strong|weak|conditional|follow[- ]?up)$", re.IGNORECASE)
CAPTION = re.compile(r"^(Table|Figure)\s+\d")


def page_lines(page):
    """把一页解析成行：合并同一 y 的 span，丢弃页眉页码，左右栏分开。"""
    rows = {}
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = line["spans"]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            if not text:
                continue
            y = round(line["bbox"][1], 1)
            if y >= Y_HEADER_MIN:
                continue
            rows.setdefault(y, []).append(
                {
                    "x": round(line["bbox"][0], 1),
                    "text": text,
                    "size": max(s["size"] for s in spans),
                    "font": spans[0]["font"],
                }
            )
    out = []
    for y in sorted(rows):
        parts = sorted(rows[y], key=lambda p: p["x"])
        out.append(
            {
                "y": y,
                "left": [p for p in parts if p["x"] < X_RIGHT_COL],
                "right": [p for p in parts if p["x"] >= X_RIGHT_COL],
            }
        )
    return out


def is_toc_page(text, page_no):
    """目录页：只在前置页出现，行数极多、几乎没有以句号结尾的长句、页码独立成行。
    加页码上限是为了避免把图形页（如 Clinical Frailty Scale 图）误判成目录。"""
    if page_no > 12:
        return False
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 100:
        return False
    sentences = sum(1 for l in lines if l.endswith(".") and len(l) > 60)
    bare_num = sum(1 for l in lines if l.isdigit())
    return sentences <= 3 and bare_num >= 15


def classify(row):
    """返回 (kind, left_parts, left_text)。字体名区分节号/节标题/正文。"""
    parts = row["left"]
    if not parts:
        return "right_only", parts, ""
    text = " ".join(p["text"] for p in parts).strip()
    head = parts[0]
    font = head["font"]
    if head["size"] >= SIZE_H1:
        return "h1", parts, text
    if head["size"] >= SIZE_H2:
        return "h2", parts, text
    # 节号行 "5.7.1" / 合并行 "5.7.1 Repeat biopsy after negative biopsy"
    if SECTION_NO.match(parts[0]["text"].strip()) and ("Italic" in parts[0]["font"]):
        return "h3", parts, text
    # 只有标题没有编号的节标题行 "Repeat biopsy after negative biopsy"
    if "BoldItalic" in font and head["size"] >= 8.5 and not NUM_TITLE.match(text):
        return "h3_title", parts, text
    if REC_HEADER.match(text):
        return "rec_header", parts, text
    return "body", parts, text


def build_units(pdf_path):
    doc = fitz.open(pdf_path)
    units = []
    skipped = {"toc_pages": [], "reference_pages": [], "empty": []}
    cur = {"h1": "", "h2_id": "", "h2_title": "", "h3_id": "", "h3_title": ""}
    buf = []
    rec_pending = []
    rec_strength = ""
    rec_mode = False
    table_open = False       # 跨页保持：续表页没有重复表头
    table_start = None
    rec_tables = []          # [(section_id, section_title, page, n_recs)]
    rec_stats = defaultdict(int)
    in_refs = False
    last_y = None
    cap_open = False         # 图表区域
    cap_buf = []
    cap_title = ""

    def sec_id():
        return cur["h3_id"] or cur["h2_id"] or cur["h1"]

    def sec_title():
        return cur["h3_title"] or cur["h2_title"] or cur["h1"]

    def flush_narrative(page):
        nonlocal buf
        if not buf:
            return
        text = " ".join(buf).strip()
        buf = []
        if len(text) < 40:
            return
        units.append({"kind": "narrative", "source": SOURCE, "version": VERSION,
                      "section_id": sec_id(), "section_title": sec_title(),
                      "h1": cur["h1"], "page": page, "text": text})

    def flush_caption(page):
        nonlocal cap_buf, cap_title
        if not cap_buf:
            return
        text = " ".join(cap_buf).strip()
        cap_buf = []
        if len(text) < 12:
            return
        units.append({"kind": "table", "source": SOURCE, "version": VERSION,
                      "section_id": sec_id(), "section_title": sec_title(),
                      "h1": cur["h1"], "page": page, "caption": cap_title, "text": text})

    def flush_rec(page, strength):
        nonlocal rec_pending
        if not rec_pending:
            return
        text = " ".join(rec_pending).strip()
        rec_pending = []
        if len(text) < 10:
            return
        units.append({"kind": "recommendation", "source": SOURCE, "version": VERSION,
                      "section_id": sec_id(), "section_title": sec_title(),
                      "h1": cur["h1"], "page": page, "text": text,
                      "strength": strength})
        rec_stats[strength or "unknown"] += 1

    for pno in range(doc.page_count):
        page = doc[pno]
        raw = page.get_text("text")
        if not raw.strip():
            skipped["empty"].append(pno + 1)
            continue
        if is_toc_page(raw, pno + 1):
            skipped["toc_pages"].append(pno + 1)
            continue
        if not in_refs and pno > doc.page_count * 0.55 and re.search(r"^\s*REFERENCES\s*$", raw, re.MULTILINE):
            flush_rec(pno + 1, rec_strength)
            flush_narrative(pno + 1)
            in_refs = True
        if in_refs:
            skipped["reference_pages"].append(pno + 1)
            continue

        last_y = None
        for row in page_lines(page):
            kind, parts, text = classify(row)
            right = " ".join(p["text"] for p in row["right"]).strip()

            if CAPTION.match(text):
                flush_rec(pno + 1, rec_strength)
                flush_narrative(pno + 1)
                flush_caption(pno + 1)
                cap_open = True
                cap_title = text[:160]
                if right:
                    cap_buf.append(right)
                continue
            if kind == "h1":
                flush_rec(pno + 1, rec_strength)
                flush_narrative(pno + 1)
                flush_caption(pno + 1)
                cap_open = False
                if table_start is not None:
                    rec_tables.append(table_start)
                    table_start = None
                rec_mode = table_open = False
                cur.update({"h1": text, "h2_id": "", "h2_title": "", "h3_id": "", "h3_title": ""})
                continue

            if kind == "h2":
                flush_rec(pno + 1, rec_strength)
                flush_narrative(pno + 1)
                flush_caption(pno + 1)
                cap_open = False
                if table_start is not None:
                    rec_tables.append(table_start)
                    table_start = None
                rec_mode = table_open = False
                m = NUM_TITLE.match(text)
                if m:
                    cur.update({"h2_id": m.group(1).rstrip("."), "h2_title": m.group(2).strip(),
                                "h3_id": "", "h3_title": ""})
                else:
                    cur.update({"h2_id": "", "h2_title": text, "h3_id": "", "h3_title": ""})
                continue

            if kind in ("h3", "h3_title"):
                flush_rec(pno + 1, rec_strength)
                flush_narrative(pno + 1)
                flush_caption(pno + 1)
                cap_open = False
                if table_start is not None:
                    rec_tables.append(table_start)
                    table_start = None
                rec_mode = table_open = False
                rec_strength = ""
                if kind == "h3":
                    m = NUM_TITLE.match(text)
                    if m:
                        cur.update({"h3_id": m.group(1).rstrip("."),
                                    "h3_title": m.group(2).strip() if len(m.group(2)) > 2 else "",
                                    "h2_id": cur["h2_id"]})
                    else:
                        cur.update({"h3_id": text.rstrip("."), "h3_title": ""})
                elif cur["h3_id"] and not cur["h3_title"]:
                    cur["h3_title"] = text
                continue

            if kind == "rec_header":
                flush_narrative(pno + 1)
                rec_mode, table_open = True, True
                rec_strength = ""
                table_start = (sec_id(), sec_title(), pno + 1, 0)
                continue

            # 表格体（不依赖表头：EAU 有大量无表头的直续表）
            # rating 词与本条首行同行 = 上一条结束、本条开始
            gap = row["y"] - last_y if last_y is not None else 0
            last_y = row["y"]

            if cap_open and not RATING.match(right) and gap <= 20:
                if kind == "body" and text:
                    cap_buf.append(text)
                    if right:
                        cap_buf.append(right)
                    continue
            if cap_open:
                flush_caption(pno + 1)
                cap_open = False

            if RATING.match(right):
                flush_rec(pno + 1, rec_strength)
                flush_narrative(pno + 1)
                rec_strength = right
                if text:
                    rec_pending.append(text)
                continue
            if rec_pending:
                if gap > 16 and not table_open:
                    # 行距拉开 = 表格结束，剩余文本回到叙述
                    flush_rec(pno + 1, rec_strength)
                    rec_strength = ""
                else:
                    if text:
                        rec_pending.append(text)
                    if right:
                        rec_pending.append(right)   # 被 x 边界切开的续行
                    continue

            if text:
                buf.append(text)
                if sum(len(b) for b in buf) >= MAX_CHUNK:
                    flush_narrative(pno + 1)

        flush_narrative(pno + 1)
        # 表格跨页：table_open 保持在页间；页末未配对的行留待下一页
        if table_open:
            rec_mode = True
        else:
            flush_rec(pno + 1, rec_strength)
            rec_strength = ""

    if table_start is not None:
        rec_tables.append(table_start)
    return units, skipped, rec_stats, rec_tables


def group_records(units):
    """按 section 聚合：推荐条目独立成条，叙述打包，另存整节父文档。"""
    records = []
    by_section = defaultdict(list)
    order = []
    for u in units:
        key = (u["section_id"], u["section_title"])
        if key not in by_section:
            order.append(key)
        by_section[key].append(u)

    for key in order:
        sec_id, sec_title = key
        items = by_section[key]
        narrative = [i["text"] for i in items if i["kind"] == "narrative"]
        recs = [i for i in items if i["kind"] == "recommendation"]
        page = min(i["page"] for i in items)

        chunk, size = [], 0
        for para in narrative:
            while len(para) > MAX_CHUNK:      # 单段超限时按句切
                cut = para.rfind(". ", 0, MAX_CHUNK)
                cut = cut + 1 if cut > 200 else MAX_CHUNK
                records.append(dict(kind="narrative", source=SOURCE, version=VERSION,
                                    section_id=sec_id, section_title=sec_title,
                                    page=page, text=para[:cut].strip()))
                para = para[cut:].strip()
            if size + len(para) > MAX_CHUNK and chunk:
                records.append(dict(kind="narrative", source=SOURCE, version=VERSION,
                                    section_id=sec_id, section_title=sec_title,
                                    page=page, text=" ".join(chunk)))
                chunk, size = [], 0
            chunk.append(para)
            size += len(para)
        if chunk:
            records.append(dict(kind="narrative", source=SOURCE, version=VERSION,
                                section_id=sec_id, section_title=sec_title,
                                page=page, text=" ".join(chunk)))

        seen = set()
        for r in recs:
            key = r["text"].strip().lower()
            if key in seen or len(r["text"]) < 30:
                continue
            seen.add(key)
            records.append(dict(kind="recommendation", source=SOURCE, version=VERSION,
                                section_id=sec_id, section_title=sec_title,
                                page=r["page"], text=r["text"], strength=r.get("strength", "")))

        tables = [i for i in items if i["kind"] == "table"]
        for t in tables:
            records.append(dict(kind="table", source=SOURCE, version=VERSION,
                                section_id=sec_id, section_title=sec_title,
                                page=t["page"], caption=t.get("caption", ""),
                                text=t["text"]))

        full = "\n".join(
            narrative
            + [f"[{t.get('caption','Table')}] {t['text']}" for t in tables]
            + [f"[Recommendation | {r.get('strength','')}] {r['text']}" for r in recs]
        )
        records.append(dict(kind="section_full", source=SOURCE, version=VERSION,
                            section_id=sec_id, section_title=sec_title,
                            page=page, text=full))

    for i, r in enumerate(records):
        r["id"] = f"eau-{i:05d}"
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    units, skipped, rec_stats, rec_tables = build_units(args.pdf)
    records = group_records(units)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "records.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    kinds = Counter(r["kind"] for r in records)
    lens = [len(r["text"]) for r in records if r["kind"] != "section_full"]
    secs = {(r["section_id"], r["section_title"]) for r in records}
    secs_with_rec = {r["section_id"] for r in records if r["kind"] == "recommendation"}
    stats = {
        "records": len(records),
        "by_kind": dict(kinds),
        "sections": len(secs),
        "sections_without_title": sum(1 for s in secs if not s[1]),
        "sections_with_recommendation": len(secs_with_rec),
        "chunk_len": {"min": min(lens), "median": statistics.median(lens), "max": max(lens)},
        "recommendation_strength": dict(rec_stats),
        "skipped_toc_pages": skipped["toc_pages"],
        "reference_pages": [skipped["reference_pages"][0], skipped["reference_pages"][-1]]
        if skipped["reference_pages"] else [],
        "reference_page_count": len(skipped["reference_pages"]),
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
