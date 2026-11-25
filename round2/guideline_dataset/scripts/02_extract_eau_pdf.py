"""02_extract_eau_pdf.py
从 EAU 2025 完整 PDF 重新提取章节文本（比官方 ChromaDB 更精确）。

输入：raw/eau_2025_prostate.pdf（251 页）
输出：raw/eau_pdf_chapters.jsonl

用 PyMuPDF (fitz) 提取，按章节号（N.N.N）切分。
"""
import fitz
import re
import json
from pathlib import Path

PDF = Path("/Users/lancheq/Desktop/项目/guideline_dataset/raw/eau_2025_prostate.pdf")
OUT = Path("/Users/lancheq/Desktop/项目/guideline_dataset/raw/eau_pdf_chapters.jsonl")

# 章节号正则：N / N.N / N.N.N / N.N.N.N
SECTION_HEADER_RE = re.compile(r"^(\d+(?:\.\d+){1,4})\s+(.{2,120})$", re.MULTILINE)

def extract_all_text(pdf_path: str) -> list[dict]:
    """提取每页文本"""
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text")
        if text.strip():
            pages.append({"page": i + 1, "text": text})
    return pages

def find_section_headers(text: str) -> list[tuple[int, str, str]]:
    """找到所有章节头的 (offset, section_id, section_title)"""
    headers = []
    for m in SECTION_HEADER_RE.finditer(text):
        sec_id = m.group(1)
        sec_title = m.group(2).strip()
        # 过滤参考文献特征：含期刊缩写/页码
        if any(x in sec_title for x in ["Lancet", "Eur Urol", "JAMA", "J Urol", "NCI", "Movember"]):
            continue
        if re.search(r"\d{4}\.\s*\d+", sec_title):
            continue
        # 章节标题不应超长
        if len(sec_title) > 100:
            continue
        headers.append((m.start(), sec_id, sec_title))
    return headers

def split_by_sections(pages: list[dict]) -> list[dict]:
    """按章节头切分全文"""
    # 合并全文
    full_text = ""
    page_offsets = []
    for p in pages:
        page_offsets.append((len(full_text), p["page"]))
        full_text += p["text"] + "\n"

    headers = find_section_headers(full_text)
    print(f"Found {len(headers)} section headers")

    # 去重连续相同 section_id
    deduped_headers = []
    last_sec_id = None
    for offset, sec_id, title in headers:
        if sec_id != last_sec_id:
            deduped_headers.append((offset, sec_id, title))
            last_sec_id = sec_id
    print(f"After dedup consecutive: {len(deduped_headers)}")

    # 切分
    chapters = []
    for i, (offset, sec_id, title) in enumerate(deduped_headers):
        end = deduped_headers[i + 1][0] if i + 1 < len(deduped_headers) else len(full_text)
        chunk_text = full_text[offset:end].strip()
        if len(chunk_text) < 30:
            continue

        # 找页码
        page = 1
        for po, pn in reversed(page_offsets):
            if po <= offset:
                page = pn
                break

        chapters.append({
            "section_id": sec_id,
            "section_title": title,
            "page": page,
            "text": chunk_text,
        })

    return chapters

def main():
    pages = extract_all_text(str(PDF))
    print(f"Extracted {len(pages)} pages from PDF")

    chapters = split_by_sections(pages)
    print(f"Split into {len(chapters)} chapters (incl TOC)")

    # 去重：同一 section_id 保留 text 最长的（正文版而非目录版）
    by_sec = {}
    for ch in chapters:
        sid = ch["section_id"]
        if sid not in by_sec or len(ch["text"]) > len(by_sec[sid]["text"]):
            by_sec[sid] = ch
    chapters = list(by_sec.values())
    # 按 page 排序
    chapters.sort(key=lambda c: (c["page"], c["section_id"]))
    print(f"After dedup (keep longest per section_id): {len(chapters)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for i, ch in enumerate(chapters):
            rec = {
                "id": f"eau-pdf-{i:04d}",
                "source": "EAU-PDF-2025",
                "section_id": ch["section_id"],
                "section_title": ch["section_title"],
                "page": ch["page"],
                "text": ch["text"],
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Wrote {len(chapters)} chapters to {OUT}")

    # 打印前 20 个章节
    print("\nFirst 20 sections:")
    for ch in chapters[:20]:
        print(f"  p{ch['page']:>3} | {ch['section_id']:<10} | {ch['section_title'][:60]}")

if __name__ == "__main__":
    main()
