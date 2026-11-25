#!/usr/bin/env python3
"""Register every labelled case of the three tasks into the C-arm manifest."""

import json
from pathlib import Path
import subprocess
import sys

ARM = Path("/home/hedongze/CHIMERA-armC")
PY = "/home/hedongze/venvs/chimera_access/bin/python"
FILES = {1: "t1_full_91_gt_pids.json", 2: "t2_full_72_gt_pids.json", 3: "t3_full_73_gt_pids.json"}

for task, name in FILES.items():
    cases = json.loads((ARM / "scripts" / name).read_text())
    cmd = [PY, "bench/run_case.py", "register", "--task", str(task), "--cases", *cases,
           "--manifest", str(ARM / f"output/bench/cases_t{task}.json")]
    proc = subprocess.run(cmd, cwd=ARM, capture_output=True, text=True)
    print(f"task{task}: {len(cases)} 例 returncode={proc.returncode} {proc.stdout.strip()[:100]} {proc.stderr.strip()[-200:]}")
