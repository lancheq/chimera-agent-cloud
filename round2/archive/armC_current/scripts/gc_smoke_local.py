#!/usr/bin/env python3
"""Local Grand-Challenge smoke test - runs inference.py's run() per interface.

Patches inference.py's hardcoded mount-point constants to repo-relative paths,
so no /input, /output, /opt permissions are needed.

Usage: python scripts/gc_smoke_local.py [interf0 interf1 interf2]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import inference  # noqa: E402

INTERFACES = sys.argv[1:] or ["interf0", "interf1", "interf2"]

# Patch remaining hardcoded GC mount points (configs/resources/model).
inference.CONFIG_PATH = ROOT / "configs" / "config.yaml"
inference.RESOURCE_PATH = ROOT / "resources"
inference.MODEL_PATH = ROOT / "model"

for name in INTERFACES:
    inference.INPUT_PATH = ROOT / "test" / "input" / name
    out = ROOT / "test" / "outputs" / name
    out.mkdir(parents=True, exist_ok=True)
    inference.OUTPUT_PATH = out
    print(f"\n=== GC smoke: {name} ===", flush=True)
    rc = inference.run()
    print(f"=== GC smoke: {name} rc={rc} ===", flush=True)

print("\nALL_INTERFACES_DONE", flush=True)
