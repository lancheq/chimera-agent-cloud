"""Assert that the container writes exactly the output-socket filenames the
platform validates against.

Rationale (GC run 17a095b4-1984-4238-b148-9abb5e0aef54): the container exited 0,
logged "Wrote GC result sockets for task 1 to /output", and the platform still
rejected it with "Output file 'prostate-biopsy-decision.json' was not produced"
-- because the code wrote the upstream baseline's ``biospy`` typo.

The authority for the expected names is the platform's own per-case socket dump
(``CHIMERA-agent/evaluation/test/input/predictions.json``), whose ``relative_path``
values are what GC checks for in /output. They are hard-coded here as EXPECTED.

Run inside the real image, as the image's own non-root ``user``:

    docker run --rm --network=none \
      -v "$PWD/.probe_mount:/mnt:ro" \
      --entrypoint python3 chimera-agent:submit /mnt/socket_contract_test.py
"""
import sys
import types
from pathlib import Path

# The module resolves this for its (never-written) per-case predictions.json.
OUT = Path("/tmp/gc_socket_out")
OUT.mkdir(parents=True, exist_ok=True)

# Capture _write_json calls instead of touching the module source.
captured: list[str] = []
shim = types.ModuleType("src.chimera_agent_baseline.utils")


def _setup_logging(*a, **k):
    return None


shim.setup_logging = _setup_logging
sys.modules["src.chimera_agent_baseline.utils"] = shim

sys.path.insert(0, "/opt/app")
import inference  # noqa: E402  (real module under test)

inference.OUTPUT_PATH = OUT
inference._write_json = lambda path, content: captured.append(Path(path).name)

# --- the platform's declared output sockets, per task ------------------------
EXPECTED = {
    1: {
        "decision": "prostate-biopsy-decision.json",
        "reasoning": "prostate-biopsy-decision-reasoning.json",
    },
    2: {
        "decision": "prostate-treatment-decision.json",
        "reasoning": "prostate-treatment-decision-reasoning.json",
    },
    3: {
        "decision": "prostate-time-to-recurrence-or-last-follow-up.json",
        "reasoning": "prostate-time-to-recurrence-or-last-follow-up-reasoning.json",
    },
}
# Minimal prediction shapes per task (only the keys _decision_value/_reasoning_value read).
PRED = {
    1: {"biopsy_decision": "yes", "free_text": "t", "confidence": 0.5,
        "variable_weights": {}, "reveal_sequence": []},
    2: {"treatment_recommendation": {"primary": "active_treatment"},
        "free_text": "t", "confidence": 0.5, "variable_weights": {},
        "reveal_sequence": []},
    3: {"event": 1, "months_to_recurrence": 12.0, "free_text": "t"},
}

failures = []
for task in (1, 2, 3):
    captured.clear()
    sockets = inference.OUTPUT_SOCKETS[task]
    inference._write_json(OUT / sockets["decision"],
                          inference._decision_value(task, PRED[task]))
    inference._write_json(OUT / sockets["reasoning"],
                          inference._reasoning_value(task, PRED[task]))
    got = set(captured)
    want = set(EXPECTED[task].values())
    status = "PASS" if got == want else "FAIL"
    if got != want:
        failures.append(task)
    print(f"task {task}: {status}")
    print(f"  written : {sorted(got)}")
    print(f"  expected: {sorted(want)}")
    if got != want:
        print(f"  MISSING : {sorted(want - got)}")
        print(f"  EXTRA   : {sorted(got - want)}")

print()
print("RESULT:", "ALL PASS" if not failures else f"FAILED tasks {failures}")
raise SystemExit(1 if failures else 0)
