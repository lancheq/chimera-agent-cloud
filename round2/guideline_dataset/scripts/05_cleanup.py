"""05_cleanup.py
修复 2 个问题：
1. 删除目录残留短章节（text < 100 且有子章节覆盖的父章节）
2. 删除误识 section_id（统计表格数字被当成章节号）

输入：normalized/guidelines_unified.jsonl（491 条）
输出：normalized/guidelines_unified.jsonl（覆盖，约 423 条）
"""
import json
import re
from pathlib import Path

NORM = Path("/Users/lancheq/Desktop/项目/guideline_dataset/normalized/guidelines_unified.jsonl")
OUT = NORM  # 覆盖原文件

def load():
    with open(NORM, encoding="utf-8") as f:
        return [json.loads(l) for l in f]

def save(records):
    with open(OUT, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

def main():
    records = load()
    print(f"Before cleanup: {len(records)}")

    # --- 修复 1：误识 section_id（统计表格数字） ---
    # 特征：section_id 含小数但不是合法章节号（合法应为指南目录结构 N.N.N）
    # 这些是表格里的统计值如 1.18 / 9.5 / 0.46 / 50.0 / 74.4 / 10.3
    # 判据：section_id 数值 > 20 或 section_id 以 0. 开头（合法章节不会从 0. 开始）
    # 同时 section_title 为空或无意义
    bogus_ids = set()
    legit_sections = set()
    # 先收集所有合法的 section_id 前缀（用于判断哪些是真正的章节）
    for r in records:
        if r["source"] == "EAU":
            parts = r["section_id"].split(".")
            if parts[0].isdigit() and 1 <= int(parts[0]) <= 9:
                legit_sections.add(r["section_id"])

    removed_bogus = []
    for r in records:
        if r["source"] != "EAU":
            continue
        sid = r["section_id"]
        parts = sid.split(".")
        # 误识特征：第一段是 0，或数值 > 20（EAU 章节号最大到 8.x）
        if parts[0] == "0" or (parts[0].isdigit() and int(parts[0]) > 20):
            removed_bogus.append(r)
            bogus_ids.add(r["id"])
        # 1.18 这种：第一段=1 合法，但第二段=18 超出正常子节范围
        # 需要看是否有 1.x 的其他子节存在
        elif len(parts) == 2 and parts[0].isdigit():
            siblings = [s for s in legit_sections if s.startswith(parts[0] + ".") and s != sid]
            if siblings:
                sibling_nums = []
                for sib in siblings:
                    sib_parts = sib.split(".")
                    if len(sib_parts) >= 2 and sib_parts[1].isdigit():
                        sibling_nums.append(int(sib_parts[1]))
                if sibling_nums:
                    max_sibling = max(sibling_nums)
                    my_second = int(parts[1]) if parts[1].isdigit() else 0
                    # 如果我的第二段远超兄弟节点的最大值（>3倍），判定为误识
                    if my_second > max_sibling * 3 and my_second > 10:
                        removed_bogus.append(r)
                        bogus_ids.add(r["id"])

    print(f"\n=== 修复 1：误识 section_id ===")
    print(f"删除: {len(removed_bogus)} 条")
    for r in removed_bogus:
        print(f"  {r['id']} | sec_id={r['section_id']:<8} | text={r['text'][:60]!r}")

    records = [r for r in records if r["id"] not in bogus_ids]

    # --- 修复 2：目录残留短章节 ---
    # 判据：text < 100 chars 且该 section_id 有子章节（子章节包含正文）
    removed_short = []
    keep_ids = set()
    for r in records:
        if r["source"] != "EAU":
            keep_ids.add(r["id"])
            continue
        if len(r["text"]) >= 100:
            keep_ids.add(r["id"])
            continue
        # text < 100：检查是否有子章节
        parent = r["section_id"]
        children = [x for x in records if x["source"] == "EAU"
                    and x["section_id"].startswith(parent + ".")
                    and len(x["text"]) >= 100]
        if children:
            # 有子章节覆盖 → 删除父章节
            removed_short.append(r)
        else:
            # 无子章节 → 保留（可能是真正简短的章节）
            keep_ids.add(r["id"])

    print(f"\n=== 修复 2：目录残留短章节（有子章节覆盖）===")
    print(f"删除: {len(removed_short)} 条")
    for r in removed_short[:10]:
        children = [x for x in records if x["source"] == "EAU"
                    and x["section_id"].startswith(r["section_id"] + ".")
                    and len(x["text"]) >= 100]
        child_text_total = sum(len(c["text"]) for c in children)
        print(f"  {r['id']} | sec={r['section_id']:<10} | len={len(r['text']):>3} | children={len(children):>2} | child_text={child_text_total:>6}")
    if len(removed_short) > 10:
        print(f"  ... and {len(removed_short) - 10} more")

    records = [r for r in records if r["id"] in keep_ids]

    # --- 重新编号 id ---
    for i, r in enumerate(records):
        prefix = r["source"].lower()
        r["id"] = f"{prefix}-{i:04d}"

    print(f"\nAfter cleanup: {len(records)}")
    print(f"  Removed total: {len(removed_bogus) + len(removed_short)}")

    save(records)

    # --- 验证修复效果 ---
    print(f"\n=== 修复后统计 ===")
    from collections import Counter
    src_counter = Counter(r["source"] for r in records)
    for s, n in src_counter.most_common():
        print(f"  {s}: {n}")

    tag_counter = Counter()
    for r in records:
        for t in r["task_tags"]:
            tag_counter[t] += 1
    print("\n  Task tags:")
    for t, n in tag_counter.most_common():
        print(f"    {t}: {n}")

    # text 长度分布
    eau = [r for r in records if r["source"] == "EAU"]
    lens = [len(r["text"]) for r in eau]
    print(f"\n  EAU text length: min={min(lens)} max={max(lens)} mean={sum(lens)//len(lens)}")
    short_left = [r for r in eau if len(r["text"]) < 100]
    print(f"  EAU text < 100 remaining: {len(short_left)}")
    for r in short_left:
        print(f"    {r['id']} | sec={r['section_id']} | len={len(r['text'])} | {r['text'][:50]!r}")

    # 关键章节验证
    print(f"\n=== 关键章节验证 ===")
    key = ["5.4.2.2", "5.6.4", "6.3.1", "6.3.2", "6.3.3", "6.4.1", "6.4.5.1.1", "7.4"]
    for sid in key:
        matches = [r for r in records if r["section_id"] == sid]
        if matches:
            r = matches[0]
            print(f"  ✅ {sid:<12} | p{r['page']:>3} | {len(r['text']):>6} chars | {r['section_title'][:50]}")
        else:
            # 检查子章节
            children = [r for r in records if r["section_id"].startswith(sid + ".")]
            if children:
                total = sum(len(c["text"]) for c in children)
                print(f"  ✅ {sid:<12} | parent deleted, {len(children)} children cover {total} chars")
            else:
                print(f"  ❌ {sid:<12} | NOT FOUND")

if __name__ == "__main__":
    main()
