#!/usr/bin/env python3
"""批次5-B：启动 236 GT case 全量跑（detach, 等价 nohup）。"""
import json
import subprocess
from pathlib import Path

ROOT = Path("/root/shared-nvme/chimera-agent-baseline")
BYT = json.loads((ROOT / "scripts" / "final_236_gt_pids.json").read_text())["by_task"]
pids = ",".join(BYT["task1"] + BYT["task2"] + BYT["task3"])

argv = [
    str(ROOT / ".venv/bin/python"),
    "-m",
    "chimera_agent_baseline.run",
    "+experiment=qwen_local",
    "agent.tasks=[1,2,3]",
    f"agent.pids=[{pids}]",
    "agent.predictor.decision_override_tasks=[]",
    "paths.output_dir=test/output.final_20260826",
]

log = open("/tmp/final_236_run.log", "a", buffering=1)
p = subprocess.Popen(
    argv,
    cwd=str(ROOT),
    stdout=log,
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)
print(p.pid, flush=True)