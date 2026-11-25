#!/usr/bin/env python
"""Apply PF1+PF2 retrieval fixes to the deployed rag.py (with .bak backup).

PF1: reranker path fallback — run.py passes {model_dir}/reranker_model which does
     not exist (model ships under resources/reranker_model); fall back explicitly
     with a loud warning instead of silently disabling cross-encoder reranking.
PF2: rerank input truncation — c["text"][:512] cuts 1000-char chunks in half
     BEFORE tokenization; pass full text and let the tokenizer truncate at 512
     tokens. Eval evidence (2026-10-25): en R@5 buggy 0.817 vs fixed 0.883,
     zh R@5 buggy 0.742 vs fixed 0.833 — the two fixes MUST ship together.

Run on the server:
  cp ragfix_apply.py ~/CHIMERA-agent/rag_eval_20261025/
  ~/miniconda3/envs/chimera/bin/python ~/CHIMERA-agent/rag_eval_20261025/ragfix_apply.py
"""

import hashlib
import shutil
import sys
from pathlib import Path

RAG = Path("/home/hedongze/CHIMERA-agent/src/chimera_agent_baseline/rag.py")
BAK = RAG.with_suffix(".py.bak_ragfix_20261025")

OLD_INIT = """        reranker_dir = Path(reranker_dir) if reranker_dir is not None else resource_dir
"""
NEW_INIT = """        reranker_dir = Path(reranker_dir) if reranker_dir is not None else None
        if reranker_dir is not None and not reranker_dir.exists():
            _fallback = resource_dir / "reranker_model"
            if _fallback.exists():
                log.warning(
                    "reranker_dir %s not found - falling back to %s "
                    "(run.py passes model_dir/reranker_model; model ships under resources/)",
                    reranker_dir, _fallback,
                )
                reranker_dir = _fallback
        if reranker_dir is None:
            reranker_dir = resource_dir
"""

OLD_PAIRS = """            pairs = [(query_text, c["text"][:512]) for c in candidates]
"""
NEW_PAIRS = """            pairs = [(query_text, c["text"]) for c in candidates]
"""


def main():
    src = RAG.read_text(encoding="utf-8")

    for old, name in ((OLD_INIT, "PF1"), (OLD_PAIRS, "PF2")):
        n = src.count(old)
        if n != 1:
            print(f"ABORT: {name} pattern matched {n} times (expected 1) — rag.py differs from spec")
            sys.exit(1)

    if not BAK.exists():
        shutil.copy2(RAG, BAK)
        print(f"backup: {BAK}")

    patched = src.replace(OLD_INIT, NEW_INIT).replace(OLD_PAIRS, NEW_PAIRS)
    RAG.write_text(patched, encoding="utf-8")

    for p in (BAK, RAG):
        print(p.name, hashlib.md5(p.read_bytes()).hexdigest())
    print("APPLIED PF1+PF2 OK")


if __name__ == "__main__":
    main()
