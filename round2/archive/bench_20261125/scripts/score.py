#!/usr/bin/env python3
"""Score both arms with one scorer, on one label set, for one task.

A arm writes ``<out>/task<N>/<case>/prostate-*-decision.json`` (official layout).
C arm writes flat sockets ``<out>/prostate-*-decision.json`` per case directory.
Both are read here so the comparison never depends on two different evaluators.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

DECISION = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}
CLINICAL = {
    1: "prostate-biopsy-decision-clinical-data.json",
    2: "prostate-treatment-decision-clinical-data.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
}


def load(path: Path):
    return json.loads(path.read_text())


def label(task: int, data_root: Path, case: str):
    return load(data_root / f"task{task}" / "agent_input" / case / DECISION[task])


def arm_a_prediction(task: int, out: Path, case: str):
    path = out / f"task{task}" / case / DECISION[task]
    return load(path) if path.is_file() else None


def arm_c_prediction(task: int, out: Path, case: str):
    for candidate in (out / f"task{task}" / case / "sockets" / DECISION[task],
                      out / f"task{task}" / case / DECISION[task]):
        if candidate.is_file():
            return load(candidate)
    return None


def normalize(task: int, value):
    if value is None:
        return None
    if task == 1:
        if isinstance(value, bool):
            return "yes" if value else "no"
        return str(value).strip().lower()
    if task == 2:
        return str(value).strip().lower()
    if isinstance(value, dict):
        return (int(value.get("event", 0)), float(value.get("months_to_recurrence", 0.0)))
    return None


def score(task: int, data_root: Path, cases: list[str], arms: dict[str, Path]) -> dict:
    rows, result = [], {}
    for arm, out in arms.items():
        hits, total, errors, months, months_event = 0, 0, [], [], []
        for case in cases:
            truth_raw = label(task, data_root, case)
            pred_raw = (arm_a_prediction if arm == "A" else arm_c_prediction)(task, out, case)
            truth, pred = normalize(task, truth_raw), normalize(task, pred_raw)
            ok = pred is not None and pred == truth
            if task == 3 and isinstance(truth, tuple) and isinstance(pred, tuple):
                # Event status is the primary label; time-to-event is scored
                # separately because a censored case still has a follow-up time.
                ok = pred[0] == truth[0]
                months.append(abs(pred[1] - truth[1]))
                if truth[0] == 1:
                    months_event.append(abs(pred[1] - truth[1]))
            total += 1
            hits += int(ok)
            if not ok:
                errors.append(dict(case=case, truth=truth, pred=pred))
            rows.append(dict(arm=arm, case=case, truth=truth, pred=pred, correct=ok))
        entry = dict(cases=total, correct=hits, accuracy=(hits / total if total else None), errors=errors[:10])
        if task == 3 and months:
            entry["mae_months_all"] = statistics.fmean(months)
            entry["median_abs_months_all"] = statistics.median(months)
            if months_event:
                entry["mae_months_event"] = statistics.fmean(months_event)
        result[arm] = entry
    return dict(task=task, rows=rows, summary=result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument("--data-root", default="/home/hedongze/CHIMERA-agent/data")
    parser.add_argument("--arm-a-out", default="/home/hedongze/CHIMERA-armA/output/bench")
    parser.add_argument("--arm-c-out", default="/home/hedongze/CHIMERA-armC/output/bench_work")
    parser.add_argument("--cases", nargs="+", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    result = score(args.task, Path(args.data_root), args.cases,
                   {"A": Path(args.arm_a_out), "C": Path(args.arm_c_out)})
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
