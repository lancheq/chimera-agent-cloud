"""01_normalize_eau.py
归一化官方 EAU ChromaDB 导出的 chunks，产出统一 schema 的 JSONL。

输入：raw/eau_official_chunks.jsonl（1501 条，section 字段含大量参考文献碎片）
输出：normalized/eau_normalized.jsonl

清洗规则：
1. 识别"参考文献碎片"section —— 形如 "163 e7." / "3 trial. Lancet Oncol, 2016. 17: 727." 等
   判据：section 不以数字开头（非 N.N.N 格式），且含期刊名/页码/年份碎片
   → 保留 chunk 文本，但把 section 回填为前一条合法 section
2. 真正的 section header 形如 "5.6.4 Summary of evidence..."
3. 去重（完全相同 text 只留一条）
4. 按 section 关键词给 task_tag：T1=biopsy/MRI/PSA、T2=treatment/management/risk、T3=BCR/recurrence/follow-up/survival
"""
import json
import re
from pathlib import Path
from collections import Counter

RAW = Path("/Users/lancheq/Desktop/项目/guideline_dataset/raw/eau_official_chunks.jsonl")
OUT = Path("/Users/lancheq/Desktop/项目/guideline_dataset/normalized/eau_normalized.jsonl")

# 合法 section：以 N.N.N 数字编号开头
# 真正合法的 section header：必须以 N.N 格式（含小数点）开头，且标题不长于 120 字
# 排除"2 first-degree relatives..."这种正文句子被误识为 section 的情况
SECTION_NUM_RE = re.compile(r"^\d+\.\d+(?:\.\d+){0,3}\s+\S")

# 参考文献碎片特征：含期刊名/页码/年份，且不是合法 N.N 格式
JOURNAL_HINTS = [
    "Lancet Oncol", "Lancet", "Eur Urol", "JAMA", "J Clin Oncol", "N Engl J Med",
    "Int J Radiat Oncol Biol Phys", "Cancers (Basel)", "Nat Med",
    "BJU Int", "Urology", "J Urol", "Movember", "Consortium", "Multicenter Randomized",
    "Imaging Trial", "HERO Study",
]

def is_valid_section(sec: str) -> bool:
    """合法 section：N.N.N 格式，且标题不长于 120 字（排除正文）"""
    if not sec:
        return False
    if SECTION_NUM_RE.match(sec) and len(sec) < 120:
        return True
    return False

def is_ref_fragment(sec: str) -> bool:
    """非合法 section（正文句子或参考文献碎片被误识）"""
    if not sec:
        return True
    if is_valid_section(sec):
        return False
    # 纯数字开头但非 N.N 格式（如 "163 e7." / "2 first-degree relatives..."）
    if re.match(r"^\d+\s+\S", sec) and not re.match(r"^\d+\.\d+", sec):
        return True
    for hint in JOURNAL_HINTS:
        if hint in sec:
            return True
    if re.search(r"\d{4}\.\s*\d+", sec):
        return True
    return False

# task 标签关键词
T1_KEYWORDS = ["biopsy", "PSA", "screening", "MRI", "PI-RADS", "PIRADS", "detection", "early diagnosis", "mpMRI", "bpMRI"]
T2_KEYWORDS = ["treatment", "management", "risk group", "risk stratif", "radical prostatectomy", "radiation", "active surveillance", "hormone", "ADT", "RP", "EBRT", "brachytherapy"]
T3_KEYWORDS = ["BCR", "biochemical recurrence", "follow-up", "follow up", "salvage", "recurrence", "survival", "PSMA", "castration-resistant", "CRPC", "metastatic"]

def assign_tags(text: str, section: str) -> list:
    combined = (section + " " + text[:500]).lower()
    tags = []
    for kw in T1_KEYWORDS:
        if kw.lower() in combined:
            tags.append("T1")
            break
    for kw in T2_KEYWORDS:
        if kw.lower() in combined:
            tags.append("T2")
            break
    for kw in T3_KEYWORDS:
        if kw.lower() in combined:
            tags.append("T3")
            break
    return tags if tags else ["GENERAL"]

def main():
    records = []
    with open(RAW, encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))

    print(f"Loaded {len(records)} raw chunks")

    # 统计原始 section 质量
    valid_count = sum(1 for r in records if is_valid_section(r["section"]))
    ref_count = sum(1 for r in records if is_ref_fragment(r["section"]))
    empty_count = sum(1 for r in records if not r["section"])
    print(f"  valid sections: {valid_count}")
    print(f"  ref fragments:  {ref_count}")
    print(f"  empty sections: {empty_count}")

    # 修复 section：遇到 ref fragment 用前一条合法 section 回填
    last_valid_sec = ""
    fixed = 0
    for r in records:
        if is_valid_section(r["section"]):
            last_valid_sec = r["section"]
        elif is_ref_fragment(r["section"]) or not r["section"]:
            if last_valid_sec:
                r["section_original"] = r["section"]
                r["section"] = last_valid_sec
                fixed += 1

    print(f"  fixed sections (backfilled): {fixed}")

    # 去重（相同 text 只留一条，保留 section 最长的那条）
    seen = {}
    for r in records:
        text_key = r["text"].strip()
        if text_key not in seen:
            seen[text_key] = r
        else:
            # 保留 section 更长（更具体）的那条
            if len(r["section"]) > len(seen[text_key]["section"]):
                seen[text_key] = r
    deduped = list(seen.values())
    print(f"  after dedup: {len(deduped)} (removed {len(records) - len(deduped)})")

    # 给每条加 task_tag + 统一 schema
    out_records = []
    for i, r in enumerate(deduped):
        tags = assign_tags(r["text"], r["section"])
        rec = {
            "id": f"eau-{i:04d}",
            "source": "EAU",
            "version": "2025-LimitedUpdate-March",
            "year": 2025,
            "section_id": r["section"].split(" ")[0] if " " in r["section"] else "",
            "section_title": " ".join(r["section"].split(" ")[1:]) if " " in r["section"] else r["section"],
            "page": r["page"],
            "text": r["text"].strip(),
            "task_tags": tags,
        }
        out_records.append(rec)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in out_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(out_records)} normalized chunks to {OUT}")

    # 按 task_tag 统计
    tag_counter = Counter()
    for r in out_records:
        for t in r["task_tags"]:
            tag_counter[t] += 1
    print("Tag distribution (a chunk may have multiple tags):")
    for t, n in tag_counter.most_common():
        print(f"  {t}: {n}")

    # 按 section_id 前两级统计
    sec_counter = Counter()
    for r in out_records:
        parts = r["section_id"].split(".")
        if len(parts) >= 2:
            sec_counter[f"{parts[0]}.{parts[1]}"] += 1
        elif parts[0]:
            sec_counter[parts[0]] += 1
    print("\nTop 15 section groups:")
    for s, n in sec_counter.most_common(15):
        print(f"  [{n:3d}] {s}")

if __name__ == "__main__":
    main()
