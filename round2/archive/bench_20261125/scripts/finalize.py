#!/usr/bin/env python3
"""Finalizer: waits for the paired run, then computes every deliverable.

Runs detached alongside the driver. When the driver writes ``run_end`` (or dies),
this collects both arms' outputs, scores them with one scorer, builds the per-case
CSV and a JSON summary, and leaves the result in ``output/bench/FINAL_RESULTS``.
"""

from __future__ import annotations

import csv
import glob
import json
import os
from pathlib import Path
import statistics
import time

ARM_A = Path("/home/hedongze/CHIMERA-armA")
ARM_C = Path("/home/hedongze/CHIMERA-armC")
BENCH = ARM_C / "output/bench"
A_OUT = ARM_A / "output/bench_A_full"
C_OUT = ARM_C / "output/bench_work"
DATA = Path("/home/hedongze/CHIMERA-agent/data")
CASE_FILES = {1: "t1_full_91_gt_pids.json", 2: "t2_full_72_gt_pids.json", 3: "t3_full_73_gt_pids.json"}
DECISION = {1: "prostate-biopsy-decision.json", 2: "prostate-treatment-decision.json",
            3: "prostate-time-to-recurrence-or-last-follow-up.json"}


def log(kind, **fields):
    with (BENCH / "status.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(ts=time.time(), kind=kind, **fields), ensure_ascii=False) + "\n")


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def cases(task):
    return json.loads((ARM_C / "scripts" / CASE_FILES[task]).read_text())


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
        return (int(value.get("event", 0)), round(float(value.get("months_to_recurrence", 0.0)), 1))
    return None



def hit(pred, truth, task):
    """Task 3 is scored on the event label; time-to-event is scored separately.

    Comparing the full (event, months) tuple would make a 1-month error count as a
    complete miss, which is not what the challenge evaluates.
    """
    if pred is None or truth is None:
        return False
    if task == 3 and isinstance(pred, tuple) and isinstance(truth, tuple):
        return pred[0] == truth[0]
    return pred == truth


def a_cost(task):
    """Per-case token usage from the instrumented client log, split on markers."""
    path = BENCH / f"A_cost_task{task}.jsonl"
    out, current = {}, None
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") == "case_start":
            current = row.get("case")
            out.setdefault(current, dict(requests=0, total_tokens=0))
        elif row.get("kind") == "model_response" and current:
            out[current]["requests"] += 1
            out[current]["total_tokens"] += row.get("total_tokens", 0) or 0
    return out


def c_cost(task, case):
    work = C_OUT / f"task{task}" / case
    events = sorted(glob.glob(str(work / "private" / "*" / "events.jsonl")))
    if not events:
        return dict(requests=None, total_tokens=None, info_units=None)
    rows = [json.loads(x) for x in Path(events[-1]).read_text().splitlines() if x.strip()]
    responses = [r for r in rows if r.get("kind") == "model_response"]
    usage = [(r.get("response") or {}).get("usage") or {} for r in responses]
    ledger = sorted(glob.glob(str(work / "private" / "*" / "temporary_evidence" / "output" / "task*" / "*"
                                      / "information_access_snapshot.json")))
    info = read(ledger[-1]) if ledger else {}
    return dict(requests=len(responses),
                total_tokens=sum(u.get("total_tokens", 0) or 0 for u in usage),
                info_units=info.get("spent_units"))


def build(task):
    rows = []
    a_use = a_cost(task)
    for case in cases(task):
        truth = norm(task, read(DATA / f"task{task}" / "agent_input" / case / DECISION[task]))
        a_raw = read(A_OUT / f"task{task}" / case / DECISION[task])
        c_raw = read(C_OUT / f"task{task}" / case / "sockets" / DECISION[task])
        a_pred, c_pred = norm(task, a_raw), norm(task, c_raw)
        cost_a, cost_c = a_use.get(case, {}), c_cost(task, case)
        rows.append(dict(task=task, case=case, truth=truth, answer_A=a_pred, answer_C=c_pred,
                         hit_A=hit(a_pred, truth, task),
                         hit_C=hit(c_pred, truth, task),
                         tokens_A=cost_a.get("total_tokens"), requests_A=cost_a.get("requests"),
                         tokens_C=cost_c.get("total_tokens"), requests_C=cost_c.get("requests"),
                         info_units_C=cost_c.get("info_units")))
    summary = dict(task=task, cases=len(rows))
    for arm, key in (("A", "hit_A"), ("C", "hit_C")):
        hits = sum(1 for r in rows if r[key])
        summary[arm] = dict(correct=hits, accuracy=round(hits / len(rows), 4) if rows else None,
                            tokens=sum(r[f"tokens_{arm}"] or 0 for r in rows),
                            requests=sum(r[f"requests_{arm}"] or 0 for r in rows),
                            completed=sum(1 for r in rows if r[f"answer_{arm}"] is not None))
    if task == 3:
        for arm in ("A", "C"):
            errors = [abs(r[f"answer_{arm}"][1] - r["truth"][1])
                      for r in rows if r[f"answer_{arm}"] and r["truth"]]
            summary[arm]["mae_months"] = round(statistics.fmean(errors), 2) if errors else None
    summary["info_units_C"] = sum(r["info_units_C"] or 0 for r in rows)
    return rows, summary


def main() -> int:
    deadline = time.time() + 14 * 3600
    while time.time() < deadline:
        text = (BENCH / "status.jsonl").read_text() if (BENCH / "status.jsonl").is_file() else ""
        if '"kind": "run_end"' in text:
            break
        pid_file = BENCH / "driver.pid"
        if pid_file.is_file():
            try:
                os.kill(int(pid_file.read_text().strip()), 0)
            except (ProcessLookupError, ValueError):
                break
        time.sleep(60)
    log("finalize_start")

    all_rows, summaries = [], []
    for task in (1, 2, 3):
        rows, summary = build(task)
        all_rows.extend(rows)
        summaries.append(summary)
        log("task_summary", **summary)

    csv_path = BENCH / "FINAL_CASES.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(all_rows[0]))
        writer.writeheader()
        writer.writerows(all_rows)
    (BENCH / "FINAL_RESULTS.json").write_text(
        json.dumps(dict(generated=time.strftime("%Y-%m-%d %H:%M:%S"), tasks=summaries),
                   indent=2, ensure_ascii=False) + "\n")
    log("finalize_end", cases=len(all_rows), csv=str(csv_path))
    print(json.dumps(summaries, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
