"""Case-scoped Adaptive entry permission for the frozen Phase2 pilot."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket

from chimera_agent_baseline.entry_profile import EntryProfile, verify_identity
from chimera_agent_baseline.host_smoke import INPUTS, KNOWLEDGE_SHA, ROOT, WEIGHTS, sha
from chimera_agent_baseline.runtime_audit import RuntimeFatal, current_stage

MOUNT = Path('/pilot/input')
SOCKETS = Path('/pilot/sockets')
CONTROL = Path('/pilot/control')
HOST_NAMESPACES = CONTROL / 'host_namespaces.json'
COUNTER_PATH = '/pilot/backend/count.sock'
SLUGS = {1: 'prostate-biopsy-decision-clinical-data',
         2: 'prostate-treatment-decision-clinical-data',
         3: 'prostate-time-to-recurrence-or-last-follow-up-clin'}


def _read(path):
    return json.loads(path.read_bytes())


def _require_unreadable(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except (FileNotFoundError, PermissionError):
        return
    os.close(fd)
    raise RuntimeFatal('Case pilot host/reference data is readable')


def verify_isolation():
    parent = _read(HOST_NAMESPACES)
    if set(parent) != {'mnt', 'pid', 'net'} or any(
            os.readlink(f'/proc/self/ns/{namespace}') == host for namespace, host in parent.items()):
        raise RuntimeFatal('Case pilot requires separate mount, PID and network namespaces')
    lines = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    matching = [line for line in lines if line[4] == str(MOUNT)]
    if len(matching) != 1 or 'ro' not in matching[0][5].split(','):
        raise RuntimeFatal('Case pilot requires a read-only input mount')
    for path in (ROOT/'data', ROOT/'.git', ROOT/'output/g2_case_pilot_20261119/preregistration.json',
                 Path('/proc/1/root') / ROOT.relative_to('/') / 'data'):
        _require_unreadable(path)


def verify_child_isolation(data_dir):
    verify_isolation()
    path = Path(data_dir).resolve(strict=True)
    if not path.is_relative_to(Path('/tmp')) or path.name != 'agent_input':
        raise RuntimeFatal('Case pilot MCP data directory is outside the entry-owned temporary view')


def socket_map(task):
    return [{'socket': {'slug': SLUGS[task], 'relative_path': 'clinical-data.json'}},
            {'socket': {'slug': 'structured-prompt', 'relative_path': 'structured-prompt.json'}}]


@dataclass(frozen=True)
class CasePilotEndpoint:
    """Use the outer supervisor's native counter without exposing its PID tree."""
    permit_sha256: str

    def _request(self, operation, **kwargs):
        try:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(45)
                connection.connect(COUNTER_PATH)
                request = dict(operation=operation, permit_sha256=self.permit_sha256, **kwargs)
                connection.sendall(json.dumps(request, allow_nan=False).encode() + b'\n')
                with connection.makefile('rb') as reader:
                    reply = json.loads(reader.readline(1024 * 1024))
            if reply['status'] == 'ok':
                return reply.get('result')
        except (OSError, ValueError, KeyError) as error:
            raise RuntimeFatal('Case pilot counter supervisor unavailable') from error
        raise RuntimeFatal('Case pilot counter supervisor rejected identity or budget')

    def verify(self):
        self._request('verify')

    def count(self, payload):
        return self._request('count', payload=payload, stage=current_stage())


@dataclass(frozen=True)
class CasePilotApproval:
    profile: EntryProfile
    task: int
    permit_file: Path
    permit_sha256: str
    server: CasePilotEndpoint | None
    dry: bool

    def _permit(self):
        if self.permit_file != CONTROL / 'permission.json' or sha(self.permit_file) != self.permit_sha256:
            raise RuntimeFatal('Case pilot permission identity differs')
        value = _read(self.permit_file)
        if (value['purpose'] != 'frozen_phase2_first_batch' or value['task'] != self.task
                or value['config_sha256'] != self.profile.config_sha256
                or value['source_sha256'] != dict(self.profile.sources)):
            raise RuntimeFatal('Case pilot permission task, configuration or source identity differs')
        return value

    @property
    def tools(self):
        return tuple(self._permit()['clinical_tools'])

    def verify_input(self, input_dir):
        verify_isolation()
        approved = self._permit()['view_sha256']
        if (Path(input_dir) != MOUNT or set(approved) != set(INPUTS)
                or {path.name for path in MOUNT.iterdir()} != set(INPUTS)):
            raise RuntimeFatal('Case pilot input mount or socket set differs')
        for name in INPUTS:
            path = MOUNT / name
            if path.is_symlink() or not path.is_file() or sha(path) != approved[name]:
                raise RuntimeFatal('Case pilot input view fingerprint differs')
        if _read(MOUNT/'inputs.json') != socket_map(self.task):
            raise RuntimeFatal('Case pilot socket task differs')

    def verify(self, cfg, config, input_dir, output_dir, task):
        self.verify_input(input_dir)
        info = cfg.agent.information_access
        if (task != self.task or Path(output_dir) != SOCKETS or not SOCKETS.is_dir()
                or cfg.model.provider != 'openai' or cfg.model.model_id != WEIGHTS.name
                or cfg.model.base_url != 'http://127.0.0.1:8765/v1' or cfg.model.max_retries != 0
                or not info.enabled or info.budget_units != 3 or info.max_attempts != 6
                or info.get('evidence_package') is not None or sha(info.knowledge_manifest) != KNOWLEDGE_SHA
                or cfg.agent.workflow != 'adaptive_v2_smoke' or list(cfg.agent.tasks) != [task]
                or cfg.agent.shared_plan.arm != 'adaptive' or cfg.agent.shared_plan.max_policy_turns != 5
                or self.profile.max_attempts != 12 or self.profile.max_reserved_tokens != 195072
                or os.environ.get('CHIMERA_ENTRY_PROFILE') != self.profile.name):
            raise RuntimeFatal('Case pilot configuration, endpoint or budget differs')
        verify_identity(self.profile, cfg, config, case_pilot=True)
        if self.dry:
            if self.server is not None:
                raise RuntimeFatal('Case pilot dry gate cannot receive a model')
        elif type(self.server) is not CasePilotEndpoint or self.server.permit_sha256 != self.permit_sha256:
            raise RuntimeFatal('Unapproved case pilot model endpoint')
        else:
            self.server.verify()

    def verify_materialized(self, prompt, clinical_path, task):
        if task != self.task:
            raise RuntimeFatal('Case pilot materialized task differs')
        expected_prompt = {**_read(MOUNT/'structured-prompt.json'), 'task': task, 'case_id': 'gc-case'}
        expected_clinical = {**_read(MOUNT/'clinical-data.json'), 'case_id': 'gc-case'}
        if prompt != expected_prompt or _read(clinical_path) != expected_clinical:
            raise RuntimeFatal('Case pilot materialization differs from approved view')

    def verify_registry(self, tools, task, runtime):
        actual = [tool.name for tool in tools]
        expected = sorted((*self.tools, 'search_guidelines'))
        if task != self.task or sorted(actual) != expected:
            raise RuntimeFatal('Case pilot MCP registration exposes a different tool set')
        runtime.emit('case_pilot_registry', allowed_tools=expected,
                     mcp_child_isolation='enforced_before_tools_list', denied_not_registered=True)

    def count(self, payload):
        if self.dry:
            raise RuntimeFatal('Case pilot dry gate cannot count a model request')
        return self.server.count(payload)
