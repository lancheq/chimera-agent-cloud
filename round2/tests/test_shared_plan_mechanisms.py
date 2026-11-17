from tools.review_shared_plan_mechanisms import classify_pending, history, compare_states


def budget(spent=3,attempts=3):
    return dict(max_attempts=6,admitted_attempts=attempts,budget_units=3,spent_units=spent)


def test_budget_blocked_pending_action_is_not_counted_as_saved_feasible_evidence():
    action=dict(tool='get_previous_notes',purpose='review')
    result=classify_pending(action,['psa_trend'],budget(),3)
    assert result['category']=='clinical_budget_blocked' and not result['individually_feasible']


def test_same_section_is_known_but_unknown_section_remains_only_a_candidate():
    action=dict(tool='get_previous_notes',purpose='review')
    assert classify_pending(action,['previous_notes'],budget(),3)['category']=='already_visible_clinical_section'
    result=classify_pending(action,[],budget(spent=2),2)
    assert result['category']=='affordable_unseen_clinical_section' and result['new_patient_units']==1
    assert 'clinically_redundant' not in result


def test_knowledge_is_not_a_new_patient_information_unit_and_attempt_cap_still_applies():
    action=dict(tool='search_guidelines',query='x',purpose='review')
    assert classify_pending(action,[],budget(),3)['new_patient_units']==0
    assert classify_pending(action,[],budget(),3,['x'])['category']=='knowledge_query_already_seen'
    assert classify_pending(action,[],budget(attempts=6),3)['category']=='attempt_or_action_cap'


def test_early_stop_is_defined_before_decision_empties_plan():
    actions=[dict(tool='first'),dict(tool='next')]
    events=[dict(kind='policy_decision',policy_turn=1,decision=dict(action='stop',remaining_plan=[],reason='done'))]
    decisions,pending=history(events,actions)
    assert pending==actions[1:] and decisions[0]['before']==actions[1:]


def test_identical_clinical_evidence_does_not_imply_identical_model_request():
    fixed=dict(clinical_hashes={'radiology_report':'same'},knowledge_payload_hashes=[],diagnosis_request_digest='first',diagnosis_options={'seed':1},information_units=1)
    adaptive=fixed|dict(diagnosis_request_digest='second')
    comparison=compare_states(fixed,adaptive)
    assert comparison['same_successful_clinical_payloads'] and not comparison['same_exact_diagnosis_request']
