#!/usr/bin/env python3
"""
audit_traces.py — 消融实验 trace 审计

统计:
  1. thinking 污染率 — critique/plan/agent 输出中包含 thinking process 的比例
  2. refine 执行率 — critique 发现 issues 并触发 refine 的比例
  3. refine 决策变更 — baseline vs self_refine 的 prediction 决策是否不同
  4. confidence 变化 — baseline vs self_refine 的 confidence 分布对比

用法:
  python scripts/audit_traces.py \
    --baseline-dir test/output.ablation_baseline \
    --selfrefine-dir test/output.ablation_selfrefine \
    --output scripts/ablation_audit.json
"""

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Thinking contamination patterns (from self_refine.py _parse_critique_json)
THINKING_PATTERNS = [
    "here's a thinking process",
    "here is a thinking process",
    "analyze user input",
    "let's analyze the provided",
    "the user wants me to audit",
    "i need to check for:",
    "i'll analyze",
    "let me review",
    "<think>",
    "<thinking>",
    "okay, let me",
    "alright, i",
    "sure, i",
]


def load_json_safe(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_text_safe(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def check_thinking_contamination(text):
    """Check if text contains thinking process contamination."""
    if not text:
        return False
    text_lower = text.lower()[:500]
    return any(p in text_lower for p in THINKING_PATTERNS)


def get_pred_decision(task, pred):
    """Extract decision field from prediction."""
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


def audit_case(case_id, task, baseline_dir, selfrefine_dir):
    """Audit a single case across both runs."""
    result = {
        "case_id": case_id,
        "task": task,
    }

    # ── Predictions ──
    baseline_pred = load_json_safe(
        os.path.join(baseline_dir, f"task{task}", case_id, "prediction.json")
    )
    selfrefine_pred = load_json_safe(
        os.path.join(selfrefine_dir, f"task{task}", case_id, "prediction.json")
    )

    result["baseline_decision"] = get_pred_decision(task, baseline_pred)
    result["selfrefine_decision"] = get_pred_decision(task, selfrefine_pred)
    result["decision_changed"] = (
        result["baseline_decision"] != result["selfrefine_decision"]
        if result["baseline_decision"] is not None and result["selfrefine_decision"] is not None
        else None
    )

    result["baseline_confidence"] = get_pred_confidence(baseline_pred)
    result["selfrefine_confidence"] = get_pred_confidence(selfrefine_pred)
    result["confidence_changed"] = (
        result["baseline_confidence"] != result["selfrefine_confidence"]
        if result["baseline_confidence"] is not None and result["selfrefine_confidence"] is not None
        else None
    )

    # ── Trace audit (self_refine run only) ──
    sr_trace_dir = os.path.join(selfrefine_dir, "trace", case_id)
    baseline_trace_dir = os.path.join(baseline_dir, "trace", case_id)

    # Check for critique files (only in self_refine run)
    critique_files = []
    refine_executed = False
    thinking_contaminated = False
    critique_issues_count = 0

    for i in range(1, 5):  # up to 4 iterations
        critique_path = os.path.join(sr_trace_dir, f"critique_iter{i}.json")
        if not os.path.exists(critique_path):
            break
        critique_data = load_json_safe(critique_path) or {}
        critique_raw = critique_data.get("critique_raw_full", "")
        issues = critique_data.get("parsed_issues", [])

        critique_files.append({
            "iteration": i,
            "issues_count": len(issues),
            "severity": critique_data.get("severity_hint", ""),
            "contaminated": check_thinking_contamination(critique_raw),
        })

        if check_thinking_contamination(critique_raw):
            thinking_contaminated = True

        if issues:
            critique_issues_count += len(issues)
            refine_executed = True  # issues found → refine triggered

    result["critique_files"] = critique_files
    result["refine_executed"] = refine_executed
    result["thinking_contaminated"] = thinking_contaminated
    result["total_critique_issues"] = critique_issues_count

    # Check plan trace for thinking contamination (both runs)
    for run_name, trace_dir in [("baseline", baseline_trace_dir), ("selfrefine", sr_trace_dir)]:
        plan_full = read_text_safe(os.path.join(trace_dir, "plan_full.txt"))
        result[f"{run_name}_plan_contaminated"] = check_thinking_contamination(plan_full)

        # Also check trace.json messages
        trace_json = load_json_safe(os.path.join(
            baseline_dir if run_name == "baseline" else selfrefine_dir,
            f"task{task}", case_id, "trace.json"
        ))
        if trace_json and "messages" in trace_json:
            for msg in trace_json["messages"]:
                content = msg.get("content", "")
                if isinstance(content, str) and check_thinking_contamination(content):
                    result[f"{run_name}_agent_contaminated"] = True
                    break
            else:
                result.setdefault(f"{run_name}_agent_contaminated", False)
        else:
            result.setdefault(f"{run_name}_agent_contaminated", None)

    # Check refined_full.txt vs original transcript
    refined_full = read_text_safe(os.path.join(sr_trace_dir, "refined_full.txt"))
    result["refined_transcript_len"] = len(refined_full) if refined_full else 0
    result["refined_contaminated"] = check_thinking_contamination(refined_full)

    # Check reflect.json for warnings
    reflect_json = load_json_safe(os.path.join(sr_trace_dir, "reflect.json"))
    result["reflect_warnings"] = reflect_json.get("warnings", []) if reflect_json else []

    # Check for .failed markers
    result["baseline_failed"] = os.path.exists(
        os.path.join(baseline_dir, f"task{task}", case_id, ".failed")
    )
    result["selfrefine_failed"] = os.path.exists(
        os.path.join(selfrefine_dir, f"task{task}", case_id, ".failed")
    )

    return result


def main():
    parser = argparse.ArgumentParser(description="Audit ablation traces")
    parser.add_argument("--baseline-dir", required=True, help="Baseline output dir")
    parser.add_argument("--selfrefine-dir", required=True, help="Self-refine output dir")
    parser.add_argument("--cases-json", default=None,
                        help="ablation_cases.json for case list (default: auto-detect from pred dirs)")
    parser.add_argument("--output", default="scripts/ablation_audit.json",
                        help="Output JSON path")
    args = parser.parse_args()

    baseline_dir = os.path.join(PROJECT_ROOT, args.baseline_dir)
    selfrefine_dir = os.path.join(PROJECT_ROOT, args.selfrefine_dir)

    # Load case list
    if args.cases_json:
        cases_data = load_json_safe(args.cases_json)
        case_tasks = []
        for task_str, cases in cases_data.get("by_task", {}).items():
            task = int(task_str.replace("task", ""))
            for c in cases:
                case_tasks.append((c, task))
    else:
        # Auto-detect cases from prediction directories
        case_tasks = []
        for task in [1, 2, 3]:
            task_dir = os.path.join(selfrefine_dir, f"task{task}")
            if os.path.isdir(task_dir):
                for case in sorted(os.listdir(task_dir)):
                    if os.path.exists(os.path.join(task_dir, case, "prediction.json")):
                        case_tasks.append((case, task))

    print(f"Auditing {len(case_tasks)} cases...")
    print(f"  Baseline:   {baseline_dir}")
    print(f"  Self-refine: {selfrefine_dir}")
    print()

    all_results = []
    for case_id, task in case_tasks:
        r = audit_case(case_id, task, baseline_dir, selfrefine_dir)
        all_results.append(r)
        status_parts = []
        if r["refine_executed"]:
            status_parts.append(f"refine({r['total_critique_issues']} issues)")
        else:
            status_parts.append("no-refine")
        if r["thinking_contaminated"]:
            status_parts.append("THINKING!")
        if r["decision_changed"]:
            status_parts.append(f"DECISION CHANGED: {r['baseline_decision']}→{r['selfrefine_decision']}")
        if r["confidence_changed"]:
            status_parts.append(f"CONF CHANGED: {r['baseline_confidence']}→{r['selfrefine_confidence']}")
        if r["baseline_failed"] or r["selfrefine_failed"]:
            status_parts.append("FAILED")
        print(f"  T{task} {case_id:40s} {' | '.join(status_parts)}")

    # ── Aggregate statistics ──
    n = len(all_results)
    n_completed = sum(1 for r in all_results if not r["baseline_failed"] and not r["selfrefine_failed"])

    stats = {
        "timestamp": datetime.now().isoformat(),
        "total_cases": n,
        "completed_cases": n_completed,
        "thinking_contamination": {
            "critique_contaminated": sum(1 for r in all_results if r["thinking_contaminated"]),
            "baseline_plan_contaminated": sum(1 for r in all_results if r.get("baseline_plan_contaminated")),
            "selfrefine_plan_contaminated": sum(1 for r in all_results if r.get("selfrefine_plan_contaminated")),
            "baseline_agent_contaminated": sum(1 for r in all_results if r.get("baseline_agent_contaminated")),
            "selfrefine_agent_contaminated": sum(1 for r in all_results if r.get("selfrefine_agent_contaminated")),
            "refined_contaminated": sum(1 for r in all_results if r.get("refined_contaminated")),
        },
        "refine_execution": {
            "refine_triggered": sum(1 for r in all_results if r["refine_executed"]),
            "no_issues": sum(1 for r in all_results if not r["refine_executed"] and r["critique_files"]),
            "no_critique_file": sum(1 for r in all_results if not r["critique_files"]),
            "total_issues_found": sum(r["total_critique_issues"] for r in all_results),
        },
        "decision_changes": {
            "changed": sum(1 for r in all_results if r["decision_changed"] is True),
            "unchanged": sum(1 for r in all_results if r["decision_changed"] is False),
            "unknown": sum(1 for r in all_results if r["decision_changed"] is None),
        },
        "confidence_changes": {
            "changed": sum(1 for r in all_results if r["confidence_changed"] is True),
            "unchanged": sum(1 for r in all_results if r["confidence_changed"] is False),
        },
        "failures": {
            "baseline": sum(1 for r in all_results if r["baseline_failed"]),
            "selfrefine": sum(1 for r in all_results if r["selfrefine_failed"]),
        },
        "confidence_distribution": {
            "baseline": dict(Counter(r["baseline_confidence"] for r in all_results)),
            "selfrefine": dict(Counter(r["selfrefine_confidence"] for r in all_results)),
        },
    }

    # Decision change details
    changed_cases = [r for r in all_results if r["decision_changed"] is True]
    stats["decision_change_details"] = [
        {
            "case_id": r["case_id"],
            "task": r["task"],
            "baseline": r["baseline_decision"],
            "selfrefine": r["selfrefine_decision"],
            "baseline_conf": r["baseline_confidence"],
            "selfrefine_conf": r["selfrefine_confidence"],
            "refine_executed": r["refine_executed"],
            "thinking_contaminated": r["thinking_contaminated"],
        }
        for r in changed_cases
    ]

    output = {
        "stats": stats,
        "per_case": all_results,
    }

    output_path = os.path.join(PROJECT_ROOT, args.output) if not os.path.isabs(args.output) else args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print("Audit Summary")
    print(f"{'='*60}")
    print(f"Total cases: {n} (completed: {n_completed})")
    print(f"\nThinking contamination:")
    print(f"  Critique contaminated: {stats['thinking_contamination']['critique_contaminated']}/{n}")
    print(f"  Baseline agent:       {stats['thinking_contamination']['baseline_agent_contaminated']}/{n}")
    print(f"  Self-refine agent:    {stats['thinking_contamination']['selfrefine_agent_contaminated']}/{n}")
    print(f"  Refined transcript:   {stats['thinking_contamination']['refined_contaminated']}/{n}")
    print(f"\nRefine execution:")
    print(f"  Triggered: {stats['refine_execution']['refine_triggered']}/{n}")
    print(f"  No issues: {stats['refine_execution']['no_issues']}/{n}")
    print(f"  No critique file: {stats['refine_execution']['no_critique_file']}/{n}")
    print(f"  Total issues found: {stats['refine_execution']['total_issues_found']}")
    print(f"\nDecision changes: {stats['decision_changes']['changed']}/{n_completed}")
    print(f"Confidence changes: {stats['confidence_changes']['changed']}/{n_completed}")
    print(f"\nConfidence distribution:")
    print(f"  Baseline:    {stats['confidence_distribution']['baseline']}")
    print(f"  Self-refine: {stats['confidence_distribution']['selfrefine']}")
    print(f"\n[Output] {output_path}")


if __name__ == "__main__":
    main()
