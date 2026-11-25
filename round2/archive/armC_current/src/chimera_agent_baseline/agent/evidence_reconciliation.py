"""Private, deterministic annotations for already-authorized evidence."""
from dataclasses import dataclass
from itertools import combinations
import json
import math
import re

from langchain_core.messages import ToolMessage

from chimera_agent_baseline.agent.evidence_features import date_interval, decode_payload
from chimera_agent_baseline.information_access import digest
from chimera_agent_baseline.runtime_audit import RuntimeFatal, canonical, consumption_event


MARKER = '[Evidence timing and unresolved comparisons; references only]'
_UPFRONT = {'psa': 'psa', 'psad': 'psad', 'vol': 'volume', 'pirads_int': 'pirads'}
_TOOL_SECTIONS = {'get_psa_trend': 'psa_trend', 'get_lab_results': 'laboratory_results'}
_PSA_NAMES = {'psa', 'total psa', 'prostate specific antigen', 'prostate-specific antigen'}
_STATES = {'observed', 'negated', 'missing', 'planned', 'uncertain'}


@dataclass(frozen=True)
class Annotation:
    source: str
    document_digest: str
    path: tuple
    state: str
    entity: str | None = None
    definition: str | None = None
    method: str | None = None
    unit: str | None = None
    observation_id: str | None = None
    observed: str | None = None
    reported: str | None = None
    available: str | None = None


@dataclass(frozen=True)
class SyntheticEvidence:
    task: int
    case_id: str
    cutoff: str | None
    annotations: tuple[Annotation, ...]
    acquisition_deadline_unix: float | None = None


@dataclass(frozen=True)
class Source:
    name: str
    document: dict
    receipt: dict


def _position(first, second):
    if first is None or second is None:
        return 'unknown'
    if first[1] < second[0]:
        return 'before'
    if first[0] > second[1]:
        return 'after'
    return 'overlap'


def _period(raw):
    if raw is None:
        return None
    interval = date_interval(raw)
    if interval is None:
        raise ValueError('Invalid date or precision')
    return interval


def _value(source, path):
    if source.name == 'upfront':
        if len(path) != 1 or path[0] not in _UPFRONT or path[0] not in source.document:
            return None
        return _UPFRONT[path[0]], source.document[path[0]], None
    section = _TOOL_SECTIONS[source.name]
    if len(path) != 3 or path[0] != section or type(path[1]) is not int or path[2] != 'val':
        return None
    rows = source.document.get(section)
    idx = path[1]
    if not isinstance(rows, list) or not 0 <= idx < len(rows) or not isinstance(rows[idx], dict):
        return None
    row = rows[idx]
    if 'val' not in row or (section == 'laboratory_results' and
                            str(row.get('name', '')).strip().lower() not in _PSA_NAMES):
        return None
    return 'psa', row['val'], row.get('unit')


def compare(records):
    """Classify pairs while retaining every original observation reference."""
    relations = []
    for left, right in combinations(records, 2):
        a, b = left['annotation'], right['annotation']
        if left['variable'] != right['variable'] or a.state != 'observed' or b.state != 'observed':
            continue
        keys_a = (a.entity, a.definition, a.method, left['unit'])
        keys_b = (b.entity, b.definition, b.method, right['unit'])
        if not all(keys_a) or keys_a != keys_b:
            status = 'not_comparable'
        else:
            dates = _position(left['time'], right['time'])
            same_id = a.observation_id and a.observation_id == b.observation_id
            if dates in ('before', 'after') and not same_id:
                status = 'distinct_times'
            elif same_id and left['time'] is not None and left['time'] == right['time']:
                v1, v2 = left['value'], right['value']
                compatible_types = type(v1) is type(v2) or (type(v1) in (int, float) and type(v2) in (int, float))
                status = ('same_observation_same_value' if compatible_types and v1 == v2
                          else 'same_observation_conflict_unresolved')
            else:
                status = 'order_or_observation_unknown'
        pair = {'left': left['id'], 'right': right['id'], 'status': status}
        if status == 'distinct_times':
            pair['left_relative_to_right'] = dates
        relations.append(pair)
    return relations


def bound_tool_sources(binding, active_calls, results, new_events, seen_ids):
    """Join only actual graph calls and their uniquely matching newly settled receipts."""
    sources = []
    used_requests = set()
    for result in results:
        name = result.name
        if name not in _TOOL_SECTIONS or name not in binding['contract']['tools']:
            continue
        if result.status != 'success' or not result.additional_kwargs.get('information_access_clinical_success'):
            continue
        call_id = result.tool_call_id
        calls = [c for c in active_calls if c['name'] == name
                 and c['args'] == {'case_id': binding['case_id']}]
        if len(calls) != 1 or calls[0]['id'] != call_id or call_id in seen_ids:
            continue
        data = decode_payload(result.content)
        if not data or data.get('case_id') != binding['case_id']:
            continue
        data_digest = digest(data)
        matches = [e for e in new_events if e.get('status') == 'returned' and e.get('success') is True
                   and e.get('allowed') is True and e.get('session_id') == binding['session_id']
                   and e.get('case_id') == binding['case_id'] and e.get('tool') == name
                   and e.get('fields') == [_TOOL_SECTIONS[name]] and e.get('response_digest') == data_digest]
        if len(matches) != 1 or matches[0]['request_id'] in used_requests:
            continue
        event = matches[0]
        used_requests.add(event['request_id'])
        seen_ids.add(call_id)
        sources.append(Source(name, data, {'request_id': event['request_id'], 'tool_call_id': call_id,
                        'response_digest': data_digest, 'acquired_unix': event['timestamp_unix']}))
    return sources


def _unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('Duplicate MCP JSON field')
    return fields


def _native_text(result):
    from mcp.types import CallToolResult, TextContent

    if (type(result) is not CallToolResult or result.isError or result.model_extra
            or result.meta is not None or len(result.content) != 1):
        return None
    block = result.content[0]
    if (type(block) is not TextContent or block.model_extra or block.meta is not None
            or block.annotations is not None or result.structuredContent != {'result': block.text}):
        return None
    try:
        payload = json.loads(block.text, object_pairs_hook=_unique_fields)
        if type(payload) is not dict:
            return None
        canonical(payload)
    except (ValueError, TypeError, RecursionError):
        return None
    return block.text


class EvidenceSession:
    def __init__(self, fixture, run_id, binding, upfront):
        if fixture.task != binding['task'] or fixture.case_id != binding['case_id']:
            raise RuntimeFatal('B metadata has a different task or case binding')
        self.fixture = fixture
        self.binding = binding
        self.call_ids = set()
        self._mcp_text = {}
        self.sources = [Source('upfront', upfront, {'source_sha256': binding['initial_source_sha256']})]
        self.scope = {'run_id': run_id, **{k: binding[k] for k in ('case_id', 'session_id', 'snapshot_id', 'task')}}

    async def attest_mcp_result(self, request, call_next):
        # The adapter also flattens embedded text resources to LC text blocks.
        # Observe the native type here, before that type distinction is lost.
        result = await call_next(request)
        if (request.server_name != 'chimera' or request.name not in _TOOL_SECTIONS
                or request.name not in self.binding['contract']['tools']
                or request.args != {'case_id': self.binding['case_id']}):
            return result
        call_id = getattr(request.runtime, 'tool_call_id', None)
        if not isinstance(call_id, str) or not call_id:
            return result
        key = (request.name, call_id)
        raw = _native_text(result)
        self._mcp_text[key] = (result.model_dump(mode='json', by_alias=True)
                               if raw is not None and key not in self._mcp_text else None)
        return result

    def _attested_message(self, message):
        attestation = self._mcp_text.get((message.name, message.tool_call_id))
        blocks = message.content
        if attestation is None or message.status != 'success' or type(blocks) is not list or len(blocks) != 1:
            return None
        if (message.artifact != {'structured_content': attestation['structuredContent']}
                or message.additional_kwargs != {'information_access_clinical_success': True}):
            return None
        block = blocks[0]
        if (type(block) is dict and set(block) == {'type', 'text', 'id'} and block['type'] == 'text'
                and block['text'] == attestation['content'][0]['text']
                and isinstance(block['id'], str) and re.fullmatch(
                    r'lc_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', block['id'])):
            return attestation
        return None

    def observe(self, calls, messages, before, after):
        # A prefix mismatch is never evidence of acquisition.
        if before != after[:len(before)]:
            return
        eligible = [message for message in messages if isinstance(message, ToolMessage) and
                    (isinstance(message.content, str) or self._attested_message(message) is not None)]
        self.sources.extend(bound_tool_sources(self.binding, calls, eligible, after[len(before):], self.call_ids))

    def sdk_messages(self, messages):
        """Project only native, ledger-bound single text. Preserve graph messages."""
        sdk_view = []
        seen_calls = set()
        duplicate_calls = set()
        for message in messages:
            if isinstance(message, ToolMessage):
                if message.tool_call_id in seen_calls:
                    duplicate_calls.add(message.tool_call_id)
                seen_calls.add(message.tool_call_id)
        for message in messages:
            if not isinstance(message, ToolMessage) or isinstance(message.content, str):
                sdk_view.append(message)
                continue
            attestation = self._attested_message(message)
            receipt = next((s.receipt for s in self.sources if s.name == message.name
                            and s.receipt.get('tool_call_id') == message.tool_call_id), None)
            if attestation is None or receipt is None or message.tool_call_id in duplicate_calls:
                consumption_event('b_mcp_sdk_text', {}, {}, status='rejected_unbound_or_unsupported',
                                  binding=self.scope, tool=message.name, tool_call_id=message.tool_call_id)
                raise RuntimeFatal('Unbound or unsupported MCP tool content before SDK request')
            text = attestation['content'][0]['text']
            consumption_event('b_mcp_sdk_text', message.content, text, status='projected_for_sdk',
                              binding=self.scope, receipt=receipt, native_result_digest=digest(attestation),
                              native_result=attestation,
                              tool=message.name, tool_call_id=message.tool_call_id)
            sdk_view.append(message.model_copy(update={'content': text}))
        return sdk_view

    def decision_context(self):
        if self.binding['task'] == 3:
            consumption_event('b_evidence_agent_input', {}, {}, status='skipped_t3_no_approved_adapter',
                              binding=self.scope)
            return None
        try:
            projection, checked = self._project()
        except (TypeError, ValueError, KeyError) as error:
            consumption_event('b_evidence_agent_input', {}, {}, status='fallback_original_context',
                              error_type=type(error).__name__, binding=self.scope)
            return None
        if not projection['observations']:
            consumption_event('b_evidence_agent_input', {}, checked, status='skipped_no_eligible_evidence',
                              binding=self.scope)
            return None
        block = MARKER + '\n' + canonical(projection)
        consumption_event('b_evidence_agent_input', {}, projection, status='prepared_for_agent_request',
                          binding=self.scope, sources=checked, block_digest=digest(block))
        return block

    def _project(self):
        fixture = self.fixture
        cutoff = _period(fixture.cutoff)
        deadline = fixture.acquisition_deadline_unix
        if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
            raise ValueError('Invalid runtime acquisition deadline')
        checked, records, public = [], [], []
        for source in self.sources:
            source_digest = digest(source.document)
            annotations = [a for a in fixture.annotations if a.source == source.name
                           and a.document_digest == source_digest]
            if len({a.path for a in annotations}) != len(annotations):
                raise ValueError('Duplicate B source path annotation')
            if not annotations:
                checked.append(dict(source=source.name, document_digest=source_digest, receipt=source.receipt,
                                    status='no_bound_metadata'))
            for a in annotations:
                ref = dict(source=source.name, document_digest=source_digest, path=list(a.path))
                receipt = source.receipt
                audit = dict(reference=ref, receipt=receipt)
                checked.append(audit)
                triplet = _value(source, a.path)
                if triplet is None:
                    audit['status'] = 'unsupported_source_path'
                    continue
                variable, value, source_unit = triplet
                if a.state not in _STATES or type(value) not in (str, int, float, bool, type(None)):
                    raise ValueError('Invalid observation state or scalar type')
                if any(v is not None and (not isinstance(v, str) or not v.strip())
                       for v in (a.entity, a.definition, a.method, a.unit, a.observation_id, source_unit)):
                    raise ValueError('Invalid B comparison metadata')
                if a.state == 'observed' and value is None:
                    raise ValueError('Observed state cannot have null value')
                observed, reported, available = (_period(raw) for raw in (a.observed, a.reported, a.available))
                audit['times'] = dict(observed=a.observed, reported=a.reported, available=a.available,
                                      cutoff=fixture.cutoff, acquired_unix=receipt.get('acquired_unix'))
                timings = [_position(t, cutoff) for t in (observed, reported, available)]
                acquired = receipt.get('acquired_unix')
                if 'after' in timings or (deadline is not None and acquired is not None and acquired > deadline):
                    audit['status'] = 'after_cutoff'
                    continue
                if timings[-1] != 'before' or 'overlap' in timings:
                    audit['status'] = 'cutoff_availability_unknown'
                    continue
                if any(_position(t, available) == 'after' for t in (observed, reported)):
                    audit['status'] = 'inconsistent_availability_metadata'
                    continue
                unit = a.unit if source_unit is None and source.name == 'upfront' else source_unit
                if source_unit is not None and a.unit is not None and a.unit != source_unit:
                    unit = None
                oid = str(len(records) + 1)
                audit['status'] = 'eligible'
                records.append(dict(id=oid, variable=variable, value=value, annotation=a,
                                    time=observed, unit=unit))
                public.append(dict(id=oid, reference={'source': source.name, 'path': list(a.path),
                    **({'call': receipt['tool_call_id']} if 'tool_call_id' in receipt else {})},
                    variable=variable, state=a.state, observed=a.observed, reported=a.reported,
                    availability='before_cutoff', entity=a.entity, definition=a.definition, method=a.method, unit=unit))
        return {'observations': public, 'relations': compare(records)}, checked
