"""Opt-in, offline projection of a pinned synthetic B1/B2 relation."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from hashlib import sha256
import json
import math

from chimera_agent_baseline.agent.evidence_reconciliation import Annotation, _period, _position, compare
from chimera_agent_baseline.information_access import canonical, digest


UNKNOWN = 'UNKNOWN'
_BOUND = 'BOUND_FOR_SYNTHETIC_AUDIT'
_REJECTIONS = {'UNBOUND_SOURCE', 'UNSUPPORTED_RAW_FIELD', 'UNSUPPORTED_SCALAR',
               'AFTER_CUTOFF', 'AS_OF_UNPROVED'}
_COMPARISON_KEYS = ('entity', 'definition', 'method', 'unit')


class BindingError(ValueError):
    """The snapshot or relation cannot be projected under the supplied authority."""


@dataclass(frozen=True)
class Scope:
    release: str
    task: int
    case_id: str
    snapshot_id: str
    session_id: str


@dataclass(frozen=True)
class SnapshotAuthority:
    """The private caller supplies these independently of the snapshot or model."""

    scope: Scope
    cutoff: str
    sha256: str


@dataclass(frozen=True)
class Reference:
    reference_id: str
    group: str
    source: str
    source_type: str
    document_digest: str
    path: tuple
    metric: str
    value: int | float
    annotation: Annotation
    collection_attested: bool
    receipt_json: str


@dataclass(frozen=True)
class EvidenceState:
    authority: SnapshotAuthority
    relation_id: str
    b2_status: str
    b2_reason: str
    b2_core_status: str | None
    sides: tuple[tuple[str, ...], tuple[str, ...]]
    references: tuple[Reference, ...]
    rejected: tuple[tuple[str, str], ...]
    evidence_group_count: int
    independent_observation_count: int | str
    observation_identity: str
    value_comparability: str
    conflict_status: str
    true_value_status: str
    temporal_trend: str
    left_relative_to_right: str | None

    def serialize(self) -> bytes:
        """Private audit serialization, including source locators and receipts."""
        return canonical({'schema_version': 'b12_evidence_state.v1', **asdict(self)}).encode('utf-8')


def _require(condition, reason):
    if not condition:
        raise BindingError(reason)


def _scope_matches(record, scope):
    return isinstance(record, dict) and type(record.get('task')) is int and record == scope


def _finite_number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _valid_id(value):
    return isinstance(value, str) and bool(value.strip())


def _reference(reference_id, record, scope, groups, cutoff):
    _require(_scope_matches(record['scope'], scope), 'REFERENCE_SCOPE_MISMATCH')
    path = record['original_field']
    _require(isinstance(path, list) and path
             and all(_valid_id(x) or (type(x) is int and x >= 0) for x in path), 'BAD_FIELD_PATH')
    source, kind = record['source_object'], record['source_type']
    metric, document = record['metric'], record['document_digest']
    _require(all(map(_valid_id, (source, kind, metric, document))), 'MISSING_SOURCE_POINTER')
    receipt = record['receipt']
    receipt_kind = receipt['kind']
    if receipt_kind == 'synthetic_settled_tool':
        _require(receipt['response_digest'] == document and kind in ('lab', 'psa_trend')
                 and all(_valid_id(receipt[k]) for k in ('request_id', 'tool_call_id'))
                 and _finite_number(receipt['acquired_unix']), 'UNBOUND_TOOL_RECEIPT')
    elif receipt_kind == 'synthetic_card_pin':
        _require(kind == 'card' and source == 'upfront'
                 and receipt['source_sha256'] == document, 'UNBOUND_CARD_RECEIPT')
    else:
        _require(receipt_kind == 'prepared_synthetic_extract_pin' and kind in ('notes', 'mri')
                 and receipt['source_sha256'] == document, 'UNBOUND_EXTRACT_RECEIPT')
    group = digest({'scope': scope, 'type': kind, 'object': source,
                    'document': document, 'path': path, 'metric': metric})
    _require(record['group'] == group and reference_id in groups[group], 'BOUND_GROUP_MISMATCH')
    claim = record['assertion']
    _require(claim['metric'] == metric and type(claim['collection_attested']) is bool, 'INVALID_ASSERTION')
    _require(all(x is None or _valid_id(x) for x in
                 (claim['entity'], claim['definition'], claim['method'], claim['identity'],
                  record['unit'], claim['unit'])), 'INVALID_COMPARISON_KEYS')
    _require(record['unit'] is None or record['unit'] == claim['unit'], 'UNIT_BINDING_MISMATCH')
    observed, reported, available = (_period(claim[k]) for k in ('observed', 'reported', 'available'))
    timing = [_position(period, cutoff) for period in (observed, reported, available)]
    _require(timing[-1] == 'before' and not {'after', 'overlap'}.intersection(timing)
             and all(_position(period, available) != 'after' for period in (observed, reported)),
             'SOURCE_NOT_AS_OF')
    value = record['raw_value']
    _require(_finite_number(value), 'UNSUPPORTED_SCALAR')
    annotation = Annotation(source, document, tuple(path), 'observed', claim['entity'], claim['definition'],
                            claim['method'], record['unit'], claim['identity'], claim['observed'],
                            claim['reported'], claim['available'])
    return Reference(reference_id, group, source, kind, document, tuple(path), metric, value,
                     annotation, claim['collection_attested'], canonical(receipt))


def _bound_sides(relation, snapshot, scope, cutoff):
    sides = relation['references']
    _require(isinstance(sides, list) and len(sides) == 2, 'INVALID_RELATION_SIDES')
    admitted, rejected = {}, {}
    for ids in sides:
        _require(isinstance(ids, list) and ids and all(map(_valid_id, ids))
                 and len(ids) == len(set(ids)), 'INVALID_REFERENCE_IDS')
        for reference_id in ids:
            if reference_id in admitted or reference_id in rejected:
                continue
            record = snapshot['references'][reference_id]
            if record['status'] == _BOUND:
                admitted[reference_id] = _reference(reference_id, record, scope, snapshot['bound_groups'], cutoff)
            else:
                reason = record.get('reason')
                _require(record['status'] == UNKNOWN and reason in _REJECTIONS, 'UNSUPPORTED_REJECTION')
                rejected[reference_id] = reason
    for ids in sides:
        groups = {admitted[k].group for k in ids if k in admitted}
        _require(len(groups) <= 1 and (not groups or all(k in admitted for k in ids)), 'MIXED_RELATION_SIDE')
    return tuple(tuple(ids) for ids in sides), admitted, rejected


def _comparison(left, right, b2_core):
    comparison_keys = [tuple(getattr(ref.annotation, k) for k in _COMPARISON_KEYS) for ref in (left, right)]
    values = zip(*comparison_keys)
    if left.metric != right.metric or any(a and b and a != b for a, b in values):
        comparable = 'INCOMPARABLE'
    else:
        comparable = 'COMPARABLE' if all(all(keys) for keys in comparison_keys) else UNKNOWN

    records = [
        dict(id=ref.reference_id, annotation=ref.annotation, variable=ref.metric,
             value=ref.value, unit=ref.annotation.unit,
             time=_period(ref.annotation.observed) if ref.collection_attested else None)
        for ref in (left, right)
    ]
    core = compare(records)
    if b2_core is not None:
        _require(core == [b2_core], 'B2_COMPARISON_MISMATCH')

    left_time, right_time = (r['time'] for r in records)
    left_id, right_id = (ref.annotation.observation_id for ref in (left, right))
    identity, order = UNKNOWN, None
    if left_time is not None and right_time is not None:
        relative = _position(left_time, right_time)
        if left_id and left_id == right_id:
            if left_time == right_time:
                identity = 'SAME_CONFIRMED'
        elif relative in ('before', 'after') or (left_id and right_id):
            identity = 'DIFFERENT_CONFIRMED'
            order = relative if relative in ('before', 'after') else None
    return identity, comparable, order


def derive_state(snapshot: bytes, authority: SnapshotAuthority, relation_id: str, *,
                 enabled: bool = False) -> EvidenceState | None:
    """Project a relation from an upstream B1/B2 snapshot pinned by private configuration."""
    if enabled is not True:
        return None
    _require(type(snapshot) is bytes and sha256(snapshot).hexdigest() == authority.sha256,
             'SNAPSHOT_SHA256_MISMATCH')
    scope = asdict(authority.scope)
    _require(scope['release'] == 'synthetic-legacy' and type(scope['task']) is int and scope['task'] == 1
             and all(_valid_id(scope[k]) for k in ('case_id', 'snapshot_id', 'session_id')),
             'SYNTHETIC_SCOPE_REQUIRED')
    try:
        data = json.loads(snapshot)
        _require(canonical(data).encode('utf-8') == snapshot, 'NONCANONICAL_B1_B2_SNAPSHOT')
        _require(_scope_matches(data['scope'], scope) and data['case_id'] == scope['case_id']
                 and data['fixture_origin'] == 'B12-supplemental', 'SNAPSHOT_SCOPE_MISMATCH')
        cutoff = _period(authority.cutoff)
        _require(cutoff is not None, 'CUTOFF_REQUIRED')
        relation = data['relations'][relation_id]
        sides, admitted, rejected = _bound_sides(relation, data, scope, cutoff)
        core = relation['core_comparison']
        status, reason = relation['status'], relation['reason']
        _require(_valid_id(status) and _valid_id(reason), 'INVALID_B2_CLASSIFICATION')
        _require(not rejected or (status == UNKNOWN and core is None), 'B2_REJECTED_SOURCE_USED')
        _require(status != 'METRIC_OR_FIELD_MISREAD'
                 and reason != 'DIVERGENT_ANNOTATIONS_FOR_ONE_BOUND_FIELD', 'CONFLICTING_B2_AUTHORITY')
        identity, comparable, order, conflict = UNKNOWN, UNKNOWN, None, UNKNOWN
        groups = {}
        for ref in admitted.values():
            prior = groups.setdefault(ref.group, ref)
            _require((prior.value, prior.annotation, prior.collection_attested)
                     == (ref.value, ref.annotation, ref.collection_attested), 'CONFLICTING_GROUP_ASSERTIONS')
        if not rejected:
            left, right = (admitted[ids[0]] for ids in sides)
            # The B2 core may be anchored to a non-first alias for one side.
            if core is not None:
                _require(core['left'] in sides[0] and core['right'] in sides[1], 'B2_POINTER_MISMATCH')
                left, right = admitted[core['left']], admitted[core['right']]
            identity, comparable, order = _comparison(left, right, core)
            if identity == 'DIFFERENT_CONFIRMED' or comparable == 'INCOMPARABLE':
                conflict = 'NOT_APPLICABLE'
            elif identity == 'SAME_CONFIRMED' and comparable == 'COMPARABLE':
                conflict = 'NO_CONFLICT' if left.value == right.value else 'CONFIRMED_UNRESOLVED'
                expected_b2 = {'NO_CONFLICT': 'SAME_OBSERVATION_SAME_VALUE',
                               'CONFIRMED_UNRESOLVED': 'SAME_OBSERVATION_CONFLICT_UNRESOLVED'}[conflict]
                binding_matches = status == expected_b2 and core is not None
                alias_only = status == 'SAME_BOUND_EVIDENCE' and left.group == right.group and core is None
                _require(binding_matches or alias_only, 'B2_SAME_OBSERVATION_UNCERTIFIED')
        observations = {'SAME_CONFIRMED': 1, 'DIFFERENT_CONFIRMED': 2}.get(identity, UNKNOWN)
        return EvidenceState(authority, relation_id, status, reason,
                             core['status'] if core is not None else None, sides, tuple(admitted.values()),
                             tuple(rejected.items()), len(groups), observations, identity, comparable,
                             conflict, 'UNRESOLVED' if conflict == 'CONFIRMED_UNRESOLVED' else UNKNOWN,
                             UNKNOWN, order)
    except BindingError:
        raise
    except (KeyError, ValueError, TypeError, IndexError, AttributeError) as exc:
        raise BindingError('MALFORMED_B1_B2_RELATION') from exc


def restore_state(serialized: bytes, snapshot: bytes, authority: SnapshotAuthority, relation_id: str, *,
                  enabled: bool = False) -> EvidenceState | None:
    """Re-derive from the original authority before accepting a serialized copy."""
    state = derive_state(snapshot, authority, relation_id, enabled=enabled)
    if state is not None:
        _require(state.serialize() == serialized, 'PROJECTION_ROUND_TRIP_MISMATCH')
    return state


def legacy_assertions(state: EvidenceState, reference_aliases: dict[str, str]) -> dict:
    """Map the measurable fields only, using the visible IDs from the same request."""
    _require(not state.rejected and state.references
             and set(reference_aliases) == {ref.reference_id for ref in state.references}
             and all(map(_valid_id, reference_aliases.values()))
             and len(set(reference_aliases.values())) == len(reference_aliases), 'MISSING_VISIBLE_SOURCE_BINDING')
    # The old contract has no enum for proven incomparability alone.
    _require(state.value_comparability != 'INCOMPARABLE', 'NO_LEGACY_INCOMPARABLE_ENUM')
    groups = {}
    for ref in state.references:
        row = groups.setdefault(ref.group, {'value': ref.value, 'source_ids': []})
        row['source_ids'].append(reference_aliases[ref.reference_id])
    conflict = {
        'CONFIRMED_UNRESOLVED': 'same_observation_conflict_unresolved',
        'NO_CONFLICT': 'same_observation_consistent',
    }.get(state.conflict_status, 'distinct_observations'
          if state.observation_identity == 'DIFFERENT_CONFIRMED' else UNKNOWN)
    return {'measurements': list(groups.values()), 'evidence_group_count': state.evidence_group_count,
            'independent_observation_count': state.independent_observation_count, 'conflict_status': conflict,
            'trend_status': UNKNOWN}


def first_legacy_mismatch(state: EvidenceState, parsed: dict, reference_aliases: dict[str, str]) -> str | None:
    """Shadow check of a strictly parsed legacy answer. The caller retains the raw evidence."""
    expected = legacy_assertions(state, reference_aliases)
    measurements = parsed.get('measurements')
    if not isinstance(measurements, list):
        return 'measurements'
    source_ids = [source_id for item in measurements for source_id in item['source_ids']]
    if len(source_ids) != len(set(source_ids)):
        return 'measurements'
    # The strict parser returns Decimal for JSON fractions. Compare exact received numbers.
    def rows(items):
        return {frozenset(item['source_ids']): Decimal(str(item['value'])) for item in items}
    if rows(measurements) != rows(expected['measurements']):
        return 'measurements'
    return next((key for key in expected if key != 'measurements' and parsed.get(key) != expected[key]), None)
