#!/usr/bin/env python
"""Apply PF4-hardening: inject HF offline env vars into the embedding subprocess.

Root cause (2026-10-26): sentence-transformers probes the HF hub even for local
model paths; on network-blackholed hosts this hangs indefinitely (d208 measured:
15s with HF_HUB_OFFLINE=1 vs >400s hang without). The GC eval container is also
offline, so this hardens production startup as well.

Run on the server:
  ~/miniconda3/envs/chimera/bin/python ragfix_offline_apply.py
"""

import hashlib
import shutil
import sys
from pathlib import Path

RAG = Path("/home/hedongze/CHIMERA-agent/src/chimera_agent_baseline/rag.py")
BAK = RAG.with_suffix(".py.bak_ragfix_offline_20261026")

OLD = """        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _EMBED_SERVER_CODE, model_path, str(SOCKET_PATH)],
            stdout=subprocess.PIPE,
            text=True,
        )
"""

NEW = """        env = dict(os.environ)
        # Local snapshot only: hub probes hang for minutes on network-blackholed
        # hosts (d208 2026-10-26: 15s offline vs >400s hang). GC container is offline too.
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _EMBED_SERVER_CODE, model_path, str(SOCKET_PATH)],
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
"""


def main():
    src = RAG.read_text(encoding="utf-8")
    if src.count(OLD) != 1:
        print(f"ABORT: pattern matched {src.count(OLD)} times")
        sys.exit(1)
    if not BAK.exists():
        shutil.copy2(RAG, BAK)
        print(f"backup: {BAK}")
    RAG.write_text(src.replace(OLD, NEW), encoding="utf-8")
    for p in (BAK, RAG):
        print(p.name, hashlib.md5(p.read_bytes()).hexdigest())
    print("APPLIED offline-hardening OK")


if __name__ == "__main__":
    main()
