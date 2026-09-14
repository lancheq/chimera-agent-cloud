"""End-to-end check of the GC adapter's MCP case-data path.

Why this test exists -- GC run a7d7744d, whose own self_refine critique said:

    "The reasoning cites specific clinical values (PSA 4.7, PI-RADS 2, PSAD
     0.14, Prostate volume 33.46 mL, csPCa probability 0.596) that are not
     present in the provided evidence transcript."
    "'The tools are returning Case not found errors' ... yet proceeds to
     generate a detailed clinical recommendation based on fabricated values"

Cause: ``_materialise_case`` wrote ``clinical.json`` while ``CaseDataStore``
resolves the case file through ``CASE_DATA_FILENAMES_BY_TASK`` (the
task-specific name). The store therefore logged "Loaded 0 cases" and every
tool answered "Case ... not found", so the agent reasoned with NO clinical
data and invented the numbers. The school-server batch never caught it because
the real data trees already carry the long filename.

This test drives the ACTUAL production path in the image:
    fixture sockets -> _materialise_case -> CaseDataStore -> tool call

It asserts the store finds the case and a tool returns real clinical values.
"""

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, "/opt/app")

shim = types.ModuleType("src.chimera_agent_baseline.utils")
shim.setup_logging = lambda *a, **k: None
sys.modules["src.chimera_agent_baseline.utils"] = shim

import inference  # noqa: E402
from src.chimera_agent_baseline.tools.base import (  # noqa: E402
    CASE_DATA_FILENAMES_BY_TASK,
    CaseDataStore,
)

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        FAILURES.append(msg)


CASES = Path(os.environ.get("CHIMERA_CASE_DIR", "/cases/test/input/interf0"))
if not CASES.is_dir():
    print(f"FATAL: fixture dir missing: {CASES}")
    raise SystemExit(2)
# The fixture is a single GC-style socket set (not a per-case tree).
case_dir = CASES
print(f"fixture case sockets: {case_dir}")

# Sockets exactly as GC presents them for task 1.
PROMPT = next(p for p in case_dir.glob("*prompt*.json"))
CLINICAL = next(p for p in case_dir.glob("*clinical-data.json"))
NEURAL = next(p for p in case_dir.glob("*neural-representations.json"))
slug_to_path = {
    inference.STRUCTURED_PROMPT_SLUG: PROMPT,
    inference.NEURAL_REP_SLUG: NEURAL,
    "prostate-biopsy-decision-clinical-data": CLINICAL,
}

with tempfile.TemporaryDirectory(prefix="adapter-e2e-") as tmp:
    root = Path(tmp) / "input"

    print("\n=== 1) _materialise_case 产出（真实适配器）===")
    inference._materialise_case(1, slug_to_path, root, inference.CASE_ID)
    out_dir = root / "task1" / "agent_input" / inference.CASE_ID
    produced = sorted(p.name for p in out_dir.iterdir())
    print(f"  {out_dir}")
    for name in produced:
        print(f"    {name}  ({out_dir.joinpath(name).stat().st_size} B)")

    expected_name = CASE_DATA_FILENAMES_BY_TASK[1]
    check(expected_name in produced,
          f"写出了 MCP 期望的任务名文件 {expected_name}")
    check("prompt.json" in produced, "写出了 prompt.json")

    print("\n=== 2) CaseDataStore 能否加载（这就是 GC 上失败的一步）===")
    # NB: the store takes the *agent_input* directory, exactly as run.py passes
    # it ("data/task1/agent_input") -- not the per-case directory.
    store = CaseDataStore(out_dir.parent)
    ids = store.list_case_ids()
    print(f"  Loaded case ids: {ids}")
    check(len(ids) == 1, f"加载到 1 个 case（GC 实测为 0）: {ids}")
    check(inference.CASE_ID in ids, f"case_id 为 {inference.CASE_ID}")

    print("\n=== 3) 工具能否取到真实临床数据 ===")
    case = store.get_case(inference.CASE_ID)
    check(case is not None, "get_case 返回非 None")
    if case:
        keys = sorted(k for k in case if k != "case_id")
        print(f"  可用字段: {keys}")
        check(len(keys) > 0, f"case 带有真实字段（非空）: {len(keys)} 个")
        # These are the fields the task-1 tools serve.
        for field in ("radiology_report", "psa_trend", "family_history"):
            check(field in case, f"包含 {field}")
        rad = str(case.get("radiology_report", ""))[:70].replace("\n", " ")
        print(f"  radiology_report 片段: {rad}...")

    print("\n=== 4) 对照：仅写 clinical.json 时应当失败（复现旧缺陷）===")
    legacy_dir = Path(tmp) / "legacy" / "task1" / "agent_input" / inference.CASE_ID
    legacy_dir.mkdir(parents=True)
    import json as _json
    (legacy_dir / "clinical.json").write_text(
        _json.dumps(dict(case or {}, case_id=inference.CASE_ID)))
    legacy_store = CaseDataStore(legacy_dir.parent)
    check(len(legacy_store.list_case_ids()) == 0,
          "只有 clinical.json 时加载 0 个 case（复现 GC 上 Loaded 0 cases 的旧缺陷）")

print()
print("RESULT:", "ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED")
for f in FAILURES:
    print("   -", f)
raise SystemExit(1 if FAILURES else 0)
