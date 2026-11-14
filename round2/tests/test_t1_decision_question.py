from copy import deepcopy
import pytest
from chimera_agent_baseline.agent.decision_question import make_question_payload, adjudicate_question
from chimera_agent_baseline.agent.evidence_review import make_review_payload
from test_t1_evidence_review import Model, packet, reply
from test_t1_typed_audit import good_typed


def test_question_focus_is_the_only_payload_change_with_identical_schema_and_sources():
    p = packet()
    usual, a = make_question_payload(p, 'usual_review')
    focused, b = make_question_payload(p, 'decision_question_review')
    baseline, _ = make_review_payload(p, 'observed_evidence_review', 'typed_v2')
    assert usual == baseline
    assert a == b and a == p['records']
    assert {k:v for k,v in focused.items() if k != 'review_focus'} == usual
    assert set(focused) - set(usual) == {'review_focus'}
    assert 'reference' not in focused and 'label' not in focused


def test_both_arms_use_identical_bound_audit_for_identical_proposals():
    p = packet(); saved = deepcopy(p); audits = []; results = []
    for arm in ('usual_review','decision_question_review'):
        reviewer = Model(reply(p)); auditor = Model(good_typed)
        result = adjudicate_question(p,arm,reviewer,auditor)
        assert len(reviewer.calls) == 1 and len(auditor.calls) == 1
        assert result['accepted'] and result['revised']
        assert result['experiment_arm'] == arm
        assert result['final_output']['biopsy_decision'] == 'no'
        assert 'review_focus' not in auditor.calls[0]
        audits.append(auditor.calls[0]); results.append(result['final_output'])
    assert audits[0] == audits[1] and results[0] == results[1] and p == saved


@pytest.mark.parametrize('arm',['usual_review','decision_question_review'])
def test_invalid_generation_cannot_fall_back_to_a_default_diagnosis(arm):
    p = packet(); saved = deepcopy(p); bad = reply(p); bad['judgment']['biopsy_decision'] = None
    reviewer = Model(bad); auditor = Model(good_typed)
    with pytest.raises(ValueError,match='bounded attempts'):
        adjudicate_question(p,arm,reviewer,auditor)
    assert len(reviewer.calls) == 2 and not auditor.calls and p == saved


def test_focus_does_not_bypass_quote_or_semantic_audit():
    p = packet(); bad = reply(p); bad['citations'][0]['quote'] = 'Fabricated source fact.'
    auditor = Model(good_typed)
    r = adjudicate_question(p,'decision_question_review',Model(bad),auditor)
    assert not r['accepted'] and r['final_output'] == p['original_full_output'] and not auditor.calls
    def reject(payload):
        v = good_typed(payload); v['plan_consistent'] = False
        return v
    r = adjudicate_question(p,'decision_question_review',Model(reply(p)),Model(reject))
    assert not r['accepted'] and r['final_output'] == p['original_full_output']
