#!/usr/bin/env python3
"""
gen_ablation_report.py — self_refine 消融报告生成

读取两组评估结果 + audit 结果，生成消融报告:
  - T1/T2/T3 decision correctness 对比
  - confidence 分布 (uncertain 比例)
  - 错误方向聚类
  - thinking 污染率 / refine 执行率 / 决策变更
  - 结论: 正贡献 / 负贡献 / 无显著

用法:
  python scripts/gen_ablation_report.py \
    --baseline-dir test/output.ablation_baseline \
    --selfrefine-dir test/output.ablation_selfrefine \
    --cases-json scripts/ablation_cases.json \
    --audit-json scripts/ablation_audit.json \
    --output scripts/ablation_report.md
"""

import argparse
import json
import os
import math
from collections import Counter, defaultdict
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")

GT_FILES = {
    1: {
        "decision": "prostate-biopsy-decision.json",
        "reasoning": "prostate-biopsy-decision-reasoning.json",
        "clinical_data": "prostate-biopsy-decision-clinical-data.json",
    },
    2: {
        "decision": "prostate-treatment-decision.json",
        "reasoning": "prostate-treatment-decision-reasoning.json",
        "clinical_data": "prostate-treatment-decision-clinical-data.json",
    },
    3: {
        "decision": "prostate-time-to-recurrence-or-last-follow-up.json",
        "reasoning": None,
        "clinical_data": "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
    },
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_json_safe(path):
    try:
        return load_json(path)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def get_gt_cases(task):
    gt_dir = os.path.join(DATA_DIR, f"task{task}", "agent_input")
    cases = []
    for case in sorted(os.listdir(gt_dir)):
        dec_file = os.path.join(gt_dir, case, GT_FILES[task]["decision"])
        if os.path.exists(dec_file):
            cases.append(case)
    return cases


def get_gt_decision(task, case):
    gt_dir = os.path.join(DATA_DIR, f"task{task}", "agent_input", case)
    dec_path = os.path.join(gt_dir, GT_FILES[task]["decision"])
    if not os.path.exists(dec_path):
        return None
    dec = load_json(dec_path)
    if task == 1:
        return dec  # "yes" or "no"
    elif task == 2:
        return dec  # "active_surveillance", etc.
    elif task == 3:
        return dec.get("event", None)  # 0 or 1


def get_pred_decision(task, pred):
    if pred is None:
        return None
    if task == 1:
        return pred.get("biopsy_decision")
    elif task == 2:
        tr = pred.get("treatment_recommendation", {})
        return tr.get("primary") if isinstance(tr, dict) else None
    elif task == 3:
        return pred.get("event")
    return None


def get_pred_confidence(pred):
    if pred is None:
        return None
    return pred.get("confidence", "MISSING")


def eval_config(pred_dir, cases_by_task):
    """Evaluate predictions for all sampled cases."""
    results = {}
    for task, case_ids in cases_by_task.items():
        task_results = {
            "total": len(case_ids),
            "found": 0,
            "correct": 0,
            "errors": [],
            "y_true": [],
            "y_pred": [],
            "confidence": Counter(),
            "decisions": Counter(),
        }

        for case_id in case_ids:
            pred_path = os.path.join(pred_dir, f"task{task}", case_id, "prediction.json")
            pred = load_json_safe(pred_path)
            gt_dec = get_gt_decision(task, case_id)

            if pred is None:
                task_results["errors"].append({
                    "case_id": case_id,
                    "error": "prediction not found",
                })
                continue

            task_results["found"] += 1
            pred_dec = get_pred_decision(task, pred)
            conf = get_pred_confidence(pred)

            task_results["y_true"].append(gt_dec)
            task_results["y_pred"].append(pred_dec)
            task_results["confidence"][conf] += 1
            task_results["decisions"][pred_dec] += 1

            if gt_dec == pred_dec:
                task_results["correct"] += 1
            else:
                task_results["errors"].append({
                    "case_id": case_id,
                    "gt": gt_dec,
                    "pred": pred_dec,
                    "confidence": conf,
                })

        # Compute accuracy
        n = task_results["found"]
        task_results["accuracy"] = task_results["correct"] / n if n > 0 else 0.0

        # Compute uncertain ratio
        total_conf = sum(task_results["confidence"].values())
        uncertain = task_results["confidence"].get("uncertain", 0)
        task_results["uncertain_ratio"] = uncertain / total_conf if total_conf > 0 else 0.0

        results[task] = task_results

    return results


def compute_f1(y_true, y_pred, labels=None):
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
    return macro_f1


def categorize_error(gt, pred, task):
    """Categorize error direction."""
    if task == 1:
        if gt == "no" and pred == "yes":
            return "false_positive"  # Over-treatment
        elif gt == "yes" and pred == "no":
            return "false_negative"  # Under-treatment
    elif task == 2:
        positive = {"active_treatment"}
        negative = {"watchful_waiting"}
        intermediate = {"active_surveillance", "continued_surveillance"}
        if gt in negative and pred in positive:
            return "over_treatment"
        elif gt in positive and pred in negative:
            return "under_treatment"
        elif gt in intermediate and pred in positive:
            return "false_escalation"
        elif gt in positive and pred in intermediate:
            return "false_de_escalation"
    elif task == 3:
        if gt == 0 and pred == 1:
            return "false_recurrence"
        elif gt == 1 and pred == 0:
            return "missed_recurrence"
    return "other"


def generate_report(baseline_results, selfrefine_results, audit_data, cases_data):
    lines = []
    lines.append("# self_refine 消融实验报告")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 实验设计: 30 case (T1:12 + T2:10 + T3:8) A/B 对比")
    lines.append(f"> 配置A (baseline): self_refine=false, atomic_check=false")
    lines.append(f"> 配置B (self_refine): self_refine=true, atomic_check=false")
    lines.append("")

    # ── 1. Decision Correctness ──
    lines.append("## 1. Decision Correctness 对比")
    lines.append("")
    lines.append("| Task | 指标 | Baseline | Self-refine | Δ |")
    lines.append("|------|------|----------|-------------|---|")

    for task in [1, 2, 3]:
        b = baseline_results[task]
        s = selfrefine_results[task]

        # Accuracy
        b_acc = b["accuracy"]
        s_acc = s["accuracy"]
        delta = s_acc - b_acc
        lines.append(f"| T{task} | Accuracy | {b_acc:.1%} ({b['correct']}/{b['found']}) | "
                      f"{s_acc:.1%} ({s['correct']}/{s['found']}) | {delta:+.1%} |")

        # Macro F1
        b_f1 = compute_f1(b["y_true"], b["y_pred"])
        s_f1 = compute_f1(s["y_true"], s["y_pred"])
        delta_f1 = s_f1 - b_f1
        lines.append(f"| T{task} | Macro F1 | {b_f1:.3f} | {s_f1:.3f} | {delta_f1:+.3f} |")

    lines.append("")

    # ── 2. Confidence Distribution ──
    lines.append("## 2. Confidence 分布对比")
    lines.append("")
    lines.append("| Task | 配置 | clear | borderline | uncertain | MISSING |")
    lines.append("|------|------|-------|------------|-----------|---------|")

    for task in [1, 2, 3]:
        for name, results in [("Baseline", baseline_results[task]), ("Self-refine", selfrefine_results[task])]:
            conf = results["confidence"]
            total = sum(conf.values())
            if total == 0:
                lines.append(f"| T{task} | {name} | - | - | - | - |")
            else:
                lines.append(f"| T{task} | {name} | "
                              f"{conf.get('clear', 0)} ({conf.get('clear', 0)/total:.0%}) | "
                              f"{conf.get('borderline', 0)} ({conf.get('borderline', 0)/total:.0%}) | "
                              f"{conf.get('uncertain', 0)} ({conf.get('uncertain', 0)/total:.0%}) | "
                              f"{conf.get('MISSING', 0)} |")

    lines.append("")
    lines.append(f"Uncertain 比例变化:")
    for task in [1, 2, 3]:
        b_unc = baseline_results[task]["uncertain_ratio"]
        s_unc = selfrefine_results[task]["uncertain_ratio"]
        lines.append(f"  - T{task}: {b_unc:.0%} → {s_unc:.0%} ({s_unc - b_unc:+.0%})")
    lines.append("")

    # ── 3. Error Direction Clustering ──
    lines.append("## 3. 错误方向聚类")
    lines.append("")

    for task in [1, 2, 3]:
        b = baseline_results[task]
        s = selfrefine_results[task]

        b_errors = [e for e in b["errors"] if "gt" in e]
        s_errors = [e for e in s["errors"] if "gt" in e]

        b_directions = Counter(categorize_error(e["gt"], e["pred"], task) for e in b_errors)
        s_directions = Counter(categorize_error(e["gt"], e["pred"], task) for e in s_errors)

        lines.append(f"### Task {task}")
        lines.append("")
        lines.append(f"| 错误方向 | Baseline | Self-refine |")
        lines.append(f"|----------|----------|-------------|")

        all_dirs = set(b_directions.keys()) | set(s_directions.keys())
        for d in sorted(all_dirs):
            lines.append(f"| {d} | {b_directions.get(d, 0)} | {s_directions.get(d, 0)} |")

        lines.append(f"| **Total** | **{len(b_errors)}** | **{len(s_errors)}** |")
        lines.append("")

        # Per-case error comparison
        lines.append(f"#### T{task} 错误 case 明细")
        lines.append("")
        b_err_cases = {e["case_id"]: e for e in b_errors}
        s_err_cases = {e["case_id"]: e for e in s_errors}
        all_err_cases = sorted(set(b_err_cases.keys()) | set(s_err_cases.keys()))

        if all_err_cases:
            lines.append("| Case | GT | Baseline Pred | SR Pred | Baseline Err? | SR Err? | 变化 |")
            lines.append("|------|-----|---------------|---------|---------------|---------|------|")
            for case_id in all_err_cases:
                gt = b_err_cases.get(case_id, s_err_cases.get(case_id, {})).get("gt", "?")
                b_pred = b_err_cases.get(case_id, {}).get("pred", "✓")
                s_pred = s_err_cases.get(case_id, {}).get("pred", "✓")
                b_err = "✗" if case_id in b_err_cases else "✓"
                s_err = "✗" if case_id in s_err_cases else "✓"

                if case_id in b_err_cases and case_id not in s_err_cases:
                    change = "🟢 修复"
                elif case_id not in b_err_cases and case_id in s_err_cases:
                    change = "🔴 新增错误"
                elif case_id in b_err_cases and case_id in s_err_cases:
                    if b_err_cases[case_id]["pred"] == s_err_cases[case_id]["pred"]:
                        change = "⬜ 未变"
                    else:
                        change = "🟡 改向"
                else:
                    change = ""

                lines.append(f"| {case_id} | {gt} | {b_pred} | {s_pred} | {b_err} | {s_err} | {change} |")
        else:
            lines.append("无错误 case")
        lines.append("")

    # ── 4. Audit: Thinking Contamination & Refine ──
    lines.append("## 4. Trace Audit 统计")
    lines.append("")

    if audit_data:
        stats = audit_data.get("stats", {})
        n = stats.get("total_cases", 0)

        # Thinking contamination
        tc = stats.get("thinking_contamination", {})
        lines.append("### 4.1 Thinking 污染率")
        lines.append("")
        lines.append(f"| 检查点 | 污染数 | 比例 |")
        lines.append(f"|--------|--------|------|")
        for key, label in [
            ("critique_contaminated", "Critique 输出"),
            ("baseline_agent_contaminated", "Baseline agent 输出"),
            ("selfrefine_agent_contaminated", "Self-refine agent 输出"),
            ("refined_contaminated", "Refined transcript"),
            ("baseline_plan_contaminated", "Baseline plan 输出"),
            ("selfrefine_plan_contaminated", "Self-refine plan 输出"),
        ]:
            count = tc.get(key, 0)
            lines.append(f"| {label} | {count}/{n} | {count/max(n,1):.0%} |")
        lines.append("")

        # Refine execution
        re_stats = stats.get("refine_execution", {})
        lines.append("### 4.2 Refine 执行率")
        lines.append("")
        lines.append(f"| 指标 | 数量 | 比例 |")
        lines.append(f"|------|------|------|")
        lines.append(f"| Refine 触发 (发现 issues) | {re_stats.get('refine_triggered', 0)}/{n} | "
                      f"{re_stats.get('refine_triggered', 0)/max(n,1):.0%} |")
        lines.append(f"| Critique 无 issues | {re_stats.get('no_issues', 0)}/{n} | "
                      f"{re_stats.get('no_issues', 0)/max(n,1):.0%} |")
        lines.append(f"| 无 critique 文件 | {re_stats.get('no_critique_file', 0)}/{n} | "
                      f"{re_stats.get('no_critique_file', 0)/max(n,1):.0%} |")
        lines.append(f"| 总 issues 发现数 | {re_stats.get('total_issues_found', 0)} | - |")
        lines.append("")

        # Decision changes
        dc = stats.get("decision_changes", {})
        lines.append("### 4.3 Refine 决策变更")
        lines.append("")
        n_completed = stats.get("completed_cases", n)
        lines.append(f"| 指标 | 数量 | 比例 |")
        lines.append(f"|------|------|------|")
        lines.append(f"| 决策变更 | {dc.get('changed', 0)}/{n_completed} | "
                      f"{dc.get('changed', 0)/max(n_completed,1):.0%} |")
        lines.append(f"| 决策不变 | {dc.get('unchanged', 0)}/{n_completed} | "
                      f"{dc.get('unchanged', 0)/max(n_completed,1):.0%} |")
        lines.append(f"| Confidence 变更 | {stats.get('confidence_changes', {}).get('changed', 0)}/{n_completed} | "
                      f"{stats.get('confidence_changes', {}).get('changed', 0)/max(n_completed,1):.0%} |")
        lines.append("")

        # Decision change details
        change_details = stats.get("decision_change_details", [])
        if change_details:
            lines.append("#### 决策变更 case 明细")
            lines.append("")
            lines.append("| Case | Task | Baseline | Self-refine | Baseline Conf | SR Conf | Refine触发 | Thinking污染 |")
            lines.append("|------|------|----------|-------------|---------------|---------|-----------|-------------|")
            for d in change_details:
                lines.append(f"| {d['case_id']} | T{d['task']} | {d['baseline']} | {d['selfrefine']} | "
                              f"{d['baseline_conf']} | {d['selfrefine_conf']} | "
                              f"{'✓' if d['refine_executed'] else '✗'} | "
                              f"{'⚠️' if d['thinking_contaminated'] else ''} |")
            lines.append("")

        # Failures
        failures = stats.get("failures", {})
        if failures.get("baseline", 0) or failures.get("selfrefine", 0):
            lines.append(f"### 失败 case")
            lines.append(f"- Baseline 失败: {failures.get('baseline', 0)}")
            lines.append(f"- Self-refine 失败: {failures.get('selfrefine', 0)}")
            lines.append("")

    # ── 5. 总结与结论 ──
    lines.append("## 5. 总结与结论")
    lines.append("")

    # Compute overall metrics
    total_b_correct = sum(baseline_results[t]["correct"] for t in [1, 2, 3])
    total_b_found = sum(baseline_results[t]["found"] for t in [1, 2, 3])
    total_s_correct = sum(selfrefine_results[t]["correct"] for t in [1, 2, 3])
    total_s_found = sum(selfrefine_results[t]["found"] for t in [1, 2, 3])

    b_acc = total_b_correct / total_b_found if total_b_found > 0 else 0
    s_acc = total_s_correct / total_s_found if total_s_found > 0 else 0
    delta_acc = s_acc - b_acc

    total_b_err = sum(len([e for e in baseline_results[t]["errors"] if "gt" in e]) for t in [1, 2, 3])
    total_s_err = sum(len([e for e in selfrefine_results[t]["errors"] if "gt" in e]) for t in [1, 2, 3])

    lines.append(f"### 指标汇总")
    lines.append(f"")
    lines.append(f"| 指标 | Baseline | Self-refine | 变化 |")
    lines.append(f"|------|----------|-------------|------|")
    lines.append(f"| 总体 Accuracy | {b_acc:.1%} ({total_b_correct}/{total_b_found}) | "
                  f"{s_acc:.1%} ({total_s_correct}/{total_s_found}) | {delta_acc:+.1%} |")
    lines.append(f"| 总错误数 | {total_b_err} | {total_s_err} | {total_s_err - total_b_err:+d} |")

    # Confidence summary
    b_conf = Counter()
    s_conf = Counter()
    for t in [1, 2, 3]:
        b_conf += baseline_results[t]["confidence"]
        s_conf += selfrefine_results[t]["confidence"]
    b_total = sum(b_conf.values())
    s_total = sum(s_conf.values())
    b_unc = b_conf.get("uncertain", 0) / b_total if b_total else 0
    s_unc = s_conf.get("uncertain", 0) / s_total if s_total else 0
    lines.append(f"| Uncertain 比例 | {b_unc:.0%} | {s_unc:.0%} | {s_unc - b_unc:+.0%} |")
    lines.append(f"")

    # Determine conclusion
    lines.append(f"### 结论")
    lines.append(f"")

    # Heuristic: positive if accuracy improved or errors decreased without accuracy loss
    # Negative if accuracy decreased or errors increased
    # No significant if changes are minimal (< 5%)
    threshold = 0.05

    if delta_acc > threshold:
        conclusion = "正贡献"
        evidence = f"Accuracy 提升 {delta_acc:+.1%}"
    elif delta_acc < -threshold:
        conclusion = "负贡献"
        evidence = f"Accuracy 下降 {delta_acc:+.1%}"
    elif total_s_err < total_b_err - 1:
        conclusion = "正贡献 (错误减少)"
        evidence = f"错误数从 {total_b_err} 降至 {total_s_err}"
    elif total_s_err > total_b_err + 1:
        conclusion = "负贡献 (错误增加)"
        evidence = f"错误数从 {total_b_err} 升至 {total_s_err}"
    else:
        conclusion = "无显著差异"
        evidence = f"Accuracy 变化 {delta_acc:+.1%}, 错误数变化 {total_s_err - total_b_err:+d}"

    lines.append(f"**self_refine 对系统性能的贡献: {conclusion}**")
    lines.append(f"")
    lines.append(f"依据: {evidence}")
    lines.append(f"")

    if audit_data:
        stats = audit_data.get("stats", {})
        re_stats = stats.get("refine_execution", {})
        tc = stats.get("thinking_contamination", {})
        n = stats.get("total_cases", 0)
        refine_rate = re_stats.get("refine_triggered", 0) / max(n, 1)
        contam_rate = tc.get("critique_contaminated", 0) / max(n, 1)

        lines.append(f"补充分析:")
        lines.append(f"- Refine 触发率: {refine_rate:.0%} — {'高频触发说明 critique 有效发现问题' if refine_rate > 0.3 else '低触发率可能说明 critique 过于保守'}")
        lines.append(f"- Thinking 污染率: {contam_rate:.0%} — {'污染较高，需优化 critique prompt' if contam_rate > 0.2 else '污染可控'}")

        dc = stats.get("decision_changes", {})
        if dc.get("changed", 0) > 0:
            lines.append(f"- 决策变更: {dc['changed']} case 发生决策改变 — refine 实际影响了输出")
        else:
            lines.append(f"- 决策变更: 无 — refine 未改变最终决策 (可能仅影响 free_text/weights)")

    lines.append("")
    lines.append("---")
    lines.append(f"> 注: 样本量 N=30, 统计显著性有限。结论应作为定性参考，不宜过度解读小幅差异。")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate ablation report")
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--selfrefine-dir", required=True)
    parser.add_argument("--cases-json", required=True)
    parser.add_argument("--audit-json", default=None)
    parser.add_argument("--output", default="scripts/ablation_report.md")
    args = parser.parse_args()

    baseline_dir = os.path.join(PROJECT_ROOT, args.baseline_dir)
    selfrefine_dir = os.path.join(PROJECT_ROOT, args.selfrefine_dir)

    # Load cases
    cases_data = load_json(args.cases_json)
    cases_by_task = {}
    for task_str, case_ids in cases_data.get("by_task", {}).items():
        task = int(task_str.replace("task", ""))
        cases_by_task[task] = case_ids

    # Evaluate both configs
    print("Evaluating baseline...")
    baseline_results = eval_config(baseline_dir, cases_by_task)
    for task in [1, 2, 3]:
        r = baseline_results[task]
        print(f"  T{task}: {r['correct']}/{r['found']} = {r['accuracy']:.1%}")

    print("Evaluating self_refine...")
    selfrefine_results = eval_config(selfrefine_dir, cases_by_task)
    for task in [1, 2, 3]:
        r = selfrefine_results[task]
        print(f"  T{task}: {r['correct']}/{r['found']} = {r['accuracy']:.1%}")

    # Load audit data
    audit_data = None
    if args.audit_json:
        audit_path = args.audit_json if os.path.isabs(args.audit_json) else os.path.join(PROJECT_ROOT, args.audit_json)
        audit_data = load_json_safe(audit_path)

    # Generate report
    report = generate_report(baseline_results, selfrefine_results, audit_data, cases_data)

    output_path = args.output if os.path.isabs(args.output) else os.path.join(PROJECT_ROOT, args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n[Report] {output_path}")


if __name__ == "__main__":
    main()
