#!/usr/bin/env python3
"""Standalone predictor evaluation on the same 236 labelled cases as the LLM arms.

Two numbers matter and they are not the same thing:

* ``insample`` -- the deployed joblib applied to the cases it was trained on. This is
  what the submission hardware would actually output, but it is optimistic because
  the head has seen these labels.
* ``loocv``    -- leave-one-out refit, which is the honest generalisation estimate
  and the only fair number to put next to a zero-shot LLM arm.

This script produces the first; ``scripts/train_predictor.py --loocv-retest``
produces the second.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, "/home/hedongze/CHIMERA-armC/src")

import numpy as np  # noqa: E402

from chimera_agent_baseline.predictor import Predictor  # noqa: E402

DATA = Path("/home/hedongze/CHIMERA-agent/data")
MODEL_DIR = Path("/home/hedongze/CHIMERA-agent/model/predictor")
CASE_FILES = {1: "t1_full_91_gt_pids.json", 2: "t2_full_72_gt_pids.json", 3: "t3_full_73_gt_pids.json"}
DECISION = {1: "prostate-biopsy-decision.json", 2: "prostate-treatment-decision.json",
            3: "prostate-time-to-recurrence-or-last-follow-up.json"}


def norm(task, value):
    if value is None:
        return None
    if task == 1:
        if isinstance(value, bool):
            return "yes" if value else "no"
        return str(value).strip().lower()
    if task == 2:
        return str(value).strip().lower()
    if isinstance(value, dict):
        return (int(value.get("event", 0)), float(value.get("months_to_recurrence") or 0.0))
    return None


def c_index(times, events, scores):
    """Harrell's C over comparable pairs."""
    conc = disc = 0
    for i in range(len(times)):
        for j in range(i + 1, len(times)):
            if events[i] == 1 and events[j] == 1:
                if times[i] == times[j]:
                    continue
                hi = times[i] < times[j]
                if scores[i] > scores[j]:
                    conc += 1 if hi else 0
                    disc += 0 if hi else 1
                elif scores[i] < scores[j]:
                    conc += 0 if hi else 1
                    disc += 1 if hi else 0
    return conc / (conc + disc) if conc + disc else float("nan")


def main() -> int:
    predictor = Predictor(MODEL_DIR)
    print("可用任务:", predictor.available_tasks)
    out = {}
    for task in (1, 2, 3):
        cases = json.loads((Path("/home/hedongze/CHIMERA-armC/scripts") / CASE_FILES[task]).read_text())
        rows, errors = [], []
        for case in cases:
            case_dir = DATA / f"task{task}" / "agent_input" / case
            truth = norm(task, json.loads((case_dir / DECISION[task]).read_text()))
            try:
                pred_raw = predictor.predict(case_dir, task)
            except Exception as exc:  # noqa: BLE001
                errors.append((case, repr(exc)[:120]))
                continue
            if task == 1:
                pred = "yes" if pred_raw.get("decision") in (True, "yes", 1) else "no"
            elif task == 2:
                pred = str(pred_raw.get("decision", "")).strip().lower()
            else:
                pred = (int(pred_raw.get("event", 0)), float(pred_raw.get("months_to_recurrence") or 0.0))
            rows.append((case, truth, pred, pred_raw))
        n = len(rows)
        if task == 3:
            times = [t[1] for _, t, _, _ in rows]
            events = [t[0] for _, t, _, _ in rows]
            risk = [(r["p_event"] if r.get("p_event") is not None else 0.0) for _, _, _, r in rows]
            hits = sum(1 for _, t, p, _ in rows if p[0] == t[0])
            mae = float(np.mean([abs(p[1] - t[1]) for _, t, p, _ in rows]))
            out[task] = dict(cases=n, event_acc=round(hits / n, 4), c_index=round(c_index(times, events, risk), 4),
                             mae_months=round(mae, 2),
                             pred_events=dict(Counter(p[0] for _, _, p, _ in rows)),
                             errors=errors)
        else:
            hits = sum(1 for _, t, p, _ in rows if p == t)
            out[task] = dict(cases=n, accuracy=round(hits / n, 4), errors=errors,
                             pred_dist=dict(Counter(p for _, _, p, _ in rows)))
        print(task, json.dumps({k: v for k, v in out[task].items() if k != "errors"}, ensure_ascii=False),
              "错误", len(errors))
    Path("/home/hedongze/CHIMERA-armC/output/bench/predictor_insample.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
