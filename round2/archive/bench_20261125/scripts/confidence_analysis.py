#!/usr/bin/env python3
"""Where does the confidence signal die?

Joins three sources per case: the expert level, the agent level, and whether the
decision was right. Then asks, in order:

1. Are the two label sets even of comparable spread (marginals)?
2. Given the spread, is the pairing informative (weighted kappa, confusion)?
3. Does the agent's level predict its own correctness at all?
4. Is the level tied to how much evidence was gathered, or to nothing?

No tuning and no recalibration: this only measures.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
import statistics

DATA = Path("/home/hedongze/CHIMERA-agent/data")
GC = Path("/home/hedongze/CHIMERA-armC/output/bench/gc_v2")
ARMS = {"A": Path("/home/hedongze/CHIMERA-armA/output/bench_A_full"),
        "C": Path("/home/hedongze/CHIMERA-armC/output/bench_work")}
LEVELS = {"clear": 2, "borderline": 1, "uncertain": 0}
REASONING = {1: "prostate-biopsy-decision-reasoning.json",
             2: "prostate-treatment-decision-reasoning.json"}
TASKS = {1: "biopsy", 2: "treatment"}


def weighted_kappa(pairs: list[tuple[int, int]]) -> float:
    """Linear weighted kappa over ordinal levels 0..2, as the evaluator reports it."""
    if not pairs:
        return float("nan")
    k = 3
    n = len(pairs)
    obs = [[0] * k for _ in range(k)]
    for a, b in pairs:
        obs[a][b] += 1
    row = [sum(obs[i]) for i in range(k)]
    col = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    w = [[abs(i - j) / (k - 1) for j in range(k)] for i in range(k)]
    num = sum(w[i][j] * obs[i][j] for i, j in product(range(k), repeat=2)) / n
    exp = sum(w[i][j] * row[i] * col[j] for i, j in product(range(k), repeat=2)) / (n * n)
    return 1 - num / exp if exp else float("nan")


def load_level(path: Path):
    try:
        value = json.loads(path.read_text()).get("confidence")
    except Exception:
        return None
    return LEVELS.get(str(value).strip().lower())


def main() -> int:
    report = {}
    for task, task_name in TASKS.items():
        rows = list(csv.DictReader((GC / "eval/armC/per_case_results.csv").open()))
        rows = [r for r in rows if r["task"] == task_name]
        report[task] = {}
        for arm in ("A", "C"):
            correct = {r["case_id"]: (r["decision_correct"] == "True", r) for r in rows}
            pairs, gt_hist, pred_hist = [], Counter(), Counter()
            by_pred, by_gt = defaultdict(list), defaultdict(list)
            revealed, ungrounded = defaultdict(list), []
            for case, (ok, row) in correct.items():
                gt = load_level(DATA / f"task{task}" / "agent_input" / case / REASONING[task])
                pred_path = ARMS[arm] / f"task{task}" / case / REASONING[task]
                if arm == "C":
                    pred_path = ARMS[arm] / f"task{task}" / case / "sockets" / REASONING[task]
                pred_level = load_level(pred_path)
                if gt is None or pred_level is None:
                    continue
                pairs.append((gt, pred_level))
                gt_hist[gt] += 1
                pred_hist[pred_level] += 1
                by_pred[pred_level].append(ok)
                by_gt[gt].append(ok)
                try:
                    seq = json.loads(pred_path.read_text()).get("reveal_sequence") or []
                except Exception:
                    seq = []
                revealed[pred_level].append(len(seq))
                if "ungrounded" in (row.get("reason") or ""):
                    ungrounded.append(ok)
            inv = {v: k for k, v in LEVELS.items()}
            entry = dict(
                n=len(pairs),
                gt_hist={inv[k]: v for k, v in sorted(gt_hist.items(), reverse=True)},
                pred_hist={inv[k]: v for k, v in sorted(pred_hist.items(), reverse=True)},
                kappa=round(weighted_kappa(pairs), 4),
                acc_by_pred={inv[k]: dict(n=len(v), acc=round(sum(v) / len(v), 4))
                             for k, v in sorted(by_pred.items(), reverse=True)},
                acc_by_gt={inv[k]: dict(n=len(v), acc=round(sum(v) / len(v), 4))
                           for k, v in sorted(by_gt.items(), reverse=True)},
                reveal_by_pred={inv[k]: round(statistics.fmean(v), 2) if v else None
                                for k, v in sorted(revealed.items(), reverse=True)},
                same_level_share=round(sum(1 for a, b in pairs if a == b) / len(pairs), 4),
                off_by_one_share=round(sum(1 for a, b in pairs if abs(a - b) == 1) / len(pairs), 4),
                off_by_two_share=round(sum(1 for a, b in pairs if abs(a - b) == 2) / len(pairs), 4),
            )
            report[task][arm] = entry
    text = json.dumps(report, indent=2, ensure_ascii=False)
    (GC / "CONFIDENCE_ANALYSIS.json").write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
