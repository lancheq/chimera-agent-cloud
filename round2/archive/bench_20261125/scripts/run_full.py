#!/usr/bin/env python3
"""Full paired benchmark driver: A arm (official) vs C arm (Adaptive v2).

Runs detached on the host, independent of any SSH session. Properties:

* resume  -- a case whose output already exists is skipped, so a crash or a
             reboot costs only the case in flight;
* bounded -- at most ``MAX_PARALLEL`` concurrent cases, chosen to stay inside the
             server's 4 slots;
* ordered -- the A arm runs first (cheap, batched), then the C arm;
* audited -- every case outcome is appended to ``status.jsonl`` and summarised in
             ``PROGRESS.txt`` after each completion.
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
PY = "/home/hedongze/venvs/chimera_access/bin/python"
BENCH = ARM_C / "output/bench"
STATUS = BENCH / "status.jsonl"
PROGRESS = BENCH / "PROGRESS.txt"
MAX_PARALLEL = 2
CASE_FILES = {
    1: ARM_C / "scripts/t1_full_91_gt_pids.json",
    2: ARM_C / "scripts/t2_full_72_gt_pids.json",
    3: ARM_C / "scripts/t3_full_73_gt_pids.json",
}
DECISION = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}
A_OUT = ARM_A / "output/bench_A_full"
C_OUT = ARM_C / "output/bench_work"


def log(kind: str, **fields) -> None:
    row = dict(ts=time.time(), kind=kind, **fields)
    with STATUS.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def cases(task: int) -> list[str]:
    return json.loads(CASE_FILES[task].read_text())


def endpoint_ok() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/props", timeout=15) as response:
            return response.status == 200
    except Exception:
        return False


def listener_pid() -> str:
    out = subprocess.check_output(["bash", "-lc",
        "ss -H -ltnp 'sport = :8765' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2"], text=True)
    return out.strip()


def a_done(task: int, case: str) -> bool:
    return (A_OUT / f"task{task}" / case / DECISION[task]).is_file()


def c_done(task: int, case: str) -> bool:
    return (C_OUT / f"task{task}" / case / "sockets" / DECISION[task]).is_file()


# ---------------------------------------------------------------- A arm (batch)
def run_a_task(task: int) -> dict:
    todo = [c for c in cases(task) if not a_done(task, c)]
    if not todo:
        return dict(arm="A", task=task, status="already_complete", cases=0)
    env = dict(os.environ, PYTHONPATH=str(ARM_A / "src"), HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", LLAMA_API_KEY="bench",
               CHIMERA_BENCH_LOG=str(BENCH / f"A_cost_task{task}.jsonl"),
               # The embedding service binds a fixed unix socket by default, so two
               # concurrent batches collide. One socket per task; removed afterwards.
               CHIMERA_EMBED_SOCKET=f"/tmp/chimera_bench_A{task}.sock")
    Path(env["CHIMERA_EMBED_SOCKET"]).unlink(missing_ok=True)
    cmd = [PY, "-m", "chimera_agent_baseline.run", "+experiment=qwen_local",
           "paths.data_root=/home/hedongze/CHIMERA-agent/data",
           "paths.resource_dir=/home/hedongze/CHIMERA-agent/resources",
           "paths.embedding_model_dir=/home/hedongze/CHIMERA-agent/model/embedding_model",
           "paths.model_dir=/home/hedongze/CHIMERA-agent/model",
           f"agent.tasks=[{task}]",
           "agent.pids=[" + ",".join(todo) + "]",
           f"paths.output_dir={A_OUT}"]
    log("a_start", task=task, cases=len(todo))
    started = time.time()
    attempts = 0
    proc = None
    while todo and attempts < 3:
        attempts += 1
        proc = subprocess.run(cmd, cwd=ARM_A, env=env, capture_output=True, text=True)
        todo = [c for c in cases(task) if not a_done(task, c)]
        if todo:
            cmd[-2] = "agent.pids=[" + ",".join(todo) + "]"
            log("a_retry", task=task, attempt=attempts, remaining=len(todo))
    tail = ((proc.stdout or "")[-2000:] + (proc.stderr or "")[-4000:]) if proc else ""
    done = sum(1 for c in cases(task) if a_done(task, c))
    log("a_end", task=task, returncode=proc.returncode if proc else None, done=done,
        requested=len(cases(task)), attempts=attempts,
        seconds=round(time.time() - started, 1), tail=tail[-1500:])
    Path(env["CHIMERA_EMBED_SOCKET"]).unlink(missing_ok=True)
    return dict(arm="A", task=task, status="ok" if done == len(cases(task)) else "partial",
                cases=done, requested=len(cases(task)))


# ---------------------------------------------------------------- C arm (per case)
def run_c_case(task: int, case: str, pid: str) -> dict:
    if c_done(task, case):
        return dict(arm="C", task=task, case=case, status="skipped")
    work = C_OUT / f"task{task}" / case
    for stale in ("sockets", "private"):
        target = work / stale
        if target.is_dir():
            subprocess.run(["rm", "-rf", str(target)], check=False)
    env = dict(os.environ, PYTHONPATH=str(ARM_C / "src"), HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", LLAMA_API_KEY="bench", CHIMERA_WEIGHT_TABLE="0",
               CHIMERA_BENCH_LOG=str(BENCH / f"C_cost_t{task}_{case}.jsonl"),
               CHIMERA_EMBED_SOCKET=f"/tmp/chimera_bench_C{task}_{case}.sock")
    Path(env["CHIMERA_EMBED_SOCKET"]).unlink(missing_ok=True)
    cmd = [PY, "bench/run_case.py", "run", "--task", str(task), "--case", case, "--pid", pid,
           "--manifest", str(BENCH / f"cases_t{task}.json"),
           "--run-id", f"full-t{task}-{case}"]
    started = time.time()
    proc = subprocess.run(cmd, cwd=ARM_C, env=env, capture_output=True, text=True)
    ok = c_done(task, case)
    row = dict(arm="C", task=task, case=case,
               status="ok" if ok else "failed", returncode=proc.returncode,
               seconds=round(time.time() - started, 1))
    if not ok:
        row["tail"] = ((proc.stdout or "")[-800:] + (proc.stderr or "")[-1500:])[-1800:]
    log("case_end", **row)
    return row


def progress() -> None:
    lines, total_done, total_all = [], 0, 0
    for task in (1, 2, 3):
        n = len(cases(task))
        a = sum(1 for c in cases(task) if a_done(task, c))
        c = sum(1 for c in cases(task) if c_done(task, c))
        total_done += a + c
        total_all += 2 * n
        lines.append(f"task{task}: A {a}/{n}   C {c}/{n}")
    lines.append(f"total: {total_done}/{total_all} case-runs")
    lines.append(f"updated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    PROGRESS.write_text("\n".join(lines) + "\n")


def main() -> int:
    BENCH.mkdir(parents=True, exist_ok=True)
    log("run_start", pid=os.getpid())
    if not endpoint_ok():
        log("abort", reason="endpoint_down")
        return 2
    log("endpoint", pid=listener_pid())

    # Stage 1: A arm, one batch per task, two tasks at a time.
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = [pool.submit(run_a_task, t) for t in (1, 2, 3)]
        for future in as_completed(futures):
            log("a_task_result", **future.result())
            progress()

    # Stage 2: C arm, one process per case, bounded concurrency.
    pid = listener_pid()
    queue = [(t, c) for t in (1, 2, 3) for c in cases(t)]
    failures: list[tuple[int, str]] = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = {pool.submit(run_c_case, t, c, pid): (t, c) for t, c in queue}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if result.get("status") == "failed":
                failures.append((result["task"], result["case"]))
            if completed % 5 == 0:
                progress()
            if result.get("status") == "failed" and not endpoint_ok():
                log("abort", reason="endpoint_lost_after", case=result.get("case"))
                for pending in futures:
                    pending.cancel()
                return 3

    # One bounded retry pass for cases that failed. The card allows 12 retries.
    if failures:
        retry = failures[:12]
        log("c_retry_pass", cases=[c for _, c in retry])
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            for result in pool.map(lambda item: run_c_case(item[0], item[1], pid), retry):
                log("c_retry_result", **result)
    progress()
    log("run_end", failures=len(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
