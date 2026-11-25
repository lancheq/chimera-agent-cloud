#!/usr/bin/env python3
"""Official reasoning-component scores for both arms (T1/T2).

Decision accuracy is only one part of the platform score. ``evaluation/evaluate.py``
also computes confidence proximity, variable-weight agreement and the
important/decisive factor set-F1 -- the components that a decision-only benchmark
cannot see. This script imports the official implementations instead of
re-deriving them, and applies them to the outputs both arms actually produced.

T3 has no reasoning ground truth, so it is out of scope here.
"""

from __future__ import annotations

import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, "/home/hedongze/CHIMERA-agent/CHIMERA-agent/evaluation")

from evaluate import (confidence_score, important_decisive_factor_score,  # noqa: E402
                      variable_weight_score)

DATA = Path("/home/hedongze/CHIMERA-agent/data")
A_OUT = Path("/home/hedongze/CHIMERA-armA/output/bench_A_full")
C_OUT = Path("/home/hedongze/CHIMERA-armC/output/bench_work")
CASE_FILES = {1: "t1_full_91_gt_pids.json", 2: "t2_full_72_gt_pids.json"}
GT_FILES = {1: "prostate-biopsy-decision-reasoning.json",
            2: "prostate-treatment-decision-reasoning.json"}


def mean(values):
    vals = [v for v in values if v is not None]
    return round(statistics.fmean(vals), 4) if vals else None


def load(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def main() -> int:
    result = {}
    for task in (1, 2):
        cases = json.loads((Path("/home/hedongze/CHIMERA-armC/scripts") / CASE_FILES[task]).read_text())
        rows = {"A": [], "C": []}
        for case in cases:
            gt = load(DATA / f"task{task}" / "agent_input" / case / GT_FILES[task])
            if not gt:
                continue
            for arm, path in (("A", A_OUT / f"task{task}" / case / GT_FILES[task]),
                              ("C", C_OUT / f"task{task}" / case / "sockets" / GT_FILES[task])):
                pred = load(path)
                if not pred:
                    rows[arm].append((None, None, None))
                    continue
                rows[arm].append((confidence_score(gt, pred),
                                  variable_weight_score(gt, pred),
                                  important_decisive_factor_score(gt, pred)))
        entry = {}
        for arm in ("A", "C"):
            triples = rows[arm]
            entry[arm] = dict(cases=len(triples),
                              confidence=mean([t[0] for t in triples]),
                              variable_weights=mean([t[1] for t in triples]),
                              important_factors=mean([t[2] for t in triples]),
                              n_confidence=sum(1 for t in triples if t[0] is not None),
                              n_weights=sum(1 for t in triples if t[1] is not None))
        result[task] = entry
        print(f"task{task}:", json.dumps(entry, ensure_ascii=False))
    Path("/home/hedongze/CHIMERA-armC/output/bench/reasoning_components.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
