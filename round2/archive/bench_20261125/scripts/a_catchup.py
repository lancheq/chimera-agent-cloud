#!/usr/bin/env python3
"""Per-case catch-up for A-arm cases the batch could not finish.

The official ``run.py`` has no per-case isolation: one case that never terminates
inside the ReAct recursion limit kills the whole batch, and a retry restarts at the
same case, so everything behind it is never attempted. Running such a batch is
therefore not "24 failures" -- it is one pathological case plus 23 blocked ones.
This script separates them by running each remaining case in its own process with
a hard timeout, and records the real failure reason per case.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ARM_A = Path("/home/hedongze/CHIMERA-armA")
ARM_C = Path("/home/hedongze/CHIMERA-armC")
BENCH = Path("/home/hedongze/CHIMERA-armC/output/bench")
A_OUT = ARM_A / "output/bench_A_full"
PY = "/home/hedongze/venvs/chimera_access/bin/python"
DECISION = {1: "prostate-biopsy-decision.json", 2: "prostate-treatment-decision.json",
            3: "prostate-time-to-recurrence-or-last-follow-up.json"}
CASE_FILES = {1: "t1_full_91_gt_pids.json", 2: "t2_full_72_gt_pids.json", 3: "t3_full_73_gt_pids.json"}
TIMEOUT = 900
WORKERS = 2


def log(kind, **fields):
    with (BENCH / "status.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(ts=time.time(), kind=kind, **fields), ensure_ascii=False) + "\n")


def missing(task):
    cases = json.loads((ARM_C / "scripts" / CASE_FILES[task]).read_text())
    return [c for c in cases if not (A_OUT / f"task{task}" / c / DECISION[task]).is_file()]


def run_one(task, case):
    sock = f"/tmp/chimera_catchup_{task}_{case}.sock"
    Path(sock).unlink(missing_ok=True)
    env = dict(os.environ, PYTHONPATH=str(ARM_A / "src"), HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", LLAMA_API_KEY="bench", CHIMERA_EMBED_SOCKET=sock,
               CHIMERA_BENCH_LOG=str(BENCH / f"A_cost_task{task}.jsonl"))
    cmd = [PY, "-m", "chimera_agent_baseline.run", "+experiment=qwen_local",
           "paths.data_root=/home/hedongze/CHIMERA-agent/data",
           "paths.resource_dir=/home/hedongze/CHIMERA-agent/resources",
           "paths.embedding_model_dir=/home/hedongze/CHIMERA-agent/model/embedding_model",
           "paths.model_dir=/home/hedongze/CHIMERA-agent/model",
           f"agent.tasks=[{task}]", f"agent.pids=[{case}]", f"paths.output_dir={A_OUT}"]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ARM_A, env=env, capture_output=True, text=True, timeout=TIMEOUT)
        ok = (A_OUT / f"task{task}" / case / DECISION[task]).is_file()
        tail = ((proc.stdout or "")[-600:] + (proc.stderr or "")[-900:]) if not ok else ""
        row = dict(arm="A", task=task, case=case, status="ok" if ok else "failed",
                   returncode=proc.returncode, seconds=round(time.time() - started, 1), tail=tail[-1200:])
    except subprocess.TimeoutExpired:
        row = dict(arm="A", task=task, case=case, status="timeout",
                   seconds=round(time.time() - started, 1))
    log("a_catchup", **row)
    Path(sock).unlink(missing_ok=True)
    return row


def main() -> int:
    tasks = [int(x) for x in sys.argv[1:]] or [2]
    queue = [(t, c) for t in tasks for c in missing(t)]
    log("a_catchup_start", cases=len(queue), workers=WORKERS)
    print(f"catch-up {len(queue)} cases", flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(run_one, t, c) for t, c in queue]
        for future in as_completed(futures):
            row = future.result()
            print(json.dumps({k: row.get(k) for k in ("task", "case", "status", "seconds")}, ensure_ascii=False), flush=True)
    log("a_catchup_end", remaining=len(missing(tasks[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
