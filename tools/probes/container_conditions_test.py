#!/usr/bin/env python3
"""Container-conditions contract test — run INSIDE the real GC image.

Why this exists
---------------
Every other local check (the 5 functional suites, the offline harness) runs with
a writable CWD and, usually, CHIMERA_OUTPUT_DIR pointing somewhere writable.  So
none of them can see a whole class of bugs: code that writes to a path which is
only unwritable **in the container**.

That class cost us a platform try-out on 2026-09-13:

    ERROR Task 2: case gc-case FAILED after 62.1s, skipping:
          [Errno 13] Permission denied: 'output'
    FileNotFoundError: .../output/task2/gc-case/prediction.json

Mechanism: the image sets ``WORKDIR /opt/app`` and ``USER user``, but /opt/app
is created root-owned (WORKDIR runs as root), so the non-root user cannot create
anything in its CWD.  Every "trace dump" defaults to the RELATIVE path
``os.environ.get("CHIMERA_OUTPUT_DIR", "output")``, so ``makedirs`` hits EACCES.
The older dumps wrap that in try/except; the override snapshot added in commit
562111f did not -- and it only runs for tasks that override (T2/T3), which is
exactly why T1 try-out passed and T2 died.

What this test does (all under the container's real CWD/user/env):
  1. proves the condition is real (the default trace dir is NOT creatable);
  2. AST-audits the SHIPPED sources: every ``makedirs`` must sit inside a
     ``try`` block -- this is the check that would have caught the bug;
  3. drives the real ``form_fill`` node with a stub model all the way through
     the override-snapshot path and asserts it does not raise.

No GPU, no LLM, no model mount required.
"""
from __future__ import annotations

import ast
import json
import os
import sys
import traceback
from pathlib import Path

FAILS: list[str] = []
PASSES: list[str] = []


def ok(msg: str) -> None:
    PASSES.append(msg)
    print(f"PASS  {msg}")


def bad(msg: str) -> None:
    FAILS.append(msg)
    print(f"FAIL  {msg}")


def line(msg: str) -> None:
    print(f"      {msg}")


# --- 1. is the container condition actually present? ------------------------
print("=" * 72)
print("[1] container condition")
print("=" * 72)
cwd = os.getcwd()
line(f"cwd           = {cwd}")
line(f"uid/gid       = {os.getuid()}/{os.getgid()}  ({os.environ.get('USER', '?')})")
line(f"CHIMERA_OUTPUT_DIR = {os.environ.get('CHIMERA_OUTPUT_DIR')!r} (unset means relative 'output')")

trace_root = os.environ.get("CHIMERA_OUTPUT_DIR", "output")
probe = os.path.join(trace_root, "trace", "__probe__")
try:
    os.makedirs(probe, exist_ok=True)
    ok("default trace dir IS creatable here (condition absent -- test proves nothing)")
    os.rmdir(probe)
except PermissionError as exc:
    ok(f"default trace dir is NOT creatable ({exc}) -> the guard is load-bearing")
except Exception as exc:  # noqa: BLE001
    line(f"unexpected error creating probe: {type(exc).__name__}: {exc}")

# --- 2. AST audit: every makedirs inside a try -------------------------------
print()
print("=" * 72)
print("[2] shipped-source audit: every makedirs must be inside a try")
print("=" * 72)


class MakedirsAudit(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.try_depth = 0
        self.total = 0
        self.unguarded: list[int] = []

    def visit_Try(self, node: ast.Try) -> None:
        self.try_depth += 1
        self.generic_visit(node)
        self.try_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "makedirs":
            self.total += 1
            if self.try_depth == 0:
                self.unguarded.append(node.lineno)
        self.generic_visit(node)


roots = [Path("/opt/app/src/chimera_agent_baseline")]
for extra in ("/opt/app/inference.py",):
    p = Path(extra)
    if p.exists():
        roots.append(p)

files: list[Path] = []
for r in roots:
    files.extend(sorted(r.rglob("*.py")) if r.is_dir() else [r])
files = [f for f in files if "__pycache__" not in str(f)]

grand_total = 0
for f in files:
    try:
        tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=str(f))
    except SyntaxError as exc:
        bad(f"{f}: unparseable ({exc})")
        continue
    audit = MakedirsAudit(f)
    audit.visit(tree)
    grand_total += audit.total
    if audit.unguarded:
        bad(f"{f}: {len(audit.unguarded)} unguarded makedirs at lines {audit.unguarded}")
line(f"scanned {len(files)} files, {grand_total} makedirs call sites")
if grand_total == 0:
    bad("no makedirs found at all -- audit did not really run")
elif not FAILS:
    ok(f"all {grand_total} makedirs call sites are inside try/except")

# --- 3. drive the real form_fill node through the override snapshot ----------
print()
print("=" * 72)
print("[3] form_fill node with decision_override=True (the path that crashed)")
print("=" * 72)

try:
    sys.path.insert(0, "/opt/app")
    sys.path.insert(0, "/opt/app/src")
    from langchain_core.messages import AIMessage  # noqa: E402

    from chimera_agent_baseline.agent.form_fill import (  # noqa: E402
        eligible_variables,
        make_form_fill_node,
    )

    TASK = 2
    called: set[str] = {"get_mri_report", "get_pathology_report"}
    elig = list(eligible_variables(TASK, called))
    line(f"eligible_variables = {elig}")

    judgment = {
        "treatment_recommendation": {
            "primary": "active_treatment",
            "modalities": ["radical_prostatectomy"],
            "detail": "High-risk disease; definitive local therapy indicated.",
            "as_protocol": None,
            "as_trigger": None,
        },
        "confidence": "clear",
        "variable_weights": {k: "noted" for k in elig},
        "free_text": (
            "PSA 11.0 with a PI-RADS 5 lesion and ISUP grade group 3 on biopsy; "
            "this is high-risk clinically significant prostate cancer and "
            "definitive local therapy is indicated."
        ),
        "repeat_test": None,
    }
    payload = json.dumps(judgment)

    class _Resp:
        def __init__(self, content: str) -> None:
            self.content = content
            self.additional_kwargs: dict = {}

    class StubModel:
        """Returns the same canned judgment for every call."""

        def invoke(self, messages, *a, **kw):  # noqa: ANN001, ANN002, ANN003
            return _Resp(payload)

    node = make_form_fill_node(
        StubModel(), max_retries=1, decision_override=True,
        self_refine=False, atomic_check=False,
    )
    state = {
        "messages": [AIMessage(content="Case reviewed; structured record follows.")],
        "task": TASK,
        "case_id": "container-conditions-test",
        "patient": {"psa": 11.0, "age": 68},
        "predictor_decision": {"decision": "continued_surveillance", "task": TASK},
        "floor_inputs": {},
    }
    result = node(state)
    sr = result.get("structured_response") or {}
    if sr.get("treatment_recommendation", {}).get("primary"):
        ok("form_fill completed through the override snapshot "
           f"(primary={sr['treatment_recommendation']['primary']})")
    else:
        bad(f"form_fill returned without a usable structured_response: {list(sr)[:6]}")
    line(f"warnings: {result.get('form_fill_warnings')}")
except Exception:  # noqa: BLE001
    bad("form_fill raised under container conditions:")
    for ln in traceback.format_exc().splitlines()[-12:]:
        line(ln)

# --- verdict -----------------------------------------------------------------
print()
print("=" * 72)
if FAILS:
    print(f" RESULT: {len(FAILS)} FAILED / {len(PASSES)} passed")
    print("=" * 72)
    raise SystemExit(1)
print(f" RESULT: ALL PASS ({len(PASSES)} checks)")
print("=" * 72)
raise SystemExit(0)
