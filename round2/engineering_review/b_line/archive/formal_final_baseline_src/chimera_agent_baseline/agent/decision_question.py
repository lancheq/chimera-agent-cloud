"""Experimental question framing; no diagnostic rule or production integration."""
from chimera_agent_baseline.agent.evidence_review import (
    TYPED_REVIEW_SYSTEM, assess_review, make_review_payload, model_json, validate_review)

ARMS = ('usual_review', 'decision_question_review')
QUESTION_FOCUS = (
    'Before recommending a decision, identify the unresolved clinical question at this encounter from the '
    'supplied primary records. Distinguish obtaining an initial tissue diagnosis, repeat/confirmatory sampling '
    'after an established diagnosis, and staging/treatment planning. In reason_for_revision, explain what '
    'information another biopsy would add and how it would or would not change the next management step. '
    'Cancer suspicion, an established diagnosis, need for treatment, and need for another biopsy are different '
    'propositions; do not use one as an automatic substitute for another. A prior positive biopsy alone '
    'does not settle the answer in either direction. Support patient-specific facts with supplied primary '
    'quotes. Do not assert lesion newness/growth, histological upgrade, or a particular elapsed biopsy interval '
    'without supporting records. Identify unresolved comparisons as unknown rather than assuming stability '
    'or progression. Unknown information alone is not affirmative evidence to reverse the original decision. '
    'Do not add a guideline, decision threshold, treatment status, or fixed mapping from pathway to yes/no.'
)


def make_question_payload(packet, arm):
    if arm not in ARMS: raise ValueError('Unknown decision-question experiment arm')
    payload, records = make_review_payload(packet, 'observed_evidence_review', 'typed_v2')
    if arm == 'decision_question_review': payload['review_focus'] = QUESTION_FOCUS
    return payload, records


def adjudicate_question(packet, arm, review_model, audit_model):
    payload, _ = make_question_payload(packet, arm)
    reviewed = model_json(review_model, TYPED_REVIEW_SYSTEM, payload,
                          lambda raw: validate_review(packet, raw))
    result = assess_review(packet, 'observed_evidence_review', reviewed, audit_model, 'typed_v2')
    result['experiment_arm'] = arm
    return result
