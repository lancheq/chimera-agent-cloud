"""Private, one-host approval for the three frozen Adaptive engineering fixtures."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import os
import subprocess

import httpx

from chimera_agent_baseline.entry_profile import EntryProfile, verify_identity
from chimera_agent_baseline.runtime_audit import RuntimeFatal, current_stage

ROOT = Path(__file__).resolve().parents[2]
BINARY = Path("/home/hedongze/llama-build/bin/llama-server")
WEIGHTS = ROOT / "model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
BINARY_SHA = "5e8d69056250cb5f263d721fae79bbc7b0b3246b038696493c50ee8155a3ad9a"
MODEL_SHA = "ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61"
TEMPLATE_SHA = "55d4931433fe502b794226ee7f4d206a6bdd436ac9f80eb7d8ebb4c639f9ea0c"
KNOWLEDGE_SHA = "13fbd478a3c4f2f3fa2f2fe60a9b48c5c838df097f15eab83077a36fc41bb4da"
NN = {1: "F01", 2: "F05", 3: "F06"}
# Relative paths and bytes of the only authorized agent inputs. The G0
# expectation file has no entry here, and a directory containing it fails.
FIXTURES = {
    1: ("5b880211c9cfb9776eb360a03bf4fad60a65604052cb49fdd06ce84c2c152f85",
        "4db249149e33327c77fd1184dfefe21a19eac161e7ffeb13369786d1897d3497",
        "580319f4af6237051a12081c62c6fd4e42bc1a4c77b33d7c8a96796be0faf31e"),
    2: ("94453f3392369ef82a988bfdbe94d4a6ac603db2c64eefec0b310a47b7e2eb92",
        "f9139f9b3f54840e614a157abc12e851c070668075a7e34cd0efa2cdf4f4d667",
        "70a6d4d262e1d6e2c25099a65f44a0948e9a66f9e6a09c93d2cccd5cd2fd1af5"),
    3: ("6226443966e160fa901772a15d376e7c0e3f2f5622bdd41c1a92dd7330055248",
        "81f602c5e56e0d8b1f619504d5b1a8ff19f653d034b7e6c7e34e084b89104cf2",
        "67efdaf101db019213715a3401e7bc25b4c7fe10cf3290d3e252163ea7855f10"),
}
INPUTS = ("inputs.json", "clinical-data.json", "structured-prompt.json")
COUNT_PATH = "/v1/chat/completions/input_tokens"


def sha(path):
    value = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def verify_fixture(path, task):
    path = Path(path)
    if set(p.name for p in path.iterdir()) != set(INPUTS):
        raise RuntimeFatal('Host smoke accepts only the three frozen input files')
    for name, digest in zip(INPUTS, FIXTURES[task], strict=True):
        file = path / name
        if file.is_symlink() or not file.is_file() or sha(file) != digest:
            raise RuntimeFatal('Frozen host fixture changed')


def _asset_stamp(path):
    state = path.stat()
    return state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns


def verify_assets():
    stamps = tuple(_asset_stamp(p) for p in (BINARY, WEIGHTS))
    for path, expected in ((BINARY, BINARY_SHA), (WEIGHTS, MODEL_SHA)):
        if sha(path) != expected:
            raise RuntimeFatal('Unapproved model or server file: ' + str(path))
    if tuple(_asset_stamp(p) for p in (BINARY, WEIGHTS)) != stamps:
        raise RuntimeFatal('Model/server file changed during identification')
    return stamps


def server_args(port):
    return [str(BINARY), "-m", str(WEIGHTS), "--host", "127.0.0.1", "--port", str(port),
            "-c", "16384", "--parallel", "1", "-ngl", "99", "-fa", "on",
            "-ctk", "q8_0", "-ctv", "q8_0", "--jinja", "-t", "8", "--seed", "20261110"]


class LocalServer:
    """Bind native counts and generation to one identified process and slot."""
    def __init__(self, pid, port, asset_stamps):
        if port not in (8765, 8766) or pid <= 0:
            raise RuntimeFatal('Unapproved host endpoint')
        self.pid, self.port, self.asset_stamps = pid, port, asset_stamps
        self.url = f"http://127.0.0.1:{port}"
        self.proc = Path(f"/proc/{pid}")
        self.start = self.proc.joinpath("stat").read_text().rpartition(") ")[2].split()[19]
        self.verify()
        self.client = httpx.Client(base_url=self.url, trust_env=False, follow_redirects=False, timeout=30)
        self.properties = None
        self.preflight_requests = 0
        self.formal_counts = 0
        self.preflight_passed = False

    def verify(self):
        try:
            cmd = self.proc.joinpath("cmdline").read_bytes().rstrip(b"\0").decode().split("\0")
            start = self.proc.joinpath("stat").read_text().rpartition(") ")[2].split()[19]
            listeners = subprocess.check_output(
                ["ss", "-H", "-ltnp", f"sport = :{self.port}"], text=True).splitlines()
            if (start != self.start or self.proc.joinpath("exe").resolve(strict=True) != BINARY
                    or cmd != server_args(self.port)
                    or tuple(_asset_stamp(p) for p in (BINARY, WEIGHTS)) != self.asset_stamps
                    or len(listeners) != 1 or listeners[0].split()[3] != f"127.0.0.1:{self.port}"
                    or f"pid={self.pid}," not in listeners[0]):
                raise ValueError("loopback listener, process or asset changed")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise RuntimeFatal('Unverified loopback model identity') from error

    def preflight(self, path, payload=None):
        self.verify()
        if self.preflight_requests >= 8 or path not in ("/props", COUNT_PATH, "/apply-template", "/tokenize"):
            raise RuntimeFatal('Unapproved non-generation probe')
        self.preflight_requests += 1
        response = self.client.get(path) if payload is None else self.client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    def bind_properties(self, props):
        slot = props.get("default_generation_settings", {}).get("n_ctx")
        if (type(slot) is not int or slot != 16384 or props.get("total_slots") != 1
                or props.get("model_path") != str(WEIGHTS) or props.get("build_info") != "b0-unknown"
                or not isinstance(props.get("chat_template"), str)
                or sha256(props["chat_template"].encode()).hexdigest() != TEMPLATE_SHA):
            raise RuntimeFatal('Model, template or slot identity mismatch')
        self.properties = {"context": slot, "slots": 1, "gguf_sha256": MODEL_SHA,
                           "binary_sha256": BINARY_SHA, "template_sha256": TEMPLATE_SHA}

    def count(self, payload):
        from chimera_agent_baseline.runtime_audit import content_digest
        if not self.preflight_passed or self.formal_counts >= 36:
            raise RuntimeFatal('Native token count is unapproved or exhausted')
        self.verify()
        stage = current_stage()
        maximum = payload.get("max_completion_tokens", payload.get("max_tokens"))
        limit = {"planner": 1024, "policy": 1024, "diagnosis": 4096, "form_fill": 4096}.get(stage)
        if (limit is None or type(maximum) is not int or not 0 < maximum <= limit
                or payload.get("model") != WEIGHTS.name):
            raise RuntimeFatal('Unknown model stage or stage output exceeds approval')
        self.formal_counts += 1
        response = self.client.post(COUNT_PATH, json=payload)
        response.raise_for_status()
        result = response.json()
        tokens = result.get("input_tokens")
        if type(tokens) is not int or tokens <= 0 or result.get("object") != "response.input_tokens":
            raise RuntimeFatal('Invalid native count response')
        fits = tokens + maximum <= min(16256, self.properties["context"] - 128)
        return dict(input_tokens=tokens, fits=fits, pid=self.pid, template_sha256=TEMPLATE_SHA,
                    payload_digest=content_digest(payload), source=COUNT_PATH,
                    context=self.properties["context"], context_reserve=128)


@dataclass(frozen=True)
class HostSmokeApproval:
    profile: EntryProfile
    server: LocalServer
    task: int
    input_dir: Path
    output_dir: Path

    def verify(self, cfg, config_path, input_dir, output_dir, task, *, require_empty_output=True):
        if (task != self.task or Path(input_dir).resolve() != self.input_dir
                or Path(output_dir).resolve() != self.output_dir
                or type(self.server) is not LocalServer or not self.server.preflight_passed
                or os.environ.get("CHIMERA_ENTRY_PROFILE") != self.profile.name
                or cfg.model.provider != "openai" or cfg.model.base_url != self.server.url + "/v1"
                or cfg.model.model_id != WEIGHTS.name or cfg.model.max_retries != 0
                or cfg.agent.workflow != "adaptive_v2_smoke" or list(cfg.agent.tasks) != [task]
                or self.profile.max_attempts != 12 or self.profile.max_reserved_tokens != 195072):
            raise RuntimeFatal('Host smoke task, endpoint or budget mismatch')
        if require_empty_output and (not self.output_dir.is_dir() or any(self.output_dir.iterdir())):
            raise RuntimeFatal('Host smoke needs a private empty output directory')
        info = cfg.agent.information_access
        if info.budget_units != 3 or info.max_attempts != 6 or sha(info.knowledge_manifest) != KNOWLEDGE_SHA:
            raise RuntimeFatal('Host smoke authorization for clinical tools or RAG differs')
        verify_fixture(input_dir, task)
        verify_identity(self.profile, cfg, config_path, host_smoke=True)
        self.server.verify()

    def count(self, payload):
        return self.server.count(payload)
