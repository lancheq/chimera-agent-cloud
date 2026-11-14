#!/usr/bin/env python3
"""Compare the preregistered night arms without selecting a new prompt/config."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path
from statistics import mean


def read_json(path: Path):
    return json.loads(path.read_text()) if path.is_file() else None


def paired_difference(left, right, ids):
    differences = [float(right[cid].get("case_score") or 0) - float(left[cid].get("case_score") or 0) for cid in ids]
    rng = random.Random(20261110)
    samples = sorted(mean(rng.choices(differences, k=len(differences))) for _ in range(4000))
    wins = losses = 0
    for cid in ids:
        a = str(left[cid].get("decision_correct", "")).lower() == "true"
        b = str(right[cid].get("decision_correct", "")).lower() == "true"
        wins += b and not a
        losses += a and not b
    discordant = wins + losses
    p = min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1)) / 2**discordant) if discordant else 1.0
    return {
        "cases": len(ids), "mean_official_case_score_delta": mean(differences),
        "exploratory_case_bootstrap_95pct": [samples[100], samples[3899]],
        "decision_fixed": wins, "decision_broken": losses,
        "mcnemar_exact_two_sided_unadjusted": p,
        "limitations": "Single seed and exposed cohort; case bootstrap does not certify patient independence; no multiplicity correction.",
    }


def summarize(run_dir: Path) -> dict:
    protocol = read_json(run_dir / "protocol.json")
    ids = protocol["case_ids"]
    arms = {}
    rows_by_arm = {}
    for name in protocol["arms"]:
        arm = run_dir / "arms" / name
        report = read_json(arm / "official_diagnostic/report.json")
        rows = {}
        csv_path = arm / "official_diagnostic/task1.csv"
        if csv_path.is_file():
            with csv_path.open() as handle:
                rows = {row["case_id"]: row for row in csv.DictReader(handle)}
        rows_by_arm[name] = rows
        elapsed, turns, tool_calls, usage = [], [], [], []
        raw_correct = raw_available = final_raw_changed = fallback_cases = budget_stops = 0
        tool_names = {}
        for cid in ids:
            trace = read_json(arm / "task1" / cid / "trace.json")
            if trace and trace.get("status") == "success":
                elapsed.append(trace.get("elapsed_seconds", 0))
                turns.append(trace.get("policy_model_turns", 0))
                events = trace.get("policy_trace", [])
                count = 0
                tokens = 0
                for event in events:
                    if event["stage"] == "plan":
                        names = event.get("executed_tools", [])
                    elif event["stage"] == "tools":
                        names = [r.get("name", "unknown") for r in event.get("results", [])]
                    else:
                        names = []
                    for tool_name in names:
                        tool_names[tool_name] = tool_names.get(tool_name, 0) + 1
                    count += len(names)
                    tokens += (event.get("usage") or {}).get("total_tokens", 0)
                    budget_stops += event.get("action") == "stop_for_budget"
                tool_calls.append(count)
                usage.append(tokens)
                warnings = trace.get("form_fill_warnings", [])
                fallback_cases += any("DEFAULTED" in warning or "salvaged" in warning for warning in warnings)
            pre = read_json(arm / "trace" / cid / "judgment_pre_calibration.json")
            pred = read_json(arm / "task1" / cid / "prediction.json")
            if pre:
                raw = pre.get("judgment", {}).get("biopsy_decision")
                if raw in ("yes", "no"):
                    raw_available += 1
                    gt = read_json(run_dir / "cohort/task1/agent_input" / cid / "prostate-biopsy-decision.json")
                    gt = gt if isinstance(gt, str) else gt.get("biopsy_decision")
                    raw_correct += raw == gt
                    if pred:
                        final_raw_changed += pred.get("biopsy_decision") != raw
        aggregates = report["aggregates"]["task1"] if report else None
        arms[name] = {
            "official_deterministic_diagnostic": aggregates,
            "diagnostics": report["diagnostics"]["task1"] if report else None,
            "successful_timing_records": len(elapsed), "mean_case_elapsed_seconds": mean(elapsed) if elapsed else None,
            "mean_agent_model_turns": mean(turns) if turns else None,
            "mean_executed_tools_including_plan": mean(tool_calls) if tool_calls else None,
            "mean_agent_only_tokens": mean(usage) if usage else None,
            "tool_counts": tool_names, "raw_judgments_available": raw_available,
            "raw_judgment_correct": raw_correct, "raw_accuracy_over_all_91": raw_correct/len(ids),
            "postprocessing_changed_raw_decision": final_raw_changed,
            "formfill_fallback_cases": fallback_cases, "budget_stops": budget_stops,
        }
    comparisons = {}
    for a, b in protocol["primary_comparisons"]:
        if set(rows_by_arm[a]) == set(ids) and set(rows_by_arm[b]) == set(ids):
            comparisons[f"{b} minus {a}"] = paired_difference(rows_by_arm[a], rows_by_arm[b], ids)
        else:
            comparisons[f"{b} minus {a}"] = {"status": "not_scored_all_targets"}
    return {
        "mode": "preregistered_legacy_cohort_screening", "arms": arms, "paired_comparisons": comparisons,
        "limitations": [
            "These are newly generated predictions, with rationale judge disabled in the official scoring diagnostic.",
            "No untouched test data, no OOF training, no independent generalization estimate.",
            "Agent-only token counts omit planner, form-fill, and fact-check LLM calls; elapsed time covers the full case.",
            "One seed, three preregistered comparisons; statistical outputs are exploratory.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.run_dir)
    (args.run_dir / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = ["# 第二轮夜间消融结果", "", "已接触的 T1 91 例；固定 seed；官方 judge-off 诊断分。不能作为独立测试结果。", "",
             "| arm | evaluated | accuracy | yes F1 | ranking diagnostic | sec/case | raw accuracy |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    def fmt(value):
        return "—" if value is None else f"{value:.4f}"
    for name, arm in report["arms"].items():
        aggregate = arm["official_deterministic_diagnostic"] or {}
        lines.append(f"| {name} | {aggregate.get('n_evaluated', '—')} | {fmt(aggregate.get('decision_accuracy'))} | "
                     f"{fmt(aggregate.get('decision_f1_yes'))} | {fmt(aggregate.get('ranking_score'))} | "
                     f"{fmt(arm['mean_case_elapsed_seconds'])} | {fmt(arm['raw_accuracy_over_all_91'])} |")
    lines.extend(["", "配对差值、修复/破坏病例数、工具计数及局限见 `comparison.json`。未评分组不能解释为零分。", ""])
    (args.run_dir / "SUMMARY.md").write_text("\n".join(lines))
    print("Saved", args.run_dir / "SUMMARY.md")


if __name__ == "__main__":
    main()
