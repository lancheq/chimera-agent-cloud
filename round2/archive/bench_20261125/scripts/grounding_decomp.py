#!/usr/bin/env python3
"""Attribute the section-grounding difference using the evaluator's own per-case output.

Reads ``per_case_results.csv`` (authoritative: the evaluator already decided which
variables were ungrounded) and joins it with each arm's reveal count, so the
difference can be assigned to coverage (how many sections were revealed) versus
weight set (how many variables were weighted at all).
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

GC = Path("/home/hedongze/CHIMERA-armC/output/bench/gc_v2")
CW = Path("/home/hedongze/CHIMERA-armC/output/bench_work")
AO = Path("/home/hedongze/CHIMERA-armA/output/bench_A_full")
REASONING = {1: "prostate-biopsy-decision-reasoning.json",
             2: "prostate-treatment-decision-reasoning.json"}
TASK_NAME = {1: "biopsy", 2: "treatment"}


def reveal_count(task: int, case: str, arm: str) -> int:
    base = (AO if arm == "A" else CW) / f"task{task}" / case
    if arm == "C":
        base = base / "sockets"
    try:
        rec = json.loads((base / REASONING[task]).read_text())
    except Exception:
        return 0
    return len(rec.get("reveal_sequence") or [])


def weighted_count(task: int, case: str, arm: str) -> int:
    base = (AO if arm == "A" else CW) / f"task{task}" / case
    if arm == "C":
        base = base / "sockets"
    try:
        rec = json.loads((base / REASONING[task]).read_text())
    except Exception:
        return 0
    weights = rec.get("variable_weights") or {}
    return sum(1 for v in weights.values() if str(v).lower() not in ("not_used", "none", ""))


def main() -> int:
    out = {}
    for task, name in TASK_NAME.items():
        out[task] = {}
        for arm in ("A", "C"):
            rows = [r for r in csv.DictReader((GC / f"eval/arm{arm}/per_case_results.csv").open())
                    if r["task"] == name]
            ground, revealed, weighted, ungrounded, zero_reveal = [], [], [], [], 0
            for row in rows:
                case = row["case_id"]
                value = row.get("section_grounding_score") or ""
                if value:
                    ground.append(float(value))
                rev = reveal_count(task, case, arm)
                revealed.append(rev)
                zero_reveal += int(rev == 0)
                weighted.append(weighted_count(task, case, arm))
                match = re.search(r"ungrounded_vars=\[([^\]]*)\]", row.get("reason") or "")
                ungrounded.append(len(re.findall(r"'([^']+)'", match.group(1))) if match else 0)
            n = len(rows)
            out[task][arm] = dict(
                cases=n,
                grounding=round(sum(ground) / n, 3),
                mean_revealed=round(sum(revealed) / n, 2),
                zero_reveal_cases=zero_reveal,
                mean_weighted_vars=round(sum(weighted) / n, 2),
                mean_ungrounded=round(sum(ungrounded) / n, 2),
                ungrounded_free_cases=sum(1 for u in ungrounded if u == 0),
            )
    text = json.dumps(out, indent=2, ensure_ascii=False)
    (GC / "GROUNDING_DECOMP.json").write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
