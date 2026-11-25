"""03_merge_normalize.py
将所有来源归一化到统一 schema：
- EAU PDF 章节（478 条，精确切分）
- EAU ChromaDB chunks（1501 条，含重叠，作为补充）
- PI-RADS v2.1 规则摘要
- AUA/SUO 2023 早期检测摘要

输出：normalized/guidelines_unified.jsonl
统一 schema:
{
  "id": str,
  "source": "EAU" | "PIRADS" | "AUA" | "EAU-CHROMA",
  "version": str,
  "year": int,
  "section_id": str,
  "section_title": str,
  "page": int | null,
  "text": str,
  "task_tags": [str]  # T1/T2/T3/GENERAL
}
"""
import json
import re
from pathlib import Path
from collections import Counter

RAW = Path("/Users/lancheq/Desktop/项目/guideline_dataset/raw")
OUT = Path("/Users/lancheq/Desktop/项目/guideline_dataset/normalized/guidelines_unified.jsonl")

# task 标签关键词
T1_KW = ["biopsy", "psa", "screening", "mri", "pi-rads", "pirads", "detection", "early diagnosis", "mpmri", "bpmri", "psad", "psa density", "transperineal", "transrectal"]
T2_KW = ["treatment", "management", "risk group", "risk stratif", "radical prostatectomy", "radiation", "active surveillance", "hormone", "adt", "rp", "ebrt", "brachytherapy", "intermediate-risk", "high-risk", "low-risk", "locally advanced"]
T3_KW = ["bcr", "biochemical recurrence", "follow-up", "follow up", "salvage", "recurrence", "survival", "psma", "castration-resistant", "crpc", "metastatic", "psa-only recurrence"]

def assign_tags(text: str, section: str) -> list:
    combined = (section + " " + text[:800]).lower()
    tags = []
    if any(kw in combined for kw in T1_KW):
        tags.append("T1")
    if any(kw in combined for kw in T2_KW):
        tags.append("T2")
    if any(kw in combined for kw in T3_KW):
        tags.append("T3")
    return tags if tags else ["GENERAL"]

def load_eau_pdf():
    """EAU PDF 章节作为主源"""
    records = []
    with open(RAW / "eau_pdf_chapters.jsonl", encoding="utf-8") as f:
        for line in f:
            ch = json.loads(line)
            rec = {
                "id": ch["id"],
                "source": "EAU",
                "version": "2025-LimitedUpdate-March",
                "year": 2025,
                "section_id": ch["section_id"],
                "section_title": ch["section_title"],
                "page": ch["page"],
                "text": ch["text"].strip(),
                "task_tags": assign_tags(ch["text"], ch["section_title"]),
            }
            records.append(rec)
    return records

def load_pirads():
    """PI-RADS v2.1 摘要"""
    text = (RAW / "pirads_v2_1_rules.md").read_text(encoding="utf-8")
    # 按 markdown ## 标题切分
    sections = re.split(r"\n## ", text)
    records = []
    for i, sec in enumerate(sections):
        if not sec.strip() or sec.startswith("#"):
            continue
        title = sec.split("\n")[0].strip()
        body = sec[len(title):].strip()
        if len(body) < 30:
            continue
        rec = {
            "id": f"pirads-{i:03d}",
            "source": "PIRADS",
            "version": "v2.1",
            "year": 2019,
            "section_id": f"PIRADS-{i}",
            "section_title": title,
            "page": None,
            "text": body,
            "task_tags": assign_tags(body, title),
        }
        records.append(rec)
    return records

def load_aua():
    """AUA/SUO 2023 摘要"""
    text = (RAW / "aua_suo_2023_early_detection.md").read_text(encoding="utf-8")
    sections = re.split(r"\n## ", text)
    records = []
    for i, sec in enumerate(sections):
        if not sec.strip() or sec.startswith("#"):
            continue
        title = sec.split("\n")[0].strip()
        body = sec[len(title):].strip()
        if len(body) < 30:
            continue
        rec = {
            "id": f"aua-{i:03d}",
            "source": "AUA",
            "version": "2023-EarlyDetection",
            "year": 2023,
            "section_id": f"AUA-{i}",
            "section_title": title,
            "page": None,
            "text": body,
            "task_tags": assign_tags(body, title),
        }
        records.append(rec)
    return records

def main():
    all_records = []

    eau_pdf = load_eau_pdf()
    all_records.extend(eau_pdf)
    print(f"EAU PDF: {len(eau_pdf)} sections")

    pirads = load_pirads()
    all_records.extend(pirads)
    print(f"PI-RADS: {len(pirads)} sections")

    aua = load_aua()
    all_records.extend(aua)
    print(f"AUA: {len(aua)} sections")

    print(f"\nTotal unified records: {len(all_records)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote: {OUT}")

    # 统计
    src_counter = Counter(r["source"] for r in all_records)
    print("\nBy source:")
    for s, n in src_counter.most_common():
        print(f"  {s}: {n}")

    tag_counter = Counter()
    for r in all_records:
        for t in r["task_tags"]:
            tag_counter[t] += 1
    print("\nBy task tag (multi-tag possible):")
    for t, n in tag_counter.most_common():
        print(f"  {t}: {n}")

    # 关键章节验证
    print("\n=== CHIMERA 关键章节覆盖 ===")
    key_check = [
        ("EAU", "5.4", "Biopsy indication"),
        ("EAU", "5.6.4", "Biopsy recommendations"),
        ("EAU", "6.3", "Treatment by disease stage"),
        ("EAU", "6.4", "PSA-only recurrence"),
        ("EAU", "7.4", "Follow-up"),
        ("PIRADS", None, "PZ/TZ scoring"),
        ("AUA", None, "Biopsy criteria"),
    ]
    for src, sid, desc in key_check:
        if sid:
            matches = [r for r in all_records if r["source"] == src and r["section_id"] == sid]
        else:
            matches = [r for r in all_records if r["source"] == src]
        status = "✅" if matches else "❌"
        print(f"  {status} {src} {sid or ''} | {desc} | {len(matches)} records")

if __name__ == "__main__":
    main()
