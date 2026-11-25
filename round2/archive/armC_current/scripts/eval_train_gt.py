#!/usr/bin/env python3
"""
P0-C: 本地 GT 评估脚本
对比 prediction.json 与 ground truth (train_release GT) 的字段级匹配。
不依赖 evaluate.py，直接 JSON 字段对比。

输出：
  - scripts/eval_train_gt_report.md
  - scripts/decision_errors.json
"""

import json
import os
import sys
import math
import argparse
from collections import Counter, defaultdict
from datetime import datetime

# ── 路径配置 ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

# Defaults — overridable via --pred-dir / --report-suffix
_DEFAULT_PRED_DIR = os.path.join(PROJECT_ROOT, "test", "output.gt_v3_20260823")
PRED_DIR = _DEFAULT_PRED_DIR
REPORT_PATH = os.path.join(PROJECT_ROOT, "scripts", "eval_train_gt_report.md")
ERRORS_PATH = os.path.join(PROJECT_ROOT, "scripts", "decision_errors.json")

# GT 文件名（每个 task 不同）
GT_FILES = {
    1: {
        "decision": "prostate-biopsy-decision.json",
        "reasoning": "prostate-biopsy-decision-reasoning.json",
    },
    2: {
        "decision": "prostate-treatment-decision.json",
        "reasoning": "prostate-treatment-decision-reasoning.json",
    },
    3: {
        "decision": "prostate-time-to-recurrence-or-last-follow-up.json",
        "reasoning": None,  # T3 没有 reasoning 文件
    },
}

# ── 工具函数 ──

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_gt_cases(task):
    """扫描 data/taskN/agent_input/ 找有 GT 的 case。"""
    gt_dir = os.path.join(DATA_DIR, f"task{task}", "agent_input")
    if not os.path.isdir(gt_dir):
        return []
    cases = []
    for case in sorted(os.listdir(gt_dir)):
        dec_file = os.path.join(gt_dir, case, GT_FILES[task]["decision"])
        if os.path.exists(dec_file):
            cases.append(case)
    return cases


def get_prediction(task, case):
    """读取 prediction.json。"""
    pred_path = os.path.join(PRED_DIR, f"task{task}", case, "prediction.json")
    if not os.path.exists(pred_path):
        return None
    return load_json(pred_path)


def get_gt(task, case):
    """读取 GT decision + reasoning。"""
    gt_dir = os.path.join(DATA_DIR, f"task{task}", "agent_input", case)
    result = {}
    dec_path = os.path.join(gt_dir, GT_FILES[task]["decision"])
    if os.path.exists(dec_path):
        result["decision"] = load_json(dec_path)
    rea_path = os.path.join(gt_dir, GT_FILES[task]["reasoning"]) if GT_FILES[task]["reasoning"] else None
    if rea_path and os.path.exists(rea_path):
        result["reasoning"] = load_json(rea_path)
    # T3 structured-prompt 也有 case facts 用于 GEval
    sp_path = os.path.join(gt_dir, "structured-prompt.json")
    if os.path.exists(sp_path):
        result["structured_prompt"] = load_json(sp_path)
    # T1/T2 clinical data
    if task == 1:
        cd_path = os.path.join(gt_dir, "prostate-biopsy-decision-clinical-data.json")
    elif task == 2:
        cd_path = os.path.join(gt_dir, "prostate-treatment-decision-clinical-data.json")
    elif task == 3:
        cd_path = os.path.join(gt_dir, "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json")
    else:
        cd_path = None
    if cd_path and os.path.exists(cd_path):
        result["clinical_data"] = load_json(cd_path)
    return result


# ── 评估指标 ──

def compute_f1(y_true, y_pred, labels=None):
    """计算 per-class F1 和 macro F1。"""
    if labels is None:
        labels = sorted(set(y_true) | set(y_pred))
    f1s = []
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1s.append(f1)
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    return macro_f1, dict(zip(labels, f1s))


def compute_weighted_kappa(gt_weights_list, pred_weights_list):
    """
    计算 variable_weights 的加权 kappa。
    weights 映射为序数: decisive=3, important=2, noted=1, not_used=0
    """
    WEIGHT_MAP = {"decisive": 3, "important": 2, "noted": 1, "not_used": 0}

    # 收集所有 (gt_val, pred_val) 对
    pairs = []
    for gt_w, pred_w in zip(gt_weights_list, pred_weights_list):
        if not isinstance(gt_w, dict) or not isinstance(pred_w, dict):
            continue
        all_keys = set(gt_w.keys()) | set(pred_w.keys())
        for k in all_keys:
            gv = gt_w.get(k, "not_used")
            pv = pred_w.get(k, "not_used")
            gi = WEIGHT_MAP.get(gv, 0)
            pi = WEIGHT_MAP.get(pv, 0)
            pairs.append((gi, pi))

    if not pairs:
        return 0.0

    n = len(pairs)
    categories = list(range(4))  # 0, 1, 2, 3

    # 构建混淆矩阵
    conf = [[0] * 4 for _ in range(4)]
    for gi, pi in pairs:
        conf[gi][pi] += 1

    # 观测一致
    po = sum(conf[i][i] for i in range(4)) / n

    # 期望一致
    row = [sum(conf[i]) for i in range(4)]
    col = [sum(conf[j][i] for j in range(4)) for i in range(4)]
    pe = sum(row[i] * col[i] for i in range(4)) / (n * n)

    if pe == 1.0:
        return 1.0

    # 加权 kappa (linear weights)
    w = [[abs(i - j) / 3.0 for j in range(4)] for i in range(4)]
    num = sum(w[i][j] * conf[i][j] for i in range(4) for j in range(4))
    den = sum(w[i][j] * row[i] * col[j] for i in range(4) for j in range(4)) / n

    if den == 0:
        return 0.0

    kappa = 1 - (num / den) if den != 0 else 0.0
    return kappa


def compute_cindex(gt_events, gt_months, pred_events, pred_months):
    """计算 C-index (concordance index)。"""
    n = len(gt_events)
    if n < 2:
        return float('nan')

    concordant = 0
    permissible = 0

    for i in range(n):
        for j in range(i + 1, n):
            # 比较可比较对
            ti, tj = gt_months[i], gt_months[j]
            ei, ej = gt_events[i], gt_events[j]

            # 确定 comparable pair
            if ei == 1 and ej == 1:
                if ti != tj:
                    permissible += 1
                    pi, pj = pred_months[i], pred_months[j]
                    if (ti < tj and pi < pj) or (ti > tj and pi > pj):
                        concordant += 1
                    elif pi == pj:
                        concordant += 0.5
            elif ei == 1 and ej == 0:
                permissible += 1
                pi, pj = pred_months[i], pred_months[j]
                if ti < tj:
                    if pi < pj:
                        concordant += 1
                    elif pi == pj:
                        concordant += 0.5
                elif ti > tj:
                    if pi > pj:
                        concordant += 1
                    elif pi == pj:
                        concordant += 0.5
            elif ei == 0 and ej == 1:
                permissible += 1
                pi, pj = pred_months[i], pred_months[j]
                if tj < ti:
                    if pj < pi:
                        concordant += 1
                    elif pj == pi:
                        concordant += 0.5
                elif tj > ti:
                    if pj > pi:
                        concordant += 1
                    elif pj == pi:
                        concordant += 0.5

    if permissible == 0:
        return float('nan')
    return concordant / permissible


# ── Task 评估 ──

def eval_task1():
    """Task1: 活检决策 (yes/no)。"""
    gt_cases = get_gt_cases(1)
    results = {
        "total_gt": len(gt_cases),
        "total_pred": 0,
        "decision_correct": 0,
        "decision_errors": [],
        "confidence_match": 0,
        "confidence_pred": Counter(),
        "confidence_gt": Counter(),
        "weights_kappa": 0.0,
        "reveal_nonempty": 0,
        "free_text_nonempty": 0,
        "y_true": [],
        "y_pred": [],
        "gt_weights_list": [],
        "pred_weights_list": [],
    }

    for case in gt_cases:
        pred = get_prediction(1, case)
        gt = get_gt(1, case)
        if pred is None:
            results["decision_errors"].append({
                "case_id": case,
                "error": "prediction.json not found",
            })
            continue
        results["total_pred"] += 1

        # Decision
        gt_dec = gt.get("decision", "")
        pred_dec = pred.get("biopsy_decision", "")
        results["y_true"].append(gt_dec)
        results["y_pred"].append(pred_dec)
        if gt_dec == pred_dec:
            results["decision_correct"] += 1
        else:
            results["decision_errors"].append({
                "case_id": case,
                "gt_decision": gt_dec,
                "pred_decision": pred_dec,
                "gt_reasoning": gt.get("reasoning", {}).get("free_text", ""),
                "pred_free_text": pred.get("free_text", ""),
            })

        # Confidence
        gt_reasoning = gt.get("reasoning", {})
        gt_conf = gt_reasoning.get("confidence", "MISSING")
        pred_conf = pred.get("confidence", "MISSING")
        results["confidence_gt"][gt_conf] += 1
        results["confidence_pred"][pred_conf] += 1
        if gt_conf == pred_conf:
            results["confidence_match"] += 1

        # Weights
        gt_w = gt_reasoning.get("variable_weights", {})
        pred_w = pred.get("variable_weights", {})
        results["gt_weights_list"].append(gt_w)
        results["pred_weights_list"].append(pred_w)

        # Reveal sequence
        reveal = pred.get("reveal_sequence", [])
        if reveal and len(reveal) > 0:
            results["reveal_nonempty"] += 1

        # Free text
        ft = pred.get("free_text", "")
        if ft and len(ft.strip()) > 0:
            results["free_text_nonempty"] += 1

    # Compute metrics
    macro_f1, per_class_f1 = compute_f1(results["y_true"], results["y_pred"], labels=["yes", "no"])
    results["macro_f1"] = macro_f1
    results["per_class_f1"] = per_class_f1
    results["weights_kappa"] = compute_weighted_kappa(results["gt_weights_list"], results["pred_weights_list"])

    return results


def eval_task2():
    """Task2: 治疗决策 (active_surveillance/active_treatment/continued_surveillance/watchful_waiting)。"""
    gt_cases = get_gt_cases(2)
    results = {
        "total_gt": len(gt_cases),
        "total_pred": 0,
        "decision_correct": 0,
        "decision_errors": [],
        "confidence_match": 0,
        "confidence_pred": Counter(),
        "confidence_gt": Counter(),
        "weights_kappa": 0.0,
        "reveal_nonempty": 0,
        "free_text_nonempty": 0,
        "y_true": [],
        "y_pred": [],
        "gt_weights_list": [],
        "pred_weights_list": [],
    }

    labels = ["active_surveillance", "active_treatment", "continued_surveillance", "watchful_waiting"]

    for case in gt_cases:
        pred = get_prediction(2, case)
        gt = get_gt(2, case)
        if pred is None:
            results["decision_errors"].append({
                "case_id": case,
                "error": "prediction.json not found",
            })
            continue
        results["total_pred"] += 1

        # Decision
        gt_dec = gt.get("decision", "")
        tr = pred.get("treatment_recommendation", {})
        pred_dec = tr.get("primary", "") if isinstance(tr, dict) else ""
        results["y_true"].append(gt_dec)
        results["y_pred"].append(pred_dec)
        if gt_dec == pred_dec:
            results["decision_correct"] += 1
        else:
            results["decision_errors"].append({
                "case_id": case,
                "gt_decision": gt_dec,
                "pred_decision": pred_dec,
                "gt_reasoning": gt.get("reasoning", {}).get("free_text", ""),
                "pred_free_text": pred.get("free_text", ""),
            })

        # Confidence
        gt_reasoning = gt.get("reasoning", {})
        gt_conf = gt_reasoning.get("confidence", "MISSING")
        pred_conf = pred.get("confidence", "MISSING")
        results["confidence_gt"][gt_conf] += 1
        results["confidence_pred"][pred_conf] += 1
        if gt_conf == pred_conf:
            results["confidence_match"] += 1

        # Weights
        gt_w = gt_reasoning.get("variable_weights", {})
        pred_w = pred.get("variable_weights", {})
        results["gt_weights_list"].append(gt_w)
        results["pred_weights_list"].append(pred_w)

        # Reveal sequence
        reveal = pred.get("reveal_sequence", [])
        if reveal and len(reveal) > 0:
            results["reveal_nonempty"] += 1

        # Free text
        ft = pred.get("free_text", "")
        if ft and len(ft.strip()) > 0:
            results["free_text_nonempty"] += 1

    macro_f1, per_class_f1 = compute_f1(results["y_true"], results["y_pred"], labels=labels)
    results["macro_f1"] = macro_f1
    results["per_class_f1"] = per_class_f1
    results["weights_kappa"] = compute_weighted_kappa(results["gt_weights_list"], results["pred_weights_list"])

    return results


def eval_task3():
    """Task3: 复发生存预测 (event + months)。仅评估有 prediction 的 case。"""
    gt_cases = get_gt_cases(3)
    results = {
        "total_gt": len(gt_cases),
        "total_pred": 0,
        "event_correct": 0,
        "decision_errors": [],
        "months_mae": 0.0,
        "cindex": float('nan'),
        "reveal_nonempty": 0,
        "free_text_nonempty": 0,
        "gt_events": [],
        "gt_months": [],
        "pred_events": [],
        "pred_months": [],
    }

    month_errors = []
    for case in gt_cases:
        pred = get_prediction(3, case)
        gt = get_gt(3, case)
        if pred is None:
            # T3 只跑了 6 case，大部分 GT case 无 prediction
            continue
        results["total_pred"] += 1

        gt_dec = gt.get("decision", {})
        gt_event = gt_dec.get("event", None)
        gt_months = gt_dec.get("months_to_recurrence", None)
        pred_event = pred.get("event", None)
        pred_months = pred.get("months_to_recurrence", None)

        if gt_event is not None and pred_event is not None:
            results["gt_events"].append(gt_event)
            results["pred_events"].append(pred_event)
            results["gt_months"].append(gt_months if gt_months is not None else 0)
            results["pred_months"].append(pred_months if pred_months is not None else 0)
            if gt_event == pred_event:
                results["event_correct"] += 1
            else:
                results["decision_errors"].append({
                    "case_id": case,
                    "gt_event": gt_event,
                    "pred_event": pred_event,
                    "gt_months": gt_months,
                    "pred_months": pred_months,
                })
            if gt_months is not None and pred_months is not None:
                month_errors.append(abs(gt_months - pred_months))

        # Reveal sequence
        reveal = pred.get("reveal_sequence", [])
        if reveal and len(reveal) > 0:
            results["reveal_nonempty"] += 1

        # Free text
        ft = pred.get("free_text", "")
        if ft and len(ft.strip()) > 0:
            results["free_text_nonempty"] += 1

    # C-index
    if len(results["gt_events"]) >= 2:
        results["cindex"] = compute_cindex(
            results["gt_events"], results["gt_months"],
            results["pred_events"], results["pred_months"]
        )

    # MAE
    if month_errors:
        results["months_mae"] = sum(month_errors) / len(month_errors)

    return results


# ── 报告生成 ──

def generate_report(t1, t2, t3):
    lines = []
    lines.append("# 本地 GT 评估报告 (eval_train_gt.py)")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 预测目录: {PRED_DIR}")
    lines.append(f"> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）")
    lines.append("")
    lines.append("⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，"
                 "分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。")
    lines.append("")

    # Task1
    lines.append(f"=== Task1 GT 评估（{t1['total_gt']} case）===")
    lines.append("")
    n = t1["total_pred"]
    if n == 0:
        lines.append("  无匹配 prediction，跳过")
    else:
        correct = t1["decision_correct"]
        lines.append(f"decision 正确率: {correct}/{n} = {correct/n*100:.1f}%")
        # per-class
        for label in ["yes", "no"]:
            tp = sum(1 for t, p in zip(t1["y_true"], t1["y_pred"]) if t == label and p == label)
            fp = sum(1 for t, p in zip(t1["y_true"], t1["y_pred"]) if t != label and p == label)
            fn = sum(1 for t, p in zip(t1["y_true"], t1["y_pred"]) if t == label and p != label)
            gt_count = sum(1 for t in t1["y_true"] if t == label)
            pred_count = sum(1 for p in t1["y_pred"] if p == label)
            lines.append(f"  - {label} 正确率: {tp}/{gt_count} (pred {pred_count})")
        lines.append(f"macro F1: {t1['macro_f1']:.3f}")
        lines.append(f"  - yes F1: {t1['per_class_f1'].get('yes', 0):.3f}")
        lines.append(f"  - no F1: {t1['per_class_f1'].get('no', 0):.3f}")
        lines.append("")
        cm = t1["confidence_match"]
        lines.append(f"confidence 匹配率: {cm}/{n} = {cm/n*100:.1f}%")
        lines.append(f"  - GT:  {dict(t1['confidence_gt'])}")
        lines.append(f"  - Pred: {dict(t1['confidence_pred'])}")
        lines.append(f"weights kappa: {t1['weights_kappa']:.3f}")
        rn = t1["reveal_nonempty"]
        lines.append(f"reveal 非空率: {rn}/{n} = {rn/n*100:.1f}%")
        fn = t1["free_text_nonempty"]
        lines.append(f"free_text 非空率: {fn}/{n} = {fn/n*100:.1f}%")
    lines.append("")

    # Task2
    lines.append(f"=== Task2 GT 评估（{t2['total_gt']} case）===")
    lines.append("")
    n = t2["total_pred"]
    if n == 0:
        lines.append("  无匹配 prediction，跳过")
    else:
        correct = t2["decision_correct"]
        lines.append(f"decision 正确率: {correct}/{n} = {correct/n*100:.1f}%")
        for label in ["active_surveillance", "active_treatment", "continued_surveillance", "watchful_waiting"]:
            tp = sum(1 for t, p in zip(t2["y_true"], t2["y_pred"]) if t == label and p == label)
            fp = sum(1 for t, p in zip(t2["y_true"], t2["y_pred"]) if t != label and p == label)
            fn = sum(1 for t, p in zip(t2["y_true"], t2["y_pred"]) if t == label and p != label)
            gt_count = sum(1 for t in t2["y_true"] if t == label)
            pred_count = sum(1 for p in t2["y_pred"] if p == label)
            lines.append(f"  - {label}: tp={tp}, gt={gt_count}, pred={pred_count}")
        lines.append(f"macro F1: {t2['macro_f1']:.3f}")
        for label in ["active_surveillance", "active_treatment", "continued_surveillance", "watchful_waiting"]:
            lines.append(f"  - {label} F1: {t2['per_class_f1'].get(label, 0):.3f}")
        lines.append("")
        cm = t2["confidence_match"]
        lines.append(f"confidence 匹配率: {cm}/{n} = {cm/n*100:.1f}%")
        lines.append(f"  - GT:  {dict(t2['confidence_gt'])}")
        lines.append(f"  - Pred: {dict(t2['confidence_pred'])}")
        lines.append(f"weights kappa: {t2['weights_kappa']:.3f}")
        rn = t2["reveal_nonempty"]
        lines.append(f"reveal 非空率: {rn}/{n} = {rn/n*100:.1f}%")
        fn = t2["free_text_nonempty"]
        lines.append(f"free_text 非空率: {fn}/{n} = {fn/n*100:.1f}%")
    lines.append("")

    # Task3
    lines.append(f"=== Task3 GT 评估（仅 {t3['total_pred']} case，全量 {t3['total_gt']} case 待重跑）===")
    lines.append("")
    n = t3["total_pred"]
    if n == 0:
        lines.append("  无匹配 prediction，跳过")
    else:
        ec = t3["event_correct"]
        lines.append(f"event 正确率: {ec}/{n} = {ec/n*100:.1f}%")
        lines.append(f"months MAE: {t3['months_mae']:.2f}")
        cidx = t3["cindex"]
        lines.append(f"C-index: {cidx:.3f}" if not math.isnan(cidx) else "C-index: N/A (样本不足)")
        rn = t3["reveal_nonempty"]
        lines.append(f"reveal 非空率: {rn}/{n} = {rn/n*100:.1f}%")
        fn = t3["free_text_nonempty"]
        lines.append(f"free_text 非空率: {fn}/{n} = {fn/n*100:.1f}%")
    lines.append("")
    lines.append("> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。")
    lines.append("")

    # 验收标准对比
    lines.append("=== 验收标准对比 ===")
    lines.append("")
    lines.append("| 指标 | 当前值 | 目标 | 状态 |")
    lines.append("|---|---|---|---|")
    t1_f1 = t1["macro_f1"] if t1["total_pred"] > 0 else 0
    lines.append(f"| task1 macro F1（91 case） | {t1_f1:.3f} | ≥0.794 | {'✅' if t1_f1 >= 0.794 else '❌'} |")
    t2_f1 = t2["macro_f1"] if t2["total_pred"] > 0 else 0
    lines.append(f"| task2 macro F1（72 case） | {t2_f1:.3f} | ≥0.35 | {'✅' if t2_f1 >= 0.35 else '❌'} |")
    t3_cidx = t3["cindex"] if not math.isnan(t3["cindex"]) else 0
    lines.append(f"| task3 C-index（仅 6 case） | {t3_cidx:.3f} | ≥0.82 | {'✅' if t3_cidx >= 0.82 else '❌'} |")
    lines.append("")

    return "\n".join(lines)


# ── 主函数 ──

def main():
    global PRED_DIR, REPORT_PATH, ERRORS_PATH

    parser = argparse.ArgumentParser(description="Local GT evaluation")
    parser.add_argument("--pred-dir", default=None,
                        help="Prediction directory (default: test/output.gt_v3_20260823)")
    parser.add_argument("--report-suffix", default="",
                        help="Suffix appended to report/errors filenames (e.g. _ablation_baseline)")
    args = parser.parse_args()

    if args.pred_dir:
        PRED_DIR = os.path.abspath(args.pred_dir)
    if args.report_suffix:
        REPORT_PATH = os.path.join(PROJECT_ROOT, "scripts", f"eval_train_gt_report{args.report_suffix}.md")
        ERRORS_PATH = os.path.join(PROJECT_ROOT, "scripts", f"decision_errors{args.report_suffix}.json")

    print("=" * 60)
    print("Agent 2 P0-C: 本地 GT 评估")
    print("=" * 60)
    print(f"预测目录: {PRED_DIR}")
    print(f"数据目录: {DATA_DIR}")
    print()

    # 环境核查
    print("[环境核查]")
    for task in [1, 2, 3]:
        gt_cases = get_gt_cases(task)
        pred_dir = os.path.join(PRED_DIR, f"task{task}")
        pred_cases = []
        if os.path.isdir(pred_dir):
            pred_cases = [c for c in os.listdir(pred_dir) if os.path.exists(os.path.join(pred_dir, c, "prediction.json"))]
        print(f"  Task{task}: GT={len(gt_cases)} case, Pred={len(pred_cases)} case")
    print()

    # 评估
    print("[评估 Task1...]")
    t1 = eval_task1()
    print(f"  decision 正确率: {t1['decision_correct']}/{t1['total_pred']} = {t1['decision_correct']/max(t1['total_pred'],1)*100:.1f}%")
    print(f"  macro F1: {t1['macro_f1']:.3f}")
    print()

    print("[评估 Task2...]")
    t2 = eval_task2()
    print(f"  decision 正确率: {t2['decision_correct']}/{t2['total_pred']} = {t2['decision_correct']/max(t2['total_pred'],1)*100:.1f}%")
    print(f"  macro F1: {t2['macro_f1']:.3f}")
    print()

    print("[评估 Task3...]")
    t3 = eval_task3()
    print(f"  event 正确率: {t3['event_correct']}/{t3['total_pred']}")
    print(f"  C-index: {t3['cindex']:.3f}" if not math.isnan(t3["cindex"]) else "  C-index: N/A")
    print()

    # 生成报告
    report = generate_report(t1, t2, t3)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[报告已写入] {REPORT_PATH}")

    # 生成 decision_errors.json
    all_errors = {
        "task1": t1["decision_errors"],
        "task2": t2["decision_errors"],
        "task3": t3["decision_errors"],
    }
    with open(ERRORS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_errors, f, ensure_ascii=False, indent=2)
    print(f"[错误 case 列表已写入] {ERRORS_PATH}")
    print(f"  Task1 错误: {len(t1['decision_errors'])} case")
    print(f"  Task2 错误: {len(t2['decision_errors'])} case")
    print(f"  Task3 错误: {len(t3['decision_errors'])} case")
    print()
    print("完成。")


if __name__ == "__main__":
    main()
