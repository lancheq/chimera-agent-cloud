#!/usr/bin/env python3
"""从 GT case 统计每个变量×任务下的权重频次分布，导出校准表。

输出 resources/weight_calibration.json，供 form_fill.py _calibrate_weights 使用。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "resources" / "weight_calibration.json"

REASONING_FILE = {
    1: "prostate-biopsy-decision-reasoning.json",
    2: "prostate-treatment-decision-reasoning.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-reasoning.json",
}

DECISION_FILE = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}


def main() -> None:
    # {task: {variable: {decision: Counter}}}
    stats: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(Counter)))
    # {task: {decision: Counter(confidence)}}  —— decision "_global" 汇总全量
    conf_stats: dict = defaultdict(lambda: defaultdict(Counter))

    for task in (1, 2, 3):
        task_dir = DATA / f"task{task}" / "agent_input"
        if not task_dir.exists():
            continue
        for case_dir in sorted(task_dir.iterdir()):
            if not case_dir.is_dir():
                continue
            reasoning_file = case_dir / REASONING_FILE[task]
            decision_file = case_dir / DECISION_FILE[task]
            if not reasoning_file.exists() or not decision_file.exists():
                continue
            reasoning = json.loads(reasoning_file.read_text())
            decision_raw = json.loads(decision_file.read_text())

            # Normalize decision to string key
            if task == 3:
                decision = "event" if decision_raw.get("event") == 1 else "no_event"
            else:
                decision = str(decision_raw).strip().strip('"').lower()

            # --- confidence 分布（task 内 + 该 task 内按 decision 分桶） ---
            conf = (reasoning.get("confidence") or "(none)")
            conf_stats[task]["_global"][conf] += 1
            conf_stats[task][decision][conf] += 1

            weights = reasoning.get("variable_weights", {})
            for var, level in weights.items():
                stats[task][var][decision][level] += 1

    # Convert to probability distributions
    calibration: dict = {}
    for task, vars in stats.items():
        calibration[str(task)] = {}
        for var, dec_dist in vars.items():
            calibration[str(task)][var] = {}
            # Aggregate across all decisions for "default"
            all_counts: Counter = Counter()
            for decision, counts in dec_dist.items():
                all_counts.update(counts)
            total_all = sum(all_counts.values())
            if total_all > 0:
                calibration[str(task)][var]["default"] = {
                    level: cnt / total_all for level, cnt in all_counts.items()
                }
            for decision, counts in dec_dist.items():
                total = sum(counts.values())
                if total > 0:
                    calibration[str(task)][var][decision] = {
                        level: cnt / total for level, cnt in counts.items()
                    }

    # confidence 分布（按 task + decision 分桶）
    confidence_calib: dict = {}
    for task, dec_buckets in conf_stats.items():
        confidence_calib[str(task)] = {}
        for dec_key, counts in dec_buckets.items():
            total = sum(counts.values())
            if total > 0:
                confidence_calib[str(task)][dec_key] = {
                    level: cnt / total for level, cnt in counts.items()
                }

    calibration["_confidence"] = confidence_calib

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(calibration, indent=2, ensure_ascii=False))

    # Print summary
    print(f"Wrote {OUTPUT}")
    for task in sorted(calibration):
        if task == "_confidence":
            continue
        print(f"\nTask {task}: {len(calibration[task])} variables")
        for var in sorted(calibration[task]):
            decs = calibration[task][var]
            default = decs.get("default", {})
            top = max(default, key=default.get) if default else "N/A"
            print(f"  {var}: default top={top} ({default.get(top, 0):.2f}), decisions={list(decs.keys())}")
    print("\nConfidence distribution (P(clear) per decision):")
    for task in sorted(confidence_calib):
        for dec_key, dist in sorted(confidence_calib[task].items()):
            p_clear = dist.get("clear", 0)
            print(f"  task{task} [{dec_key}]: clear={p_clear:.2f} "
                  f"borderline={dist.get('borderline', 0):.2f} uncertain={dist.get('uncertain', 0):.2f}")


if __name__ == "__main__":
    main()
