"""Observable component execution and opt-in strict experiment failures.

Diagnostics never enter the submission record. Legacy mode keeps fallbacks;
CHIMERA_STRICT_RUNTIME=1 rejects required components taking fallback paths.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from threading import RLock
from uuid import uuid4
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ComponentFailure(RuntimeError):
    pass


def strict_runtime() -> bool:
    return os.environ.get("CHIMERA_STRICT_RUNTIME", "0").lower() in {"1", "true", "yes", "on"}


def component_event(component: str, *, configured: bool, status: str,
                    reason: str | None = None, fallback: str | None = None,
                    required: bool = False, **details: Any) -> dict:
    event = {"component": component, "configured": configured, "status": status,
             "reason": reason, "fallback": fallback, "required": required, **details}
    runtime = current_runtime()
    if runtime is not None:
        runtime.emit('component', **event)
    root = os.environ.get("CHIMERA_OUTPUT_DIR")
    if root:
        try:
            path = Path(root) / "runtime_components.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            log.warning("Cannot persist component event for %s", component, exc_info=True)
            if strict_runtime():
                raise
    if required and status in {"failed", "fallback"} and strict_runtime():
        raise ComponentFailure(f"Required component {component}: {reason}; fallback={fallback}")
    return event


class ProcessingAudit:
    def __init__(self, task: int, case_id: str):
        self.task, self.case_id = task, case_id
        self.stages: list[dict] = []

    def record(self, stage: str, before: dict, after: dict, warnings: list[str], *,
               configured: bool = True, status: str = "executed", reason: str | None = None,
               fallback: str | None = None, required: bool = False) -> None:
        consumption_event(stage, before, after, status=status, task=self.task, case_id=self.case_id,
                          configured=configured, reason=reason, fallback=fallback,
                          warnings=list(warnings), authority='observed_existing_behavior')
        self.stages.append({"stage": stage, "configured": configured, "status": status,
                            "reason": reason, "fallback": fallback,
                            "before": deepcopy(before), "after": deepcopy(after),
                            "warnings": list(warnings)})
        root = os.environ.get("CHIMERA_OUTPUT_DIR")
        if root:
            try:
                path = Path(root) / f"task{self.task}" / self.case_id / "processing_audit.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps({"task": self.task, "case_id": self.case_id,
                                           "stages": self.stages}, ensure_ascii=False, indent=2, default=str))
                temp.replace(path)
            except OSError:
                log.warning("Cannot persist processing audit for %s", self.case_id, exc_info=True)
                if strict_runtime():
                    raise
        component_event(stage, configured=configured, status=status, reason=reason, fallback=fallback,
                        required=required, task=self.task, case_id=self.case_id)


# Fatal infrastructure failures intentionally bypass legacy `except Exception`
# recovery in graph/form-fill/atomic. Only orchestration catches BaseException,
# records failure and re-raises; these can never become a default diagnosis.
class RuntimeFatal(BaseException):
    pass


def json_value(value):
    if hasattr(value, 'model_dump'):
        return json_value(value.model_dump(mode='json'))
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise RuntimeFatal('Unsupported audit identity type: ' + type(value).__name__)


def canonical(value):
    return json.dumps(json_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def content_digest(value):
    import hashlib
    return hashlib.sha256(canonical(value).encode()).hexdigest()


_ACTIVE = ContextVar('chimera_private_runtime', default=None)
_CASE = ContextVar('chimera_case_identity', default={})
_STAGE = ContextVar('chimera_stage_identity', default='model_request_unattributed')


def current_runtime():
    return _ACTIVE.get()


def current_stage():
    return _STAGE.get()


@contextmanager
def case_scope(task, case_id):
    token = _CASE.set({'task': task, 'case_id': case_id})
    try:
        yield
    finally:
        _CASE.reset(token)


@contextmanager
def stage_scope(stage):
    token = _STAGE.set(stage)
    try:
        yield
    finally:
        _STAGE.reset(token)


def audit_stage(stage):
    from functools import wraps
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with stage_scope(stage):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def consumption_event(stage, before, after, *, status='observed', **details):
    runtime = current_runtime()
    if runtime is not None:
        return runtime.transition(stage, before, after, status=status, **details)


class PrivateRuntime:
    """One private, append-only run journal; never part of platform sockets.

    token_counter(payload) must count the complete wire template exactly or
    raise. Reservations charge input + maximum output for *every* attempt,
    including failed attempts; no credits based on absent usage metadata.
    """
    def __init__(self, root, *, max_attempts, max_reserved_tokens, token_counter,
                 identity=None, identity_check=None):
        if (type(max_attempts) is not int or max_attempts < 0
                or type(max_reserved_tokens) is not int or max_reserved_tokens < 0
                or not callable(token_counter)):
            raise RuntimeFatal('Finite request budgets and an exact counter are required')
        self.run_id = uuid4().hex
        self.root = Path(root).resolve() / self.run_id
        self.root.mkdir(parents=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / 'events.jsonl'
        self.max_attempts, self.max_reserved_tokens = max_attempts, max_reserved_tokens
        self.token_counter, self.identity_check = token_counter, identity_check
        self.attempts = self.reserved_tokens = self.seq = 0
        self.previous = None
        self.lock = RLock()
        self.identity_digest = content_digest(identity or {})
        self.reflection_test = None
        self.reflection_authority = None
        self.reflection_pilot = None      # C2a 受限真实模型准入，默认无   # 生产 Reflection，与合成测试注入相互独立
        self.emit('run_started', identity=identity or {})

    def emit(self, kind, **data):
        with self.lock:
            event = dict(schema_version=1, run_id=self.run_id, seq=self.seq, previous=self.previous,
                         kind=kind, stage=_STAGE.get())
            event.update(_CASE.get())
            event.update(data)
            try:
                event['digest'] = content_digest(event)
            except RuntimeFatal:
                raise
            except Exception as exc:
                raise RuntimeFatal('Private audit serialization failed') from exc
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, 'a', encoding='utf-8') as stream:
                    stream.write(canonical(event) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                raise RuntimeFatal('Private audit persistence failed') from exc
            self.seq += 1
            self.previous = event['digest']
            return event

    def transition(self, stage, before, after, *, status='observed', **details):
        before, after = json_value(before), json_value(after)
        changes = []
        def walk(a, b, path=''):
            if isinstance(a, dict) and isinstance(b, dict):
                for key in sorted(set(a) | set(b)):
                    p = path + '/' + key.replace('~', '~0').replace('/', '~1')
                    if key not in a or key not in b:
                        changes.append(dict(field=p, before=a.get(key), after=b.get(key),
                                            before_present=key in a, after_present=key in b))
                    else:
                        walk(a[key], b[key], p)
            elif a != b:
                changes.append(dict(field=path or '/', before=a, after=b,
                                    before_present=True, after_present=True))
        walk(before, after)
        return self.emit('consumption', stage=stage, status=status, before=before, after=after,
                         before_digest=content_digest(before), after_digest=content_digest(after),
                         changed_fields=changes, **details)

    @contextmanager
    def activate(self):
        token = _ACTIVE.set(self)
        try:
            yield self
        except BaseException as exc:
            self.emit('run_failed', error_type=type(exc).__name__)
            raise
        finally:
            _ACTIVE.reset(token)

    @contextmanager
    def wire(self, payload, *, backend, sdk_payload_digest=None):
        with self.lock:
            digest = content_digest(payload)
            maximum = payload.get('max_completion_tokens', payload.get('max_tokens'))
            boundary = {} if sdk_payload_digest is None else dict(
                sdk_payload_digest=sdk_payload_digest, effective_output_max=maximum)
            preflight = self.emit('request_preflight', backend=backend, payload=payload,
                                  payload_digest=digest, **boundary)
            if getattr(self, 'host_smoke', False) and self.attempts >= self.max_attempts:
                self.emit('request_rejected', backend=backend, reason='attempt_limit_before_count',
                          payload_digest=digest)
                raise RuntimeFatal('All-request budget exhausted before exact count')
            if self.identity_check:
                try:
                    self.identity_check()
                except RuntimeFatal:
                    raise
                except Exception as exc:
                    raise RuntimeFatal('Cannot verify pre-wire runtime identity') from exc
            try:
                state = self.token_counter(payload)
            except RuntimeFatal:
                raise
            except Exception as exc:
                self.emit('request_rejected', backend=backend, reason='exact_context_unavailable',
                          payload_digest=digest)
                raise RuntimeFatal('Exact full request context could not be verified') from exc
            if not isinstance(state, dict):
                raise RuntimeFatal('Exact counter returned an invalid receipt')
            count = state.get('input_tokens')
            if sdk_payload_digest is not None and content_digest(payload) != digest:
                raise RuntimeFatal('Exact counter modified the effective request')
            if type(count) is not int or count < 0 or type(maximum) is not int or maximum < 0 or not state.get('fits'):
                raise RuntimeFatal('Invalid or exhausted exact context reservation')
            reserve = count + maximum
            if self.attempts >= self.max_attempts or self.reserved_tokens + reserve > self.max_reserved_tokens:
                self.emit('request_rejected', backend=backend, reason='fatal_budget',
                          payload_digest=digest)
                raise RuntimeFatal('All-request budget exhausted before wire attempt')
            self.attempts += 1
            self.reserved_tokens += reserve
            attempt = self.attempts
            self.emit('wire_attempt', attempt=attempt, backend=backend, payload=payload,
                      preflight_digest=preflight['digest'], attempt_scope='sdk_submit_or_offline_chat',
                          payload_digest=digest, context=state,
                          reserved_tokens=reserve, total_reserved_tokens=self.reserved_tokens, **boundary)
        try:
            yield attempt
        except BaseException as exc:
            self.emit('wire_error', attempt=attempt, error_type=type(exc).__name__)
            raise
        else:
            self.emit('wire_return', attempt=attempt)

    def export_tree(self, root):
        """Export temporary evidence before deletion, never follow symlinks."""
        import hashlib
        root = Path(root)
        dest = self.root / 'temporary_evidence'
        manifest = {}
        for source in sorted(root.rglob('*')):
            if source.is_symlink():
                raise RuntimeFatal('Symlink in temporary audit evidence')
            if source.is_file():
                relative = source.relative_to(root)
                target = dest / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                data = source.read_bytes()
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                manifest[str(relative)] = hashlib.sha256(data).hexdigest()
        return self.emit('temporary_evidence_exported', files=manifest)

    def finalize_sockets(self, task, case_id, prediction, paths, values, *, weight_keys=None,
                         authority='existing_projection_and_align_unreviewed_D05'):
        import hashlib
        with case_scope(task, case_id):
            files = {}
            for key, path in paths.items():
                data = Path(path).read_bytes()
                if json.loads(data) != values[key]:
                    raise RuntimeFatal('Final socket readback mismatch')
                files[key] = dict(file=Path(path).name,
                                  sha256=hashlib.sha256(data).hexdigest(),
                                  content_digest=content_digest(values[key]))
            # Field mappings describe actual projection; they do not authorize
            # a correction or pretend an overwritten value was retained.
            decision_source = {1: '/biopsy_decision', 2: '/treatment_recommendation/primary',
                               3: '/event + /months_to_recurrence'}[task]
            mapping = dict(decision=decision_source,
                           reasoning='/free_text' if task == 3 else
                           dict(free_text='/free_text', confidence='/confidence',
                                variable_weights='/variable_weights', reveal_sequence='/reveal_sequence'))
            events = [json.loads(line) for line in self.path.read_text().splitlines()]
            stages = [e for e in events if e['kind'] == 'consumption'
                      and e.get('task') == task and e.get('case_id') == case_id]
            from dataclasses import asdict
            from chimera_agent_baseline.agent.reflection_contract import finalize_reflection
            rules = self.reflection_test.overwrites if self.reflection_test is not None else ()
            receipt = finalize_reflection(task, stages, values, rules, weight_keys=weight_keys)
            if self.reflection_test is not None and receipt is None:
                raise RuntimeFatal('Missing reflection selection before finalization')
            external = next((event['status'] for event in stages
                             if event['stage'] == 'external_reflection'), 'not_triggered')
            status = receipt.status if receipt is not None else 'structured_candidate_not_requested'
            return self.transition('final_sockets', prediction, values, status='finalized',
                                   files=files, projection=mapping,
                                   stage_digests=[e['digest'] for e in stages],
                                   overwrite_history=[dict(stage=e['stage'], digest=e['digest'],
                                                           changed_fields=e['changed_fields'])
                                                      for e in stages],
                                   reflection_consumption=status,
                                   external_reflection_status=external,
                                   reflection_receipt=asdict(receipt) if receipt is not None else None,
                                   authority=authority)
