"""Real-case benchmark entry for the frozen Adaptive v2 candidate.

Why this module exists
----------------------
The project already has two Adaptive entries, and neither fits a paired
benchmark. ``host_smoke`` binds three synthetic fixtures by SHA and refuses any
other input. ``case_pilot`` binds /pilot mount+pid+net namespaces, a read-only
input mount and an outer counter socket, i.e. the Grand Challenge pilot
container. Both are correctly closed to real patient cases, and neither may be
reused here.

This entry binds what a controlled benchmark actually needs, and nothing less:

* a pre-registered case manifest -- per-case input file SHAs, so the case under
  test is identified rather than "whatever is on disk";
* the frozen candidate configuration, by path and SHA;
* the identified local model server (same GGUF as the other arm, by SHA);
* a private, empty output directory;
* a finite per-case request and token budget, enforced by native counting.

It does not relax the Adaptive controller, the information-access ledger, the
audit trail or the case scope. It replaces "three named synthetic fixtures" with
"a manifest of named real cases" and leaves everything else as strict.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess

import httpx

from chimera_agent_baseline.entry_profile import EntryProfile, verify_identity
from chimera_agent_baseline.runtime_audit import RuntimeFatal, content_digest, current_stage

COUNT_PATH = "/v1/chat/completions/input_tokens"
# Per-stage output ceilings, identical to the host-smoke approval. They bound
# what one request may reserve; they are not strategy settings.
STAGE_LIMITS = {"planner": 1024, "policy": 1024, "diagnosis": 4096, "form_fill": 4096}
CONTEXT_RESERVE = 256


def _safe_int(value) -> int:
    return value if type(value) is int else -1


class BenchmarkServer:
    """Bind native token counting to one identified, unchanging process.

    Unlike the host-smoke server this does not require a specific argv: the
    long-running host endpoint predates this benchmark and has a different
    (already validated) startup line. What it does require is that the weights,
    the binary and the process identity stay the same for the whole run, and
    that the same GGUF is what the other arm talks to.
    """

    def __init__(self, pid: int, port: int, weights: Path, model_sha256: str):
        if port <= 0 or pid <= 0:
            raise RuntimeFatal("Invalid local endpoint identity")
        self.pid, self.port, self.weights = pid, port, weights
        self.model_sha256 = model_sha256
        self.url = f"http://127.0.0.1:{port}"
        self.proc = Path(f"/proc/{pid}")
        try:
            self.cmdline = self.proc.joinpath("cmdline").read_bytes().rstrip(b"\0").decode().split("\0")
            self.start = self.proc.joinpath("stat").read_text().rpartition(") ")[2].split()[19]
        except OSError as error:
            raise RuntimeFatal("Benchmark endpoint process unreadable") from error
        self.client = httpx.Client(base_url=self.url, trust_env=False, follow_redirects=False, timeout=60)
        self.properties = None
        self.attempts = 0
        self.reserved_tokens = 0

    def verify(self):
        try:
            listeners = subprocess.check_output(
                ["ss", "-H", "-ltnp", f"sport = :{self.port}"], text=True).splitlines()
            live_cmdline = self.proc.joinpath("cmdline").read_bytes().rstrip(b"\0").decode().split("\0")
            start = self.proc.joinpath("stat").read_text().rpartition(") ")[2].split()[19]
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeFatal("Benchmark endpoint identity unreadable") from error
        if (live_cmdline != self.cmdline or start != self.start
                or len(listeners) != 1
                or listeners[0].split()[3] != f"127.0.0.1:{self.port}"
                or f"pid={self.pid}," not in listeners[0]):
            raise RuntimeFatal("Benchmark endpoint changed during the run")

    def bind_properties(self):
        response = self.client.get("/props")
        response.raise_for_status()
        props = response.json()
        model_path = props.get("model_path")
        settings = props.get("default_generation_settings") or {}
        context = settings.get("n_ctx")
        template = props.get("chat_template")
        if (not isinstance(model_path, str) or Path(model_path).name != self.weights.name
                or _safe_int(context) <= 0 or not isinstance(template, str)):
            raise RuntimeFatal("Benchmark model, template or context identity mismatch")
        from hashlib import sha256
        self.properties = dict(context=_safe_int(context), slots=props.get("total_slots"),
                               build_info=props.get("build_info"), model_path=model_path,
                               template_sha256=sha256(template.encode()).hexdigest(),
                               pid=self.pid, port=self.port, gguf_sha256=self.model_sha256)
        return self.properties

    def count(self, payload):
        if self.properties is None:
            raise RuntimeFatal("Benchmark counting used before identity binding")
        maximum = payload.get("max_completion_tokens", payload.get("max_tokens"))
        limit = STAGE_LIMITS.get(current_stage())
        if (limit is None or type(maximum) is not int or not 0 < maximum <= limit
                or payload.get("model") != self.weights.name):
            raise RuntimeFatal("Unknown model stage or stage output exceeds approval")
        self.verify()
        response = self.client.post(COUNT_PATH, json=payload)
        response.raise_for_status()
        result = response.json()
        tokens = result.get("input_tokens")
        if type(tokens) is not int or tokens <= 0 or result.get("object") != "response.input_tokens":
            raise RuntimeFatal("Invalid native count response")
        self.attempts += 1
        self.reserved_tokens += tokens + maximum
        fits = tokens + maximum <= self.properties["context"] - CONTEXT_RESERVE
        return dict(input_tokens=tokens, fits=fits, pid=self.pid,
                    template_sha256=self.properties["template_sha256"],
                    payload_digest=content_digest(payload), source=COUNT_PATH,
                    context=self.properties["context"], context_reserve=CONTEXT_RESERVE)


@dataclass(frozen=True)
class BenchmarkApproval:
    """One approved real case for the frozen Adaptive candidate."""

    profile: EntryProfile
    server: BenchmarkServer
    task: int
    case_id: str
    input_dir: Path
    output_dir: Path
    manifest_path: Path
    per_case_attempts: int
    per_case_reserved_tokens: int

    def _manifest(self) -> dict:
        try:
            value = json.loads(Path(self.manifest_path).read_bytes())
        except (OSError, ValueError) as error:
            raise RuntimeFatal("Benchmark case manifest unreadable") from error
        if not isinstance(value, dict) or value.get("task") != self.task:
            raise RuntimeFatal("Benchmark case manifest task mismatch")
        return value

    def _entry(self) -> dict:
        value = self._manifest()
        cases = value.get("cases") or {}
        if self.case_id not in cases:
            raise RuntimeFatal("Case is not in the pre-registered benchmark manifest")
        return cases[self.case_id]

    def verify_input(self, input_dir: Path) -> None:
        from chimera_agent_baseline.host_smoke import sha
        expected = self._entry()
        path = Path(input_dir)
        if path.resolve() != self.input_dir:
            raise RuntimeFatal("Benchmark input directory differs from the approved case")
        if {p.name for p in path.iterdir()} != set(expected):
            raise RuntimeFatal("Benchmark case file set differs from the manifest")
        for name, digest in expected.items():
            file = path / name
            if file.is_symlink() or not file.is_file() or sha(file) != digest:
                raise RuntimeFatal("Benchmark case file fingerprint differs from the manifest")

    def verify(self, cfg, config_path, input_dir, output_dir, task, *, require_empty_output=True):
        from chimera_agent_baseline.host_smoke import sha
        if (task != self.task or Path(input_dir).resolve() != self.input_dir
                or Path(output_dir).resolve() != self.output_dir
                or os.environ.get("CHIMERA_ENTRY_PROFILE") != self.profile.name
                or cfg.model.provider != "openai" or cfg.model.base_url != self.server.url + "/v1"
                or cfg.agent.workflow != "adaptive_v2_smoke" or list(cfg.agent.tasks) != [task]
                or self.profile.max_attempts != self.per_case_attempts
                or self.profile.max_reserved_tokens != self.per_case_reserved_tokens):
            raise RuntimeFatal("Benchmark task, endpoint or budget mismatch")
        if require_empty_output and (not self.output_dir.is_dir() or any(self.output_dir.iterdir())):
            raise RuntimeFatal("Benchmark needs a private empty output directory")
        info = cfg.agent.information_access
        if not info.enabled or _safe_int(info.budget_units) <= 0 or _safe_int(info.max_attempts) <= 0:
            raise RuntimeFatal("Benchmark requires an enabled, finite information-access budget")
        if not isinstance(info.knowledge_manifest, str) or not Path(info.knowledge_manifest).is_file():
            raise RuntimeFatal("Benchmark requires the frozen knowledge manifest")
        self.verify_input(Path(input_dir))
        if sha(Path(config_path)) != self.profile.config_sha256:
            raise RuntimeFatal("Benchmark configuration fingerprint differs")
        verify_identity(self.profile, cfg, config_path, benchmark=True)
        if self.server.properties is None:
            self.server.bind_properties()
        self.server.verify()

    def count(self, payload):
        if self.server.attempts >= self.per_case_attempts:
            raise RuntimeFatal("Benchmark per-case request budget exhausted")
        result = self.server.count(payload)
        if self.server.reserved_tokens > self.per_case_reserved_tokens:
            raise RuntimeFatal("Benchmark per-case token budget exhausted")
        return result
