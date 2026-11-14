from copy import deepcopy
import pytest
from chimera_agent_baseline.agent.evidence_review import (
    assess_review, make_review_payload, make_audit_payload, quantitative_opinion,
    TypedAuditReply, normalize_quote)
from tools.t1_audit_contract_cases import cases
from test_t1_evidence_review import Model, good_audit


def good_typed(payload):
    return {'case_id': payload['case_id'], 'review_digest': payload['review_digest'],
        'audited_decision': payload['proposed_final_output']['biopsy_decision'],
        'factual_support': True, 'decision_reason_consistent': True, 'plan_consistent': True,
        'reviewed_fields': ['biopsy_decision', 'free_text', 'repeat_test', 'variable_weights'],
        'blocking_findings': [], 'nonblocking_notes': []}


def test_legacy_audit_payload_and_output_stay_compatible():
    c = cases()[0]; p = c['packet']
    def audit(payload):
        assert 'process_context' not in payload
        assert 'contract' not in payload
        v = good_audit(payload); v['case_id'] = p['case_id']
        return v
    assert assess_review(p, c['source_arm'], c['proposal'], Model(audit))['accepted']


def test_typed_score_declares_dataset_decision_target_and_not_cancer_risk():
    p = cases()[0]['packet']
    q = quantitative_opinion(p)
    assert q['target'] == 'prostate-biopsy-decision' and q['positive_label'] == 'yes'
    assert 'not cancer' in q['score_meaning']
    for bad in (float('nan'), float('inf'), -.1, 1.1, True, '0.2'):
        with pytest.raises(ValueError):
            quantitative_opinion({**p, 'p_yes': bad})
    with pytest.raises(ValueError):
        quantitative_opinion({**p, 'predictor_decision': 'yes'})


def test_typed_context_preserves_source_arm_only_difference():
    p = cases()[0]['packet']
    a, _ = make_review_payload(p, 'context_only_review', 'typed_v2')
    b, _ = make_review_payload(p, 'observed_evidence_review', 'typed_v2')
    assert {k: v for k,v in a.items() if k != 'primary_records'} == {k: v for k,v in b.items() if k != 'primary_records'}


def test_process_context_verifies_attribution_but_never_becomes_primary_citation():
    c = cases()[0]; auditor = Model(good_typed)
    r = assess_review(c['packet'], c['source_arm'], c['proposal'], auditor, 'typed_v2')
    assert r['accepted']
    payload = auditor.calls[0]
    assert payload['process_context']['original_opinion']['source_type'] == 'generated_original_opinion'
    assert all(x['source_type'] != 'generated_original_opinion' for x in payload['primary_records'])
    bad = deepcopy(c['proposal']); bad['citations'][0]['record_id'] = 'original_opinion'
    auditor = Model(good_typed)
    assert not assess_review(c['packet'], c['source_arm'], bad, auditor, 'typed_v2')['accepted']
    assert not auditor.calls


def test_typed_hash_binds_patient_sources_and_process_opinions():
    c = cases()[0]; p = c['packet']; r = c['proposal']
    _, records = make_review_payload(p, c['source_arm'], 'typed_v2')
    a = make_audit_payload(p, records, p['original_full_output'], r, 'typed_v2')
    changed = deepcopy(p); changed['original_judgment']['free_text'] += ' Different opinion.'
    b = make_audit_payload(changed, records, p['original_full_output'], r, 'typed_v2')
    assert a['review_digest'] != b['review_digest']
    rec = deepcopy(records); rec[0]['text'] += '\nPSA: 99'
    d = make_audit_payload(p, rec, p['original_full_output'], r, 'typed_v2')
    assert a['review_digest'] != d['review_digest']


@pytest.mark.parametrize('kind', ['wrong_hash', 'missing_weights', 'duplicate_field', 'false_boolean', 'blocking_finding'])
def test_typed_blocking_errors_preserve_entire_original(kind):
    c = cases()[0]
    def verdict(payload):
        v = good_typed(payload)
        if kind == 'wrong_hash': v['review_digest'] = 'other'
        if kind == 'missing_weights': v['reviewed_fields'].remove('variable_weights')
        if kind == 'duplicate_field': v['reviewed_fields'].append('free_text')
        if kind == 'false_boolean': v['plan_consistent'] = False
        if kind == 'blocking_finding': v['blocking_findings'] = [{'code': 'target_misinterpretation',
            'field': 'free_text', 'claim': 'Synthetic bad claim.', 'explanation': 'Synthetic reason.'}]
        return v
    r = assess_review(c['packet'], c['source_arm'], c['proposal'], Model(verdict), 'typed_v2')
    assert not r['accepted'] and r['final_output'] == c['packet']['original_full_output']


def test_nonblocking_note_alone_does_not_reject_full_output():
    c = cases()[0]
    def verdict(payload):
        v = good_typed(payload); v['nonblocking_notes'] = ['Clinical uncertainty remains.']
        return v
    assert assess_review(c['packet'], c['source_arm'], c['proposal'], Model(verdict), 'typed_v2')['accepted']


def test_synthetic_cases_have_valid_primary_quotes_without_reference_labels():
    for c in cases():
        _, records = make_review_payload(c['packet'], c['source_arm'], 'typed_v2')
        record_map = {r['record_id']: r['text'] for r in records}
        assert all(normalize_quote(x['quote']) in normalize_quote(record_map[x['record_id']]) for x in c['proposal']['citations'])
        assert 'label' not in c['packet']
