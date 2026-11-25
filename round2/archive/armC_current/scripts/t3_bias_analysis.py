#!/usr/bin/env python3
"""T3 event 0→1 误报偏置分析"""
import json, glob, os
from collections import Counter

BASE = "test/output.final_20260826/task3"
GT_DIR = "data/task3/agent_input"

results = []
for pred_path in sorted(glob.glob(f"{BASE}/*/prediction.json")):
    case_id = os.path.basename(os.path.dirname(pred_path))
    # GT 在 agent_input/{case_id}/prostate-time-to-recurrence-or-last-follow-up.json
    gt_path = os.path.join(GT_DIR, case_id, "prostate-time-to-recurrence-or-last-follow-up.json")
    if not os.path.exists(gt_path):
        continue

    with open(pred_path) as f:
        pred = json.load(f)
    with open(gt_path) as f:
        gt = json.load(f)

    gt_event = gt.get("event", gt.get("decision", {}).get("event"))
    pred_event = pred.get("event", pred.get("decision", {}).get("event"))
    pred_months = pred.get("months_to_recurrence", pred.get("decision", {}).get("months_to_recurrence"))

    results.append({
        "case_id": case_id,
        "gt_event": gt_event,
        "pred_event": pred_event,
        "pred_months": pred_months
    })

# 混淆矩阵
cm = Counter()
for r in results:
    cm[(r["gt_event"], r["pred_event"])] += 1

total = len(results)
gt0_total = sum(1 for r in results if r["gt_event"] == 0)
gt1_total = sum(1 for r in results if r["gt_event"] == 1)
false_positive = cm.get((0, 1), 0)  # GT=0, pred=1 (误报)
false_negative = cm.get((1, 0), 0)  # GT=1, pred=0 (漏报)
true_positive = cm.get((1, 1), 0)
true_negative = cm.get((0, 0), 0)

# 0→1 误报率
fp_rate = false_positive / gt0_total if gt0_total > 0 else 0

# GT event=0 的 case pred months 分布
gt0_pred_months = [r["pred_months"] for r in results if r["gt_event"] == 0 and r["pred_event"] == 1]

print(f"=== T3 偏置专项分析 ===")
print(f"总 case 数: {total}")
print(f"GT event=0: {gt0_total}, GT event=1: {gt1_total}")
print(f"\n混淆矩阵:")
print(f"  GT=0/pred=0 (TN): {true_negative}")
print(f"  GT=0/pred=1 (FP, 误报): {false_positive}")
print(f"  GT=1/pred=0 (FN, 漏报): {false_negative}")
print(f"  GT=1/pred=1 (TP): {true_positive}")
print(f"\n0→1 误报率: {false_positive}/{gt0_total} = {fp_rate:.1%}")
print(f"\nGT event=0 但 pred=1 的 months 分布: {gt0_pred_months}")
print(f"\n判定:")
if fp_rate < 0.30:
    print(f"  <30% → 偏置不严重，可接受，写入报告作 caveat")
elif fp_rate < 0.50:
    print(f"  30-50% → 偏置明显，建议修但不阻塞")
else:
    print(f"  ≥50% → 严重偏置，必须修")

# 输出 JSON
with open("scripts/t3_bias_report.json", "w") as f:
    json.dump({
        "total": total,
        "confusion_matrix": {"TN": true_negative, "FP": false_positive, "FN": false_negative, "TP": true_positive},
        "fp_rate": fp_rate,
        "gt0_pred_months": gt0_pred_months
    }, f, indent=2)
print(f"\nJSON 报告: scripts/t3_bias_report.json")
