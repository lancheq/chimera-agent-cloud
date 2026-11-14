#!/usr/bin/env python3
"""Freeze and run a bounded two-GPU, four-arm T1 ablation on exposed data.

No submission, training, external API or prompt search is performed. Each lane
owns one local model service. Only this run's process groups can be terminated.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Shanghai")
ARMS = {
    "A_planned_500": {"workflow": "planned_react", "plan_context_chars": 500, "guidelines": True},
    "B_planned_full": {"workflow": "planned_react", "plan_context_chars": None, "guidelines": True},
    "C_react": {"workflow": "react", "plan_context_chars": None, "guidelines": True},
    "D_react_no_guidelines": {"workflow": "react", "plan_context_chars": None, "guidelines": False},
}
COMMON_ENV = {
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
    "OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4", "MKL_NUM_THREADS": "4",
    "CHIMERA_GATE": "0", "CHIMERA_T1_PROMPT": "baseline", "CHIMERA_QA_STRICT": "0",
    "CHIMERA_PLAN_TOOLS_FIX": "warn", "CHIMERA_FORMFILL_SELF_REFINE": "0",
    "CHIMERA_PRE_CALIB_DUMP": "1", "CHIMERA_T1_DECISION_LAYER": "v2",
    "CHIMERA_COST_ALIGN": "1", "CHIMERA_PREDICTOR_POST_REACT": "1",
    "CHIMERA_PROMOTE_NOT_USED": "1", "CHIMERA_DEMOTE_UNGROUNDABLE": "1",
    "CHIMERA_FORCE_CONF_CLEAR": "0", "CHIMERA_CONF_DOWNGRADE": "off",
    "CHIMERA_WEIGHT_TABLE": "1", "CHIMERA_PREDICTOR_NOTE": "1",
    # Preserve the current effective retrieval path (the configured model/
    # reranker snapshot is incomplete). All arms use the same RRF fallback;
    # repairing the reranker is a separate future experiment.
    "CHIMERA_RERANKER": "off", "LLAMA_API_KEY": "no-key",
}


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def write_json(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(run_dir: Path) -> dict:
    snapshot = run_dir / "snapshot"
    if snapshot.exists():
        raise FileExistsError(f"Snapshot already exists; never mix code versions: {snapshot}")
    snapshot.mkdir(parents=True)
    for name in ("src", "configs", "templates"):
        shutil.copytree(ROOT / name, snapshot / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("inference.py", "reasoning_align.py"):
        shutil.copy2(ROOT / name, snapshot / name)
    (snapshot / "tools").mkdir()
    for name in ("round2_regression.py", "summarize_round2_night.py", "run_round2_night.py"):
        shutil.copy2(ROOT / "tools" / name, snapshot / "tools" / name)
    resources = snapshot / "resources"
    resources.mkdir()
    for path in (ROOT / "resources").iterdir():
        if path.suffix == ".json" or path.name in {"guideline_qa.jsonl", "guideline_qa.embeddings.npy"}:
            shutil.copy2(path, resources / path.name)
    shutil.copytree(ROOT / "resources/case_index", resources / "case_index")
    shutil.copytree(ROOT / "resources/guidelines_db", resources / "guidelines_db")
    (resources / "embedding_model").symlink_to(ROOT / "resources/embedding_model", target_is_directory=True)
    (snapshot / "model").mkdir()
    shutil.copytree(ROOT / "model/predictor", snapshot / "model/predictor")
    official = ROOT / "output/round2_20261110/official_evaluator"
    shutil.copytree(official, snapshot / "official_evaluator", ignore=shutil.ignore_patterns("__pycache__"))

    cohort = run_dir / "cohort/task1/agent_input"
    cohort.mkdir(parents=True)
    ids = sorted(p.parent.name for p in (ROOT / "data/task1/agent_input").glob("*/prostate-biopsy-decision.json"))
    if len(ids) != 91:
        raise ValueError(f"Expected frozen legacy cohort of 91 labeled T1 cases, got {len(ids)}")
    for cid in ids:
        shutil.copytree(ROOT / "data/task1/agent_input" / cid, cohort / cid)
    hashes = {}
    for base in (snapshot, run_dir / "cohort"):
        for path in sorted(base.rglob("*")):
            if path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)):
                hashes[str(path.relative_to(run_dir))] = digest(path)
    write_json(run_dir / "frozen_hashes.json", hashes)
    model = ROOT / "model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
    protocol = {
        "created_at": now(), "project_root": str(ROOT), "cohort_role": "legacy_exposed", "task": 1, "case_ids": ids,
        "arms": ARMS, "primary_comparisons": [["A_planned_500", "B_planned_full"],
            ["B_planned_full", "C_react"], ["C_react", "D_react_no_guidelines"]],
        "common_environment": COMMON_ENV, "temperature": 0.3, "request_seed": 20261110,
        "max_model_turns": None, "case_failure_policy": "all 91 targets retained; at most one retry pass",
        "model": {"path": str(model), "bytes": model.stat().st_size, "mtime_ns": model.stat().st_mtime_ns},
        "notes": [
            "Normal graph routing already preserves the primary system prompt; no repair arm is needed.",
            "Planner removal also removes planner-only fewshot and forced tool execution.",
            "Final decisions retain the existing v2 decision layer; raw pre-calibration decisions are reported separately.",
            "Rationale judge disabled for initial official diagnostic scores; these are not full platform scores.",
            "Reranker was unavailable in the current configured path; explicit off preserves that common fallback.",
            "All arms share the MCP env-forwarding repair, enabling their isolated embedding sockets and frozen source imports.",
            "Single-seed screening on already-exposed training cases cannot establish generalization.",
        ],
    }
    write_json(run_dir / "protocol.json", protocol)
    return protocol


class NightRun:
    def __init__(self, run_dir: Path, python: str, hours: float):
        self.run_dir, self.python = run_dir, python
        self.snapshot = run_dir / "snapshot"
        self.deadline = time.monotonic() + hours * 3600
        self.lock = threading.Lock()
        self.state = {"started_at": now(), "status": "running", "hours_limit": hours, "arms": {}}
        self.protocol = json.loads((run_dir / "protocol.json").read_text())
        self.stop_material_monitor = threading.Event()

    def update(self, arm: str, **values):
        with self.lock:
            self.state["arms"].setdefault(arm, {}).update(values)
            self.state["updated_at"] = now()
            write_json(self.run_dir / "status.json", self.state)

    def service_ready(self, gpu: int) -> bool:
        descriptor = self.run_dir / f"service_gpu{gpu}.json"
        service = json.loads(descriptor.read_text())
        try:
            os.kill(service["pid"], 0)
            alive = True
        except ProcessLookupError:
            alive = False
        if not alive:
            with (self.run_dir / f"server_gpu{gpu}.log").open("a") as log:
                proc = subprocess.Popen(service["args"], stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, env=dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu)), start_new_session=True)
            service["pid"] = proc.pid
            write_json(descriptor, service)
        for _ in range(60):
            if time.monotonic() >= self.deadline:
                return False
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{8765 + gpu}/health", timeout=3) as response:
                    if response.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(5)
        return False

    def monitor_materials(self):
        # READY alone is not evidence of delivery. Only collect a successful
        # response from the Mac watcher; keep source project files untouched.
        pending = Path(self.protocol["project_root"]) / "git_auto_pending"
        destination = self.run_dir / "local_materials"
        while not self.stop_material_monitor.is_set():
            status = {"updated_at": now(), "status": "waiting_for_local_bridge"}
            if (pending / "DONE").is_file():
                if (pending / "DONE").read_text().strip() == "success" and (pending / "output").is_dir():
                    try:
                        if not destination.exists():
                            shutil.copytree(pending / "output", destination)
                        for docx in destination.rglob("*.docx"):
                            with zipfile.ZipFile(docx) as archive:
                                document = ET.fromstring(archive.read("word/document.xml"))
                            paragraphs = ["".join(paragraph.itertext()) for paragraph in document.iter(
                                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")]
                            docx.with_suffix(".extracted.txt").write_text("\n".join(paragraphs))
                        status["status"] = "received_and_extracted_pending_content_review"
                    except Exception as exc:
                        status.update(status="material_processing_failed", error=f"{type(exc).__name__}: {exc}")
                else:
                    status["status"] = "local_bridge_failed"
                write_json(self.run_dir / "materials_status.json", status)
                return
            write_json(self.run_dir / "materials_status.json", status)
            self.stop_material_monitor.wait(30)

    def arm(self, name: str, gpu: int):
        spec = ARMS[name]
        work = self.run_dir / "work" / name
        work.mkdir(parents=True)
        (work / "templates").symlink_to(self.snapshot / "templates", target_is_directory=True)
        # Chroma may write bookkeeping on open; each arm gets its own DB copy.
        shutil.copytree(self.snapshot / "resources/guidelines_db", work / "guidelines_db")
        (work / "resources").mkdir()
        for path in (self.snapshot / "resources").iterdir():
            if path.name == "guidelines_db":
                (work / "resources" / path.name).symlink_to(work / "guidelines_db", target_is_directory=True)
            else:
                (work / "resources" / path.name).symlink_to(path, target_is_directory=path.is_dir())
        out = self.run_dir / "arms" / name
        out.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("CHIMERA_")}
        env.update(COMMON_ENV)
        env.update({"PYTHONPATH": str(self.snapshot / "src"),
                    "CHIMERA_EMBED_SOCKET": f"/tmp/ch_r2_{os.getpid()}_{name[:8]}.sock",
                    "CHIMERA_T1_TOOL_DENY": "get_pathology_report,get_family_history"
                         + (",search_guidelines" if not spec["guidelines"] else "")})
        args = [self.python, "-m", "chimera_agent_baseline.run", "agent.tasks=[1]",
            f"agent.workflow={spec['workflow']}",
            f"agent.plan_context_chars={spec['plan_context_chars'] if spec['plan_context_chars'] is not None else 'null'}",
            "agent.retry_failed=true", "model.request_timeout=240", "+model.extra_body.seed=20261110",
            f"model.base_url=http://127.0.0.1:{8765 + gpu}/v1",
            f"paths.data_root={self.run_dir / 'cohort'}", f"paths.output_dir={out}",
            f"paths.resource_dir={work / 'resources'}", f"paths.model_dir={self.snapshot / 'model'}",
            f"paths.embedding_model_dir={self.snapshot / 'resources/embedding_model'}"]
        write_json(out / "command.json", {"args": args, "environment": {k:env[k] for k in env
            if k.startswith("CHIMERA_") or k in COMMON_ENV}, "gpu": gpu, "cwd": str(work)})
        for attempt in (1, 2):
            if not self.service_ready(gpu):
                self.update(name, status="service_unavailable", gpu=gpu)
                return
            self.update(name, status="running", attempt=attempt, gpu=gpu, started_at=now())
            with (out / f"attempt{attempt}.log").open("w") as log:
                proc = subprocess.Popen(args, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                        stderr=subprocess.STDOUT, start_new_session=True)
                self.update(name, pid=proc.pid)
                restarts = 0
                while proc.poll() is None:
                    if time.monotonic() >= self.deadline:
                        os.killpg(proc.pid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(proc.pid, signal.SIGKILL)
                            proc.wait()
                        self.update(name, status="time_limit_reached")
                        return
                    present = len(list((out / "task1").glob("*/prediction.json")))
                    self.update(name, predictions_present=present, expected=91)
                    service = json.loads((self.run_dir / f"service_gpu{gpu}.json").read_text())
                    try:
                        os.kill(service["pid"], 0)
                    except ProcessLookupError:
                        if restarts < 2:
                            restarts += 1
                            self.update(name, service_restarts_this_attempt=restarts)
                            self.service_ready(gpu)
                    time.sleep(15)
            missing = [cid for cid in self.protocol["case_ids"] if not (out / "task1" / cid / "prediction.json").is_file()]
            self.update(name, returncode=proc.returncode, missing_prediction_ids=missing,
                        predictions_present=91-len(missing))
            if not missing:
                break
        self.update(name, status="scoring", inference_complete=not missing)
        scoring = [self.python, str(self.snapshot / "tools/round2_regression.py"),
            "--evaluator", str(self.snapshot / "official_evaluator/evaluate.py"),
            "--mapping", str(self.snapshot / "official_evaluator/section_variable_mapping.json"),
            "--data-root", str(self.run_dir / "cohort"), "--predictions-root", str(out),
            "--task", "1", "--cohort-role", "legacy_exposed", "--output", str(out / "official_diagnostic")]
        with (out / "score.log").open("w") as log:
            result = subprocess.run(scoring, cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        self.update(name, status="complete" if not missing and result.returncode == 0 else "incomplete",
                    score_returncode=result.returncode, finished_at=now())

    def lane(self, gpu: int, names: list[str]):
        for name in names:
            try:
                self.arm(name, gpu)
            except Exception as exc:
                self.update(name, status="supervisor_error", error=f"{type(exc).__name__}: {exc}")

    def run(self):
        monitor = threading.Thread(target=self.monitor_materials, daemon=True)
        monitor.start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.lane, 0, ["A_planned_500", "D_react_no_guidelines"]),
                       pool.submit(self.lane, 1, ["C_react", "B_planned_full"])]
            for future in futures:
                future.result()
        self.stop_material_monitor.set()
        monitor.join(timeout=5)
        result = subprocess.run([self.python, str(self.snapshot / "tools/summarize_round2_night.py"),
                                 "--run-dir", str(self.run_dir)], timeout=180)
        self.state["status"] = "complete" if all(self.state["arms"].get(name, {}).get("status") == "complete"
                                                  for name in ARMS) and result.returncode == 0 else "incomplete"
        self.state["finished_at"] = now()
        frozen = json.loads((self.run_dir / "frozen_hashes.json").read_text())
        changed = [name for name, expected in frozen.items() if digest(self.run_dir / name) != expected]
        self.state["changed_frozen_files"] = changed
        if changed:
            self.state["status"] = "invalid_snapshot_changed"
        write_json(self.run_dir / "status.json", self.state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--hours", type=float, default=8)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--prepared", action="store_true")
    args = parser.parse_args()
    if not 0 < args.hours <= 12:
        parser.error("hours must be between 0 and 12")
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not args.prepared:
            prepare(run_dir)
        if not args.prepare_only:
            NightRun(run_dir, args.python, args.hours).run()


if __name__ == "__main__":
    main()
