"""Predeclared cohort, grouped statistics, and unknown compute accounting."""
from copy import deepcopy
import pytest
from tools.prepare_shared_plan_study import select_cases,SOURCE
from tools.analyze_shared_plan_study import exact_mcnemar,paired_interval,compute_cost


def cohort(n=6):
    cases=[dict(source=SOURCE,task=1,record_index=i,case_id=str(i),decision_file_present=True,usage='internal_development_exposed') for i in range(n)]
    groups=dict(mappings=[dict(record_index=i,risk_group_id='group-'+str(i)) for i in range(n)])
    return dict(cases=cases),groups


def test_cohort_membership_uses_presence_and_risk_groups_not_target_values():
    pool,groups=cohort()
    first,mapping,n=select_cases(pool,groups,expected=6)
    pool['cases'].append(dict(source=SOURCE,task=1,record_index=6,case_id='unlabeled',decision_file_present=False,usage='internal_development_exposed'))
    second,_,n=select_cases(pool,groups,expected=6)
    assert first==second and n==1 and len(set(mapping.values()))==6


def test_duplicate_groups_or_changed_denominator_require_new_protocol():
    pool,groups=cohort()
    groups['mappings'][1]['risk_group_id']=groups['mappings'][0]['risk_group_id']
    with pytest.raises(ValueError,match='Duplicate conservative'):select_cases(pool,groups,expected=6)
    pool,groups=cohort()
    with pytest.raises(ValueError,match='count differs'):select_cases(pool,groups,expected=5)


def test_exact_paired_statistics_are_symmetric_and_ties_are_not_discordant():
    assert exact_mcnemar(0,0)==1
    assert exact_mcnemar(5,0)==pytest.approx(.0625)
    assert exact_mcnemar(5,1)==exact_mcnemar(1,5)
    assert paired_interval([0,0,0],['A','B','C'],100)==[0,0]
    assert paired_interval([1,1,1],['A','B','C'],100)==[1,1]


def test_unknown_or_lost_response_tokens_are_not_recorded_as_zero():
    events=[dict(kind='model_request',stage='policy')]
    result=compute_cost(events)
    assert result['client_call_attempts']==1 and result['successful_responses']==0
    assert result['input_tokens'] is None and result['output_tokens'] is None
    events.append(dict(kind='model_return',stage='policy',usage=dict(input_tokens=10,output_tokens=4),elapsed_seconds=.1))
    result=compute_cost(events)
    assert result['input_tokens']==10 and result['output_tokens']==4
