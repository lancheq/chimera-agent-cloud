#!/usr/bin/env python3
"""Freeze guard for the public evaluation baseline.

Refuses to score anything if the pinned evaluator is not byte-identical to the
version this project's numbers were produced with. Provenance is recorded next to
the file so the pin can be re-derived rather than trusted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

EVAL = Path("/home/hedongze/CHIMERA-armC/output/bench/evaluator_official_main")
PIN = EVAL / "PIN.json"


def main() -> int:
    target = EVAL / "evaluate.py"
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    pin = json.loads(PIN.read_text())
    ok = digest == pin["sha256"]
    print(f"evaluate.py  sha256={digest}")
    print(f"pinned       sha256={pin['sha256']}")
    print(f"upstream     commit={pin['upstream_commit']} date={pin['upstream_date']} msg={pin['upstream_message']!r}")
    print("status:", "OK" if ok else "MISMATCH — 拒绝评分")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
