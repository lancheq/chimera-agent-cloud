"""Binding and consumption of already reviewed judgments. No review or model calls."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Callable, Literal

from pydantic import ValidationError

from chimera_agent_baseline.runtime_audit import RuntimeFatal, canonical, content_digest


Mechanism = Literal['external_graph', 'internal_formfill']


@dataclass(frozen=True)
class DraftArtifact:
    text_digest: str
    process_digest: str
    source_type: Literal['generated_opinion'] = 'generated_opinion'


@dataclass(frozen=True)
class ReflectionArtifact:
    mechanism: Mechanism
    draft: DraftArtifact
    output_digest: str
    status: Literal['disabled', 'not_triggered', 'no_issue', 'detected', 'unreviewed', 'error']


@dataclass(frozen=True)
class Subject:
    run_id: str
    task: int
    case_id: str
    profile_digest: str
    draft: DraftArtifact
    base_digest: str
    source_digest: str
    process_digest: str


@dataclass(frozen=True)
class Citation:
    record_id: str
    quote: str
    claim: str


@dataclass(frozen=True)
class CorrectionProposal:
    subject: Subject
    mechanism: Mechanism
    candidate_json: str
    candidate_digest: str
    changed_fields: tuple[str, ...]
    reason: str
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class AuditVerdict:
    subject: Subject
    mechanism: Mechanism
    proposal_digest: str
    candidate_digest: str
    status: Literal['accepted', 'rejected', 'unknown', 'not_requested', 'error']
    reviewed_fields: tuple[str, ...]
    method: str
    blocking_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Review:
    proposal: CorrectionProposal
    verdict: AuditVerdict | None


@dataclass(frozen=True)
class ReviewBatch:
    status: Literal['disabled', 'not_triggered', 'no_issue', 'detected', 'error']
    reviews: tuple[Review, ...] = ()


@dataclass(frozen=True)
class OverwriteRule:
    stage: str
    field: str
    before_json: str
    after_json: str
    rule_id: str


@dataclass(frozen=True)
class Context:
    subject: Subject
    initial_json: str
    upfront: str | None
    artifacts: tuple[ReflectionArtifact, ...]


@dataclass(frozen=True)
class SyntheticReview:
    """Injected by trusted Python tests in a verified synthetic entry scope only."""

    review: Callable[[Context], ReviewBatch]
    overwrites: tuple[OverwriteRule, ...] = ()
    method: str = 'synthetic_contract_stub'


@dataclass(frozen=True)
class Selection:
    status: str
    source: str
    selected_json: str
    subject: Subject
    proposal_digest: str | None = None
    verdict_digest: str | None = None
    reason: str | None = None

    @property
    def judgment(self):
        return json.loads(self.selected_json)

    @property
    def digest(self):
        return content_digest(self.judgment)


@dataclass(frozen=True)
class FinalReceipt:
    status: str
    selection_digest: str
    consumption_digest: str | None
    fields: tuple[dict, ...]


def draft_artifact(messages, text):
    return DraftArtifact(content_digest(text), content_digest(messages))


def validate_judgment(task, schema, value):
    # The normal parser tolerates defaults and extra/model-only fields. For
    # G1R the complete object checked by the reviewer must be exactly the
    # object assigned. Programmatic socket metadata is never part of it.
    if not isinstance(value, dict):
        raise ValueError('Judgment must be an object')
    checked = schema.model_validate(value).model_dump(mode='json')
    if canonical(checked) != canonical(value):
        raise ValueError('Judgment is incomplete or has coerced/unknown fields')
    if judgment_label(task, value) is None or not isinstance(value.get('free_text'), str) or not value['free_text'].strip():
        raise ValueError('Missing required judgment decision or rationale')
    if task in (1, 2) and value.get('confidence') is None:
        raise ValueError('Missing required confidence')
    return checked


def judgment_label(task, value):
    if task == 2:
        return (value.get('treatment_recommendation') or {}).get('primary')
    return value.get('biopsy_decision' if task == 1 else 'event')


def resolve(context, batch, authority, schema):
    """Select a full object. The only allowed source is this run's upfront record."""
    subject = context.subject
    try:
        base = validate_judgment(subject.task, schema, json.loads(context.initial_json))
        if content_digest(base) != subject.base_digest:
            raise ValueError('Initial judgment binding differs')
    except (ValueError, ValidationError) as error:
        raise RuntimeFatal('failed_no_valid_initial') from error

    proposal = verdict = None

    def keep(status, reason=None):
        return Selection(status, 'initial', context.initial_json, subject,
                         content_digest(asdict(proposal)) if proposal else None,
                         content_digest(asdict(verdict)) if verdict else None, reason)

    if type(batch) is not ReviewBatch or type(batch.reviews) is not tuple or batch.status not in (
            'disabled', 'not_triggered', 'no_issue', 'detected', 'error'):
        return keep('error_fallback_initial', 'invalid_review_batch')
    if len(batch.reviews) > 1:
        return keep('ambiguous_candidates')
    if not batch.reviews:
        status = {'detected': 'detected_no_executable_proposal',
                  'error': 'error_fallback_initial'}.get(batch.status, batch.status)
        return keep(status)
    if batch.status != 'detected':
        return keep('error_fallback_initial', 'review_batch_state_conflict')
    review = batch.reviews[0]
    if type(review) is not Review or type(review.proposal) is not CorrectionProposal:
        return keep('invalid_proposal', 'malformed_proposal')
    proposal = review.proposal
    verdict = review.verdict
    if verdict is not None and type(verdict) is not AuditVerdict:
        verdict = None
        return keep('error_fallback_initial', 'malformed_audit')
    try:
        candidate = validate_judgment(subject.task, schema, json.loads(proposal.candidate_json))
    except (ValueError, ValidationError, TypeError):
        return keep('invalid_proposal')

    fields = tuple(sorted(key for key in base if canonical(base[key]) != canonical(candidate[key])))
    if (proposal.subject != subject or proposal.mechanism not in ('external_graph', 'internal_formfill')
            or proposal.candidate_digest != content_digest(candidate)
            or proposal.changed_fields != fields
            or not isinstance(proposal.reason, str) or not proposal.reason.strip()
            or type(proposal.citations) is not tuple or not proposal.citations or not context.upfront
            or subject.source_digest != upfront_digest(context.upfront)
            or any(type(c) is not Citation or c.record_id != 'rendered_upfront'
                   or not isinstance(c.quote, str) or not c.quote.strip() or c.quote not in context.upfront
                   or not isinstance(c.claim, str) or not c.claim.strip()
                   for c in proposal.citations)):
        return keep('binding_or_authority_rejected', 'proposal_identity_fields_or_source')
    if authority is None or verdict is None:
        return keep('blocked_missing_authorization')
    if (verdict.subject != subject or verdict.mechanism != proposal.mechanism
            or verdict.candidate_digest != proposal.candidate_digest
            or verdict.proposal_digest != content_digest(asdict(proposal))
            or verdict.method != authority.method):
        return keep('binding_or_authority_rejected', 'audit_identity_or_role')
    if verdict.status == 'error':
        return keep('error_fallback_initial')
    if verdict.status == 'rejected' or verdict.blocking_codes:
        return keep('audit_rejected')
    if verdict.status != 'accepted':
        return keep('blocked_missing_authorization')
    if verdict.reviewed_fields != tuple(sorted(candidate)):
        return keep('binding_or_authority_rejected', 'coverage_incomplete')
    return Selection('accepted_candidate' if fields else 'executed_no_delta',
                     'authorized_candidate', canonical(candidate), subject,
                     content_digest(asdict(proposal)), content_digest(asdict(verdict)))


def upfront_digest(upfront):
    # Permission is bound by code to the actual rendered initial human message.
    # Tool returns and generated opinions cannot assert that permission.
    return content_digest({'record_id': 'rendered_upfront',
                           'permission': 'entry_original_upfront_record', 'text': upfront})


def upfront_record(text):
    """Return the rendered record section, excluding instructions and tool descriptions."""
    if not isinstance(text, str):
        return None
    _, marker, remainder = text.partition('\nClinical data (read-only — values loaded from the record):\n')
    record, end, _ = remainder.partition('\nInformation you can request via tools')
    return (record.strip() or None) if marker and end else None


_MISSING = object()


def _leaves(value, path=()):
    if isinstance(value, dict):
        for key, child in sorted(value.items()):
            yield from _leaves(child, (*path, key))
    else:
        yield path, value


def _value(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def _projection(task, path, weight_keys):
    key = path[0]
    if key == 'free_text':
        return ('reasoning',) if task == 3 else ('reasoning', *path)
    if key == 'variable_weights' and weight_keys is not None and path[1] not in weight_keys:
        return None
    if key in ('confidence', 'variable_weights'):
        return ('reasoning', *path)
    if task == 1 and key == 'biopsy_decision' or task == 2 and path == ('treatment_recommendation', 'primary'):
        return ('decision',)
    if task == 3 and key in ('event', 'months_to_recurrence'):
        return ('decision', *path)
    return None  # Defined internal-only fields (repeat_test and T2 plan details).


def _field_change(stage, field, before, after, rules):
    if before is _MISSING and after is _MISSING:
        return None
    both_present = before is not _MISSING and after is not _MISSING
    if both_present and canonical(before) == canonical(after):
        return None
    rule = next((rule for rule in rules if both_present and stage == rule.stage and field == rule.field
                 and canonical(before) == rule.before_json and canonical(after) == rule.after_json), None)
    return {'stage': stage, 'status': 'dropped' if after is _MISSING else
            'authorized_overwrite' if rule else 'unauthorized_overwrite',
            'before_present': before is not _MISSING, 'after_present': after is not _MISSING,
            'before': None if before is _MISSING else before,
            'after': None if after is _MISSING else after, 'rule_id': rule.rule_id if rule else None}


def finalize_reflection(task, stages, sockets, rules=(), *, weight_keys=None):
    selections = [event for event in stages if event['stage'] == 'reflection_selection']
    if not selections:
        return None
    selected = selections[-1]
    consumed = [event for event in stages if event['stage'] == 'reflection_formfill_consumed']
    if (len(selections) != 1 or len(consumed) != 1
            or consumed[0]['seq'] <= selected['seq']
            or consumed[0].get('selection_digest') != selected['digest']
            or consumed[0]['after_digest'] != selected['after_digest']):
        return FinalReceipt('consumption_contract_failed', selected['digest'], None, ())
    actual = consumed[0]
    fields = []
    terminal = 'finalized_retained' if selected['selected_source'] == 'authorized_candidate' else 'finalized_initial'
    if selected.get('status') in ('invalid_proposal', 'error_fallback_initial', 'binding_or_authority_rejected'):
        terminal = 'finalized_initial_with_error'
    boundaries = [(event['stage'], event['after'].get('structured_response', {})
                   if event['stage'] == 'graph_output' else event['after'])
                  for event in stages if event['seq'] > actual['seq'] and event['stage'] != 'verified_judgment']
    required = {'assembled_output', 'graph_output', 'prediction_file'}
    if task in (1, 2):
        required.add('reasoning_align')
    if not required <= {stage for stage, _ in boundaries}:
        terminal = 'consumption_contract_failed'
    for path, initial in _leaves(selected['after']):
        field = '/' + '/'.join(p.replace('~', '~0').replace('/', '~1') for p in path)
        projection = _projection(task, path, weight_keys)
        previous = initial
        history = []
        for stage, snapshot in boundaries:
            if stage == 'reasoning_align' and (not projection or projection[0] != 'reasoning'):
                continue
            value = _value(snapshot, path)
            change = _field_change(stage, field, previous, value, rules)
            if change is not None:
                history.append(change)
            previous = value
        final = _value(sockets, projection) if projection else previous
        if projection:
            change = _field_change('final_sockets', field, previous, final, rules)
            if change is not None:
                history.append(change)
        if any(item['status'] != 'authorized_overwrite' for item in history):
            terminal = 'consumption_contract_failed'
        elif history and terminal == 'finalized_retained':
            terminal = 'accepted_then_overridden'
        fields.append({'field': field, 'projection': projection, 'selected': initial,
                       'final': None if final is _MISSING else final,
                       'final_present': final is not _MISSING, 'history': history,
                       'status': history[-1]['status'] if history else 'retained' if projection else 'internal_only'})
    return FinalReceipt(terminal, selected['digest'], actual['digest'], tuple(fields))
