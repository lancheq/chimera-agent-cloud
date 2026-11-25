#!/usr/bin/env python3
"""
CHIMERA-agent 本地评估脚本 — 对比 prediction vs ground truth
计算准确率、F1、混淆矩阵、生存分析指标等

用法:
  python scripts/eval_against_truth.py
  python scripts/eval_against_truth.py --task 1    # 只看 task1
"""
import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "test/output"

# ── 数据加载 ──────────────────────────────────────────────────────────────

def load_ground_truth(task: int) -> dict[str, dict]:
    """加载 ground truth: 决策 + reasoning"""
    task_dir = DATA / f"task{task}" / "agent_input"
    results = {}

    # ground truth 文件名映射
    decision_files = {
        1: "prostate-biopsy-decision.json",
        2: "prostate-treatment-decision.json",
        3: "prostate-time-to-recurrence-or-last-follow-up.json",
    }
    reasoning_files = {
        1: "prostate-biopsy-decision-reasoning.json",
        2: "prostate-treatment-decision-reasoning.json",
        3: "prostate-time-to-recurrence-or-last-follow-up-reasoning.json",
    }

    for case_dir in sorted(task_dir.iterdir()):
        if not case_dir.is_dir():
            continue
        cid = case_dir.name
        gt = {}

        # 决策
        df = case_dir / decision_files[task]
        if df.exists():
            gt["decision"] = json.loads(df.read_text())

        # reasoning
        rf = case_dir / reasoning_files[task]
        if rf.exists():
            gt["reasoning"] = json.loads(rf.read_text())

        if gt:
            results[cid] = gt
    return results


def load_predictions(task: int) -> dict[str, dict]:
    """加载模型预测"""
    task_dir = OUT / f"task{task}"
    results = {}
    if not task_dir.exists():
        return results
    for case_dir in sorted(task_dir.iterdir()):
        if not case_dir.is_dir():
            continue
        pf = case_dir / "prediction.json"
        if pf.exists():
            results[case_dir.name] = json.loads(pf.read_text())
    return results


# ── 评估指标 ──────────────────────────────────────────────────────────────

def eval_task1(gt: dict, pred: dict) -> dict:
    """Task 1: 二分类 (yes/no)"""
    metrics = {"total": 0, "correct": 0, "tp": 0, "fp": 0, "tn": 0, "fn": 0,
               "errors": [], "confidence_match": 0, "weight_match": 0}

    for cid, g in gt.items():
        p = pred.get(cid)
        if not p:
            continue
        metrics["total"] += 1

        gt_dec = g.get("decision", "")
        pr_dec = p.get("biopsy_decision", "")

        if gt_dec == pr_dec:
            metrics["correct"] += 1
            if gt_dec == "yes":
                metrics["tp"] += 1
            else:
                metrics["tn"] += 1
        else:
            if pr_dec == "yes":
                metrics["fp"] += 1
            else:
                metrics["fn"] += 1
            metrics["errors"].append({
                "case_id": cid,
                "gt": gt_dec,
                "pred": pr_dec,
                "free_text": p.get("free_text", "")[:200],
            })

        # confidence 匹配
        gt_conf = g.get("reasoning", {}).get("confidence", "")
        pr_conf = p.get("confidence", "")
        if gt_conf == pr_conf:
            metrics["confidence_match"] += 1

        # variable_weights 匹配 (Jaccard-style)
        gt_w = g.get("reasoning", {}).get("variable_weights", {})
        pr_w = p.get("variable_weights", {})
        if gt_w and pr_w:
            matched = sum(1 for k in gt_w if gt_w.get(k) == pr_w.get(k))
            total = len(gt_w)
            if total > 0:
                metrics["weight_match"] += matched / total

    # 计算汇总
    m = metrics
    acc = m["correct"] / m["total"] if m["total"] else 0
    precision = m["tp"] / (m["tp"] + m["fp"]) if (m["tp"] + m["fp"]) else 0
    recall = m["tp"] / (m["tp"] + m["fn"]) if (m["tp"] + m["fn"]) else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    print(f"\n{'='*60}")
    print(f"Task 1: 前列腺活检决策 ({m['total']} cases)")
    print(f"{'='*60}")
    print(f"\n  准确率 (Accuracy):  {acc:.4f}  ({m['correct']}/{m['total']})")
    print(f"  精确率 (Precision): {precision:.4f}")
    print(f"  召回率 (Recall):    {recall:.4f}")
    print(f"  F1 分数:            {f1:.4f}")
    print(f"\n  混淆矩阵:")
    print(f"                  Pred=yes  Pred=no")
    print(f"    Truth=yes      {m['tp']:6d}    {m['fn']:6d}")
    print(f"    Truth=no       {m['fp']:6d}    {m['tn']:6d}")
    print(f"\n  Confidence 匹配率: {m['confidence_match']}/{m['total']} ({m['confidence_match']/m['total']*100:.1f}%)")
    print(f"  Weight 平均匹配率:  {m['weight_match']/m['total']*100:.1f}%")

    if m["errors"]:
        print(f"\n  错误案例 ({len(m['errors'])} 个):")
        for e in m["errors"][:5]:
            print(f"    {e['case_id']}: gt={e['gt']} → pred={e['pred']}")
            print(f"      reasoning: {e['free_text'][:120]}...")
        if len(m["errors"]) > 5:
            print(f"    ... 还有 {len(m['errors'])-5} 个")

    return metrics


def eval_task2(gt: dict, pred: dict) -> dict:
    """Task 2: 四分类"""
    labels = ["active_surveillance", "continued_surveillance", "watchful_waiting", "active_treatment"]
    metrics = {"total": 0, "correct": 0, "errors": [], "confidence_match": 0, "weight_match": 0}
    confusion = defaultdict(lambda: defaultdict(int))

    for cid, g in gt.items():
        p = pred.get(cid)
        if not p:
            continue
        metrics["total"] += 1

        gt_dec = g.get("decision", "")
        pr_dec = p.get("treatment_recommendation", {}).get("primary", "")

        confusion[gt_dec][pr_dec] += 1
        if gt_dec == pr_dec:
            metrics["correct"] += 1
        else:
            metrics["errors"].append({
                "case_id": cid,
                "gt": gt_dec,
                "pred": pr_dec,
                "free_text": p.get("free_text", "")[:200],
            })

        gt_conf = g.get("reasoning", {}).get("confidence", "")
        pr_conf = p.get("confidence", "")
        if gt_conf == pr_conf:
            metrics["confidence_match"] += 1

        gt_w = g.get("reasoning", {}).get("variable_weights", {})
        pr_w = p.get("variable_weights", {})
        if gt_w and pr_w:
            matched = sum(1 for k in gt_w if gt_w.get(k) == pr_w.get(k))
            total = len(gt_w)
            if total > 0:
                metrics["weight_match"] += matched / total

    acc = metrics["correct"] / metrics["total"] if metrics["total"] else 0

    # 每类的 precision/recall
    print(f"\n{'='*60}")
    print(f"Task 2: 治疗决策 ({metrics['total']} cases)")
    print(f"{'='*60}")
    print(f"\n  准确率 (Accuracy): {acc:.4f}  ({metrics['correct']}/{metrics['total']})")

    print(f"\n  混淆矩阵:")
    print(f"{'':>25s}", end="")
    for l in labels:
        print(f"  {l[:12]:>12s}", end="")
    print()
    for gt_l in labels:
        print(f"  {gt_l[:23]:>25s}", end="")
        for pr_l in labels:
            v = confusion[gt_l][pr_l]
            v_str = str(v) if v else "."
            print(f"  {v_str:>12s}", end="")
        print()

    print(f"\n  各类指标:")
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(confusion[label][other] for other in labels if other != label)
        prec = tp / (tp + fp) if (tp + fp) else 0
        rec = tp / (tp + fn) if (tp + fn) else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
        print(f"    {label:25s}: P={prec:.3f} R={rec:.3f} F1={f1:.3f}  (n={tp+fn})")

    # macro F1
    f1s = []
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(confusion[label][other] for other in labels if other != label)
        prec = tp / (tp + fp) if (tp + fp) else 0
        rec = tp / (tp + fn) if (tp + fn) else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
        f1s.append(f1)
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0
    print(f"\n  Macro-F1: {macro_f1:.4f}")

    print(f"\n  Confidence 匹配率: {metrics['confidence_match']}/{metrics['total']} ({metrics['confidence_match']/metrics['total']*100:.1f}%)")
    print(f"  Weight 平均匹配率:  {metrics['weight_match']/metrics['total']*100:.1f}%")

    if metrics["errors"]:
        print(f"\n  错误案例 ({len(metrics['errors'])} 个):")
        for e in metrics["errors"][:5]:
            print(f"    {e['case_id']}: gt={e['gt']} → pred={e['pred']}")
            print(f"      reasoning: {e['free_text'][:120]}...")
        if len(metrics["errors"]) > 5:
            print(f"    ... 还有 {len(metrics['errors'])-5} 个")

    return metrics


def eval_task3(gt: dict, pred: dict) -> dict:
    """Task 3: 生存分析 (event + months)"""
    metrics = {"total": 0, "event_correct": 0, "errors": [],
               "months_mae": 0, "months_rmse": 0, "c_index": 0}

    pairs = []
    for cid, g in gt.items():
        p = pred.get(cid)
        if not p:
            continue
        metrics["total"] += 1

        gt_data = g.get("decision", {})
        pr_event = p.get("event", -1)
        pr_months = p.get("months_to_recurrence", -1)

        if isinstance(gt_data, dict):
            gt_event = gt_data.get("event", -1)
            gt_months = gt_data.get("months_to_recurrence", -1)
        else:
            continue

        pairs.append({
            "case_id": cid,
            "gt_event": gt_event,
            "gt_months": gt_months,
            "pr_event": pr_event,
            "pr_months": pr_months,
        })

        if gt_event == pr_event:
            metrics["event_correct"] += 1
        else:
            metrics["errors"].append({
                "case_id": cid,
                "gt_event": gt_event, "gt_months": gt_months,
                "pr_event": pr_event, "pr_months": pr_months,
                "free_text": p.get("free_text", "")[:200],
            })

    # 事件准确率
    event_acc = metrics["event_correct"] / metrics["total"] if metrics["total"] else 0

    # MAE & RMSE (仅对 event=1 的 case 计算月数误差)
    event1_pairs = [p for p in pairs if p["gt_event"] == 1 and p["pr_event"] == 1]
    if event1_pairs:
        diffs = [abs(p["gt_months"] - p["pr_months"]) for p in event1_pairs]
        metrics["months_mae"] = sum(diffs) / len(diffs)
        metrics["months_rmse"] = math.sqrt(sum(d ** 2 for d in diffs) / len(diffs))

    # C-index (简化版: 基于 concordance)
    # 对于所有 pair (i,j), 如果 gt_months_i < gt_months_j 且 event_i=1,
    # 检查 pr_months_i < pr_months_j
    concordant = 0
    permissible = 0
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            pi, pj = pairs[i], pairs[j]
            # 至少一个有事件
            if pi["gt_event"] == 1 or pj["gt_event"] == 1:
                # 确定哪个应该更短
                if pi["gt_months"] < pj["gt_months"] and pi["gt_event"] == 1:
                    permissible += 1
                    if pi["pr_months"] < pj["pr_months"]:
                        concordant += 1
                elif pj["gt_months"] < pi["gt_months"] and pj["gt_event"] == 1:
                    permissible += 1
                    if pj["pr_months"] < pi["pr_months"]:
                        concordant += 1
                elif pi["gt_months"] == pj["gt_months"]:
                    pass  # tie, skip
    metrics["c_index"] = concordant / permissible if permissible else 0

    print(f"\n{'='*60}")
    print(f"Task 3: 复发时间预测 ({metrics['total']} cases)")
    print(f"{'='*60}")
    print(f"\n  事件准确率 (Event Accuracy): {event_acc:.4f}  ({metrics['event_correct']}/{metrics['total']})")
    print(f"  C-index (一致性指数):        {metrics['c_index']:.4f}  ({concordant}/{permissible} concordant pairs)")

    # event 混淆
    tp = sum(1 for p in pairs if p["gt_event"] == 1 and p["pr_event"] == 1)
    fp = sum(1 for p in pairs if p["gt_event"] == 0 and p["pr_event"] == 1)
    tn = sum(1 for p in pairs if p["gt_event"] == 0 and p["pr_event"] == 0)
    fn = sum(1 for p in pairs if p["gt_event"] == 1 and p["pr_event"] == 0)

    print(f"\n  Event 混淆矩阵:")
    print(f"                  Pred=event  Pred=no-event")
    print(f"    Truth=event      {tp:6d}      {fn:6d}")
    print(f"    Truth=no-event   {fp:6d}      {tn:6d}")

    if event1_pairs:
        print(f"\n  月数误差 (仅 event=1 且预测正确的 case, n={len(event1_pairs)}):")
        print(f"    MAE:  {metrics['months_mae']:.2f} 月")
        print(f"    RMSE: {metrics['months_rmse']:.2f} 月")

    # 月数分布对比
    gt_months_all = [p["gt_months"] for p in pairs]
    pr_months_all = [p["pr_months"] for p in pairs]
    print(f"\n  月数分布对比:")
    print(f"    Ground truth:  min={min(gt_months_all):.1f}  median={sorted(gt_months_all)[len(gt_months_all)//2]:.1f}  max={max(gt_months_all):.1f}  mean={sum(gt_months_all)/len(gt_months_all):.1f}")
    print(f"    Prediction:    min={min(pr_months_all):.1f}  median={sorted(pr_months_all)[len(pr_months_all)//2]:.1f}  max={max(pr_months_all):.1f}  mean={sum(pr_months_all)/len(pr_months_all):.1f}")

    if metrics["errors"]:
        print(f"\n  事件预测错误 ({len(metrics['errors'])} 个):")
        for e in metrics["errors"][:5]:
            print(f"    {e['case_id']}: gt(event={e['gt_event']}, months={e['gt_months']}) → pred(event={e['pr_event']}, months={e['pr_months']})")
            print(f"      reasoning: {e['free_text'][:120]}...")
        if len(metrics["errors"]) > 5:
            print(f"    ... 还有 {len(metrics['errors'])-5} 个")

    return metrics


# ── 主入口 ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CHIMERA-agent 本地评估")
    parser.add_argument("--task", type=int, choices=[1, 2, 3], help="只评估指定 task")
    args = parser.parse_args()

    print("CHIMERA-agent 本地评估 (Prediction vs Ground Truth)")
    print(f"数据目录: {DATA}")
    print(f"输出目录: {OUT}")

    tasks = [args.task] if args.task else [1, 2, 3]
    all_metrics = {}

    for task in tasks:
        gt = load_ground_truth(task)
        pred = load_predictions(task)

        if not gt:
            print(f"\nTask {task}: 无 ground truth 数据")
            continue
        if not pred:
            print(f"\nTask {task}: 无预测数据")
            continue

        print(f"\nTask {task}: GT={len(gt)} cases, Pred={len(pred)} cases, 匹配={len(set(gt) & set(pred))} cases")

        if task == 1:
            all_metrics[task] = eval_task1(gt, pred)
        elif task == 2:
            all_metrics[task] = eval_task2(gt, pred)
        elif task == 3:
            all_metrics[task] = eval_task3(gt, pred)

    # 汇总
    print(f"\n{'='*60}")
    print("汇总")
    print(f"{'='*60}")
    for task in tasks:
        if task in all_metrics:
            m = all_metrics[task]
            acc = m.get("correct", m.get("event_correct", 0)) / m["total"] if m["total"] else 0
            label = {1: "活检决策准确率", 2: "治疗决策准确率", 3: "事件预测准确率"}[task]
            print(f"  Task {task} {label}: {acc:.4f}")
            if task == 3 and "c_index" in m:
                print(f"  Task 3 C-index: {m['c_index']:.4f}")


if __name__ == "__main__":
    main()
