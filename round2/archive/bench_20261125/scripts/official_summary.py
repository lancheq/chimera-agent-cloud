#!/usr/bin/env python3
"""Summarise the official evaluator's per-arm results and project the ranking score."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/home/hedongze/CHIMERA-armC/output/bench/gc/eval")


def _weighted_f1(report: str) -> float:
    """Pull the weighted-average F1 out of the sklearn classification report."""
    for line in report.splitlines():
        if line.strip().startswith("weighted avg"):
            parts = line.split()
            return round(float(parts[-2]), 4)
    return 0.0


def load(arm: str, task: int) -> dict:
    path = ROOT / f"arm{arm}" / f"task{task}" / "metrics.json"
    return json.loads(path.read_text())["aggregates"]


def main() -> int:
    table = {}
    for arm in ("A", "C"):
        for task in (1, 2, 3):
            agg = load(arm, task)
            row = dict(
                case_score=round(agg["final_DAG_score"], 4),
                gate_pass=round(agg.get("decision_gate_pass_rate") or 0, 4),
                case_score_passed=round(agg.get("mean_case_score_among_gate_passed") or 0, 4),
                decision_acc=round(agg.get("decision_accuracy") or 0, 4),
                exact_acc=round(agg.get("exact_decision_accuracy") or 0, 4),
                conf_kappa=round(agg.get("confidence_weighted_kappa") or 0, 4),
                weight_kappa=round(agg.get("variable_weight_weighted_kappa") or 0, 4),
                tool=round(agg.get("mean_tool_score") or 0, 4),
                grounding=round(agg.get("mean_section_grounding_score") or 0, 4),
            )
            if task == 1:
                row["f1"] = round(agg.get("decision_f1_yes") or 0, 4)
            elif task == 2:
                row["f1"] = round(agg.get("decision_macro_f1") or 0, 4)
                row["f1_weighted"] = _weighted_f1(agg.get("decision_classification_report") or "")
            else:
                row["c_index"] = round(agg.get("concordance_index") or 0, 4)
                row["event_acc"] = round(agg.get("recurrence_event_accuracy") or 0, 4)
                row["time_score"] = round(agg.get("mean_time_score") or 0, 4)
                row["mae_event1"] = round(agg.get("event1_time_mae_months") or 0, 2)
            table[f"{arm}{task}"] = row

    # Leaderboard shape: T1 and T2 = 50% mean case score + 50% F1; T3 = C-index.
    # S = 0.4*T1 + 0.4*T2 + 0.2*T3.
    for arm in ("A", "C"):
        t1 = 0.5 * table[f"{arm}1"]["case_score"] + 0.5 * table[f"{arm}1"]["f1"]
        t2 = 0.5 * table[f"{arm}2"]["case_score"] + 0.5 * table[f"{arm}2"].get("f1_weighted", table[f"{arm}2"]["f1"])
        t3 = table[f"{arm}3"]["c_index"]
        table[f"{arm}_ranking"] = dict(T1=round(t1, 4), T2=round(t2, 4), T3=round(t3, 4),
                                       S=round(0.4 * t1 + 0.4 * t2 + 0.2 * t3, 4),
                                       S_macro_f1_variant=round(0.4 * t1 + 0.4 * (
                                           0.5 * table[f"{arm}2"]["case_score"]
                                           + 0.5 * table[f"{arm}2"]["f1"]) + 0.2 * t3, 4))
    text = json.dumps(table, indent=2, ensure_ascii=False)
    (ROOT.parent / "OFFICIAL_SCORES.json").write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
