"""Trace-directory naming for per-case artefacts.

Grand Challenge feeds one anonymous case per container invocation, so the
pipeline id is always the synthetic ``gc-case``.  That is fine on the platform
but wrong for offline batches: every case then writes into the same
``trace/gc-case/`` directory and silently overwrites the previous case, which
makes per-case auditing (e.g. the decision-override snapshot) impossible after
the fact -- an A/B run over 51 cases left exactly one surviving trace.

Offline harnesses export ``CHIMERA_CASE_ID`` with the real case id; the platform
does not set it, so container behaviour is unchanged.
"""
from __future__ import annotations

import os
from typing import Any

__all__ = ["trace_case_id"]


def trace_case_id(default: Any) -> str:
    """Directory key for this case's trace: the real id when known."""
    override = os.environ.get("CHIMERA_CASE_ID", "").strip()
    return override or str(default)
