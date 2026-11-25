#!/usr/bin/env python3
"""输出质量检查 — 统计决策分布 + 检测异常"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "test/output"

def load_predictions(task):
    d = OUT / f"task{task}"
    results = []
    for f in sorted(d.glob("*/prediction.json")):
        with open(f) as fh:
            results.append(json.load(fh))
    return results

def check_task1():
    preds = load_predictions(1)
    print(f"\n{'='*60}")
    print(f"Task 1: 前列腺活检决策 ({len(preds)} cases)")
    print(f"{'='*60}")

    decisions = Counter(p["biopsy_decision"] for p in preds)
    confidences = Counter(p["confidence"] for p in preds)

    print(f"\n决策分布:")
    for k, v in decisions.most_common():
        print(f"  {k:10s}: {v:4d} ({v/len(preds)*100:.1f}%)")

    print(f"\n置信度分布:")
    for k, v in confidences.most_common():
        print(f"  {k:12s}: {v:4d} ({v/len(preds)*100:.1f}%)")

    # 异常检测
    issues = []
    for p in preds:
        cid = p["case_id"]
        if len(p["free_text"]) < 40:
            issues.append(f"  ⚠️ {cid}: free_text 过短 ({len(p['free_text'])} chars)")
        if not p.get("reveal_sequence"):
            pass  # mock 数据可能没有 reveal
        weights = p.get("variable_weights", {})
        decisive = [k for k, v in weights.items() if v == "decisive"]
        if not decisive:
            issues.append(f"  ⚠️ {cid}: 没有 decisive 权重变量")
        if p["biopsy_decision"] not in ("yes", "no"):
            issues.append(f"  ❌ {cid}: 决策值异常 '{p['biopsy_decision']}'")

    # 决策极度不平衡
    yes_ratio = decisions.get("yes", 0) / len(preds)
    if yes_ratio < 0.1 or yes_ratio > 0.9:
        issues.append(f"  ⚠️ 决策极度不平衡: yes={yes_ratio*100:.1f}%")

    print(f"\n异常检测 ({len(issues)} 项):")
    for i in issues[:10]:
        print(i)
    if len(issues) > 10:
        print(f"  ... 还有 {len(issues)-10} 项")

def check_task2():
    preds = load_predictions(2)
    print(f"\n{'='*60}")
    print(f"Task 2: 治疗决策 ({len(preds)} cases)")
    print(f"{'='*60}")

    actions = Counter(p["treatment_recommendation"]["primary"] for p in preds)
    confidences = Counter(p["confidence"] for p in preds)

    print(f"\n治疗决策分布:")
    for k, v in actions.most_common():
        print(f"  {k:25s}: {v:4d} ({v/len(preds)*100:.1f}%)")

    print(f"\n置信度分布:")
    for k, v in confidences.most_common():
        print(f"  {k:12s}: {v:4d} ({v/len(preds)*100:.1f}%)")

    issues = []
    for p in preds:
        cid = p["case_id"]
        if len(p["free_text"]) < 40:
            issues.append(f"  ⚠️ {cid}: free_text 过短")
        weights = p.get("variable_weights", {})
        decisive = [k for k, v in weights.items() if v == "decisive"]
        if not decisive:
            issues.append(f"  ⚠️ {cid}: 没有 decisive 权重变量")
        tr = p["treatment_recommendation"]
        if tr["primary"] == "active_treatment" and not tr.get("modalities"):
            issues.append(f"  ⚠️ {cid}: active_treatment 但无 modalities")

    print(f"\n异常检测 ({len(issues)} 项):")
    for i in issues[:10]:
        print(i)
    if len(issues) > 10:
        print(f"  ... 还有 {len(issues)-10} 项")

def check_task3():
    preds = load_predictions(3)
    print(f"\n{'='*60}")
    print(f"Task 3: 复发时间预测 ({len(preds)} cases)")
    print(f"{'='*60}")

    events = Counter(p["event"] for p in preds)
    months = [p["months_to_recurrence"] for p in preds]

    print(f"\n事件分布:")
    for k, v in sorted(events.items()):
        print(f"  event={k}: {v:4d} ({v/len(preds)*100:.1f}%)")

    print(f"\n复发时间统计 (月):")
    print(f"  min:    {min(months):.1f}")
    print(f"  max:    {max(months):.1f}")
    print(f"  mean:   {sum(months)/len(months):.1f}")
    sorted_m = sorted(months)
    print(f"  median: {sorted_m[len(sorted_m)//2]:.1f}")

    # 分桶
    buckets = [(0, 12), (12, 24), (24, 36), (36, 60), (60, 120), (120, float('inf'))]
    print(f"\n时间分桶:")
    for lo, hi in buckets:
        cnt = sum(1 for m in months if lo <= m < hi)
        label = f"{lo}-{hi}m" if hi != float('inf') else f"{lo}m+"
        print(f"  {label:10s}: {'█' * (cnt // 2)} {cnt}")

    issues = []
    for p in preds:
        cid = p["case_id"]
        if len(p["free_text"]) < 40:
            issues.append(f"  ⚠️ {cid}: free_text 过短")
        if p["event"] == 0 and p["months_to_recurrence"] > 120:
            issues.append(f"  ⚠️ {cid}: event=0 但 months={p['months_to_recurrence']}")
        if p["months_to_recurrence"] == 0:
            issues.append(f"  ⚠️ {cid}: months_to_recurrence=0")

    print(f"\n异常检测 ({len(issues)} 项):")
    for i in issues[:10]:
        print(i)
    if len(issues) > 10:
        print(f"  ... 还有 {len(issues)-10} 项")

def main():
    print("CHIMERA-agent 输出质量检查")
    print(f"时间: {Path('/proc/uptime').read_text().strip() if False else ''}")

    for task in [1, 2, 3]:
        try:
            if task == 1:
                check_task1()
            elif task == 2:
                check_task2()
            elif task == 3:
                check_task3()
        except Exception as e:
            print(f"\nTask {task} 检查失败: {e}")

    print(f"\n{'='*60}")
    print("检查完成")

if __name__ == "__main__":
    main()
