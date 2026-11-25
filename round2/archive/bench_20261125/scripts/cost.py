#!/usr/bin/env python3
"""Per-case cost table for both arms, from each arm's own exact instrument.

A arm: ``bench_cost`` JSONL written by the model client (one file per case).
C arm: the run's audit journal, which records every verified HTTP request and
the backend-reported usage. Both count real requests against the same endpoint.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

CASES = {1: ["PT-pseudo_48fb60dfd22f", "PT-pseudo_152344add0f1"], 2: ["T2-001", "T2-008"], 3: ["T3-034", "T3-021"]}


def arm_a(path: Path) -> dict:
    if not path.is_file():
        return {}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        return {}
    span = rows[-1]["ts"] - rows[0]["ts"]
    return dict(requests=len(rows),
                prompt_tokens=sum(r.get("prompt_tokens", 0) for r in rows),
                completion_tokens=sum(r.get("completion_tokens", 0) for r in rows),
                total_tokens=sum(r.get("total_tokens", 0) for r in rows),
                wall_seconds=round(span, 1))


def arm_c(work: Path) -> dict:
    events = sorted(glob.glob(str(work / "private" / "*" / "events.jsonl")))
    if not events:
        return {}
    rows = [json.loads(line) for line in Path(events[-1]).read_text().splitlines() if line.strip()]
    responses = [r for r in rows if r.get("kind") == "model_response"]
    usage = [(r.get("response") or {}).get("usage") or {} for r in responses]
    ledger = sorted(glob.glob(str(work / "private" / "*" / "temporary_evidence" / "output" / "task*" / "*"
                                      / "information_access_snapshot.json")))
    info = json.loads(Path(ledger[-1]).read_text()) if ledger else {}
    done = next((r for r in rows if r.get("kind") == "run_completed"), None)
    return dict(requests=len(responses),
                prompt_tokens=sum(u.get("prompt_tokens", 0) or 0 for u in usage),
                completion_tokens=sum(u.get("completion_tokens", 0) or 0 for u in usage),
                total_tokens=sum(u.get("total_tokens", 0) or 0 for u in usage),
                reserved_tokens=(done or {}).get("reserved_tokens"),
                info_units=info.get("spent_units"), info_attempts=info.get("admitted_attempts"),
                stages=sorted({r.get("stage") for r in responses}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm-a-root", default="/home/hedongze/CHIMERA-armA/output/bench")
    parser.add_argument("--arm-c-work", default="/home/hedongze/CHIMERA-armC/output/bench_work")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    table = []
    for task, cases in CASES.items():
        for case in cases:
            row = dict(task=task, case=case)
            row["A"] = arm_a(Path(args.arm_a_root) / f"A_cost_t{task}_{case}.jsonl")
            row["C"] = arm_c(Path(args.arm_c_work) / f"task{task}" / case)
            table.append(row)
    text = json.dumps(table, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
