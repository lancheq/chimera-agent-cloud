#!/usr/bin/env python3
"""One-case runner for the frozen Adaptive v2 benchmark arm (C arm).

Two subcommands:

  register  build the pre-registered case manifest: copy each real case into a
            private, canonical input view and record every input file's SHA.
  run       verify that manifest, then hand the case to ``inference.run``
            through the BenchmarkApproval entry.

The Adaptive controller, the information-access ledger, the per-case request
budget and the audit journal are all left exactly as the candidate defines them.
This script only supplies identity, input and budget.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import inference  # noqa: E402  (import after the path pin: this arm's tree must win)
from omegaconf import OmegaConf  # noqa: E402

from chimera_agent_baseline.entry_profile import (EntryProfile, effective_config,  # noqa: E402
                                                  effective_environment, runtime_flags, source_identity)
from chimera_agent_baseline.benchmark_entry import BenchmarkApproval, BenchmarkServer  # noqa: E402
from chimera_agent_baseline.runtime_audit import RuntimeFatal, content_digest  # noqa: E402

CLINICAL_SLUG = {
    1: "prostate-biopsy-decision-clinical-data",
    2: "prostate-treatment-decision-clinical-data",
    3: "prostate-time-to-recurrence-or-last-follow-up-clin",
}
CLINICAL_FILE = {
    1: "prostate-biopsy-decision-clinical-data.json",
    2: "prostate-treatment-decision-clinical-data.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
}
PER_CASE_ATTEMPTS = 12
PER_CASE_RESERVED_TOKENS = 195072


def sha(path) -> str:
    value = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def canonical_inputs(task: int) -> bytes:
    """The exact inputs.json bytes both the manifest and the run must agree on."""
    sockets = [
        {"socket": {"slug": CLINICAL_SLUG[task], "relative_path": "clinical-data.json"}},
        {"socket": {"slug": "structured-prompt", "relative_path": "structured-prompt.json"}},
    ]
    return (json.dumps(sockets, indent=2, sort_keys=True) + "\n").encode()


def build_view(task: int, case_id: str, data_root: Path, view: Path) -> dict:
    """Materialise the canonical one-case input view and return its file SHAs."""
    source = data_root / f"task{task}" / "agent_input" / case_id
    clinical = source / CLINICAL_FILE[task]
    prompt = source / "structured-prompt.json"
    for path in (clinical, prompt):
        if not path.is_file():
            raise RuntimeFatal(f"Case input missing: {path}")
    view.mkdir(parents=True, exist_ok=True)
    (view / "inputs.json").write_bytes(canonical_inputs(task))
    (view / "clinical-data.json").write_bytes(clinical.read_bytes())
    (view / "structured-prompt.json").write_bytes(prompt.read_bytes())
    return {name: sha(view / name) for name in ("inputs.json", "clinical-data.json", "structured-prompt.json")}


def cmd_register(args) -> int:
    data_root = Path(args.data_root)
    manifest = {"task": args.task, "data_root": str(data_root), "cases": {}}
    for case_id in args.cases:
        view = Path(args.work) / f"task{args.task}" / case_id / "input"
        manifest["cases"][case_id] = build_view(args.task, case_id, data_root, view)
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"registered": list(manifest["cases"]), "manifest": str(args.manifest)}, ensure_ascii=False))
    return 0


def model_sha(weights: Path, cache: Path) -> str:
    if cache.is_file():
        value = cache.read_text().strip()
        if len(value) == 64:
            return value
    value = sha(weights)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(value + "\n")
    return value


def cmd_run(args) -> int:
    task, case_id = args.task, args.case
    work = Path(args.work) / f"task{task}" / case_id
    view, out, audit = work / "input", work / "sockets", work / "private"
    for where in (view, out, audit):
        where.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise RuntimeFatal("Benchmark output directory must be empty")
    # Rebuild the view deterministically; the manifest check below proves bytes match.
    produced = build_view(task, case_id, Path(args.data_root), view)

    config_file = work / "config.yaml"
    cfg = OmegaConf.load(ROOT / "configs/config.yaml")
    overlay = OmegaConf.load(ROOT / "configs/round2_shared_plan_repair_v2.yaml")
    cfg = OmegaConf.merge(cfg, overlay)
    cfg.agent.workflow = "adaptive_v2_smoke"
    cfg.agent.shared_plan.arm = "adaptive"
    cfg.agent.tasks = [task]
    cfg.agent.pids = [case_id]
    cfg.agent.max_model_turns = 5
    cfg.agent.predictor.enabled = False
    cfg.agent.predictor.decision_override = False
    cfg.agent.predictor.t1_evidence_features = False
    cfg.agent.form_fill.self_refine = False
    cfg.agent.form_fill.atomic_check = False
    cfg.agent.information_access.enabled = True
    cfg.agent.information_access.run_id = args.run_id
    if args.knowledge_manifest:
        cfg.agent.information_access.knowledge_manifest = args.knowledge_manifest
    cfg.generation.max_new_tokens = 8192
    cfg.generation.temperature = 0.3
    cfg.model.base_url = f"http://127.0.0.1:{args.port}/v1"
    cfg.model.model_id = args.weights_name
    OmegaConf.save(cfg, config_file)

    inference.CONFIG_PATH = config_file
    inference.INPUT_PATH = view
    inference.OUTPUT_PATH = out
    inference.RESOURCE_PATH = Path(args.resources)
    inference.MODEL_PATH = Path(args.model_dir)
    inference.CASE_ID = case_id

    effective = inference._load_config(view / "owned-input", work / "owned-temp", task)
    name = args.run_id
    os.environ["CHIMERA_ENTRY_PROFILE"] = name
    profile = EntryProfile(
        name=name, config_path=str(config_file), config_sha256=sha(config_file),
        effective_digest=content_digest(effective_config(effective)), sources=source_identity(),
        environment=effective_environment(), runtime_flags=runtime_flags(), audit_root=str(audit),
        max_attempts=PER_CASE_ATTEMPTS, max_reserved_tokens=PER_CASE_RESERVED_TOKENS,
        purpose="adaptive_real_case_benchmark",
    )
    server = BenchmarkServer(args.pid, args.port, Path(args.weights), model_sha(Path(args.weights), Path(args.model_sha_cache)))
    server.bind_properties()
    approval = BenchmarkApproval(
        profile=profile, server=server, task=task, case_id=case_id,
        input_dir=view.resolve(), output_dir=out.resolve(), manifest_path=Path(args.manifest),
        per_case_attempts=PER_CASE_ATTEMPTS, per_case_reserved_tokens=PER_CASE_RESERVED_TOKENS,
    )
    # Fail before any model call if the case does not match its registration.
    expected = approval._entry()
    if expected != produced:
        raise RuntimeFatal("Rebuilt case view does not match the registered manifest")
    record = dict(case=case_id, task=task, entry_profile=name, input_sha256=produced,
                  model_sha256=server.model_sha256, endpoint=server.properties,
                  budget=dict(attempts=PER_CASE_ATTEMPTS, reserved_tokens=PER_CASE_RESERVED_TOKENS,
                              information_units=int(cfg.agent.information_access.budget_units)),
                  mechanisms=dict(workflow="adaptive_v2_smoke", adaptive=True, predictor=False,
                                  reflection=False, few_shot=False, weight_table=os.environ.get("CHIMERA_WEIGHT_TABLE"),
                                  reasoning_align="disabled_in_adaptive"),
                  config_sha256=profile.config_sha256, sources_sha256=len(profile.sources))
    (work / "approval.json").write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n")
    code = inference.run(_benchmark=approval)
    print(json.dumps({"case": case_id, "return": code, "out": str(out)}, ensure_ascii=False))
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    reg = sub.add_parser("register")
    run = sub.add_parser("run")
    for name, target in (("register", reg), ("run", run)):
        target.add_argument("--task", type=int, required=True)
        target.add_argument("--data-root", default="/home/hedongze/CHIMERA-agent/data")
        target.add_argument("--work", default=str(ROOT / "output/bench_work"))
        target.add_argument("--manifest", default=str(ROOT / "output/bench/cases.json"))
    reg.add_argument("--cases", nargs="+", required=True)
    run.add_argument("--case", required=True)
    run.add_argument("--run-id", default="adaptive-benchmark")
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--pid", type=int, required=True)
    run.add_argument("--weights", default="/home/hedongze/CHIMERA-agent/model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
    run.add_argument("--weights-name", default="Qwen3.6-35B-A3B-UD-Q4_K_M.gguf")
    run.add_argument("--model-dir", default="/home/hedongze/CHIMERA-agent/model")
    run.add_argument("--resources", default="/home/hedongze/CHIMERA-agent/resources")
    run.add_argument("--model-sha-cache", default=str(ROOT / "output/bench/model_sha256.txt"))
    run.add_argument("--knowledge-manifest",
                     default="/home/hedongze/CHIMERA-agent/output/s2_knowledge_snapshot_20261113_v4/manifest.json")
    args = parser.parse_args()
    return cmd_register(args) if args.cmd == "register" else cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
