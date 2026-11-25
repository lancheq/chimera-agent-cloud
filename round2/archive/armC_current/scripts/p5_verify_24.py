#!/usr/bin/env python3
"""P5 生效验证: 24 fallback case 重跑后 free_text 对比 + 抽查。"""
import json
import random

d = "test/output.t1_full_v2_20260829/task1"
before = {s["case_id"]: s["free_text"] for s in json.load(open("scripts/t1_fallback24_before_P5.json"))}
poison_before = sum(1 for ft in before.values() if "<function=" in ft or "<tool_call" in ft)
poison_after = 0
newwarn = 0
n = 0
cleaned = []
print("=== 24 case before/after free_text 对比 ===")
for cid, ft_old in sorted(before.items()):
    p = f"{d}/{cid}/prediction.json"
    j = json.load(open(p))
    ft_new = j.get("free_text", "")
    n += 1
    bad_new = "<function=" in ft_new or "<tool_call" in ft_new
    if bad_new:
        poison_after += 1
    t = json.load(open(f"{d}/{cid}/trace.json"))
    if any("final answer extracted" in w for w in (t.get("form_fill_warnings") or [])):
        newwarn += 1
    old_bad = "<function=" in ft_old or "<tool_call" in ft_old
    if old_bad and not bad_new:
        cleaned.append(cid)
        print(f"  [CLEANED] {cid}  len {len(ft_old)}->{len(ft_new)}")
print()
print(f"中毒(工具语法) 前={poison_before} 后={poison_after}  新warning命中={newwarn}/{n}  CLEANED={len(cleaned)}")
print()
print("=== 抽查 5 case free_text（P5 生效铁证）===")
random.seed(7)
for cid in random.sample(list(before.keys()), 5):
    ft = json.load(open(f"{d}/{cid}/prediction.json")).get("free_text", "")
    has = "<function=" in ft or "<tool_call" in ft
    print(f"-- {cid}: 含工具语法={has}")
    print("   ", ft[:110].replace(chr(10), " "))
