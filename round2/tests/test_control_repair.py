"""Output-contract faults, bounded recovery, and preserved permission boundary."""
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError
from langchain_core.messages import AIMessage, HumanMessage
from omegaconf import OmegaConf

from chimera_agent_baseline import shared_plan as sp
from chimera_agent_baseline.control_schema import control_schema
from chimera_agent_baseline.model_context import LlamaContextGuard
from test_shared_plan import setup, acquire


class Sequence:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, AIMessage) else AIMessage(content=json.dumps(value))


def repaired_items(tmp_path, model):
    items = list(setup(tmp_path))
    items[0].agent.shared_plan.control_interface_version = 2
    items[0].agent.shared_plan.diagnosis_attempts = 2
    items[4] = model
    def token_guard(messages, limit, **kwargs):
        return dict(fits=True, max_new_tokens=limit, template_options=kwargs)
    items[6] = sp.RecordedModel(model, items[0], items[5], token_guard)
    return items


def tools():
    return [SimpleNamespace(name=n) for n in ('get_mri_report', 'search_guidelines')]


@pytest.mark.parametrize('field', ['actions', 'remaining_plan'])
def test_backend_schema_matches_tool_permissions_and_validates_good_outputs(field):
    schema = control_schema(field, tools(), 2)
    Draft202012Validator.check_schema(schema)
    value = {field: [dict(tool='search_guidelines', query='synthetic guideline', purpose='Check guidance'),
                     dict(tool='get_mri_report', query=None, purpose='Read report')]}
    if field == 'remaining_plan':
        value['reason'] = 'Review permitted evidence'
    Draft202012Validator(schema).validate(value)


@pytest.mark.parametrize('action', [
    dict(tool='get_hidden_features', query=None, purpose='x'),
    dict(tool='get_mri_report', query='switch context', purpose='x'),
    dict(tool='get_mri_report', query=None, purpose='x', case_id='OTHER'),
    dict(tool='get_mri_report', query=None),
    dict(tool='search_guidelines', query='', purpose='x'),
    dict(tool='search_guidelines', query=None, purpose='x'),
])
def test_backend_schema_rejects_the_observed_missing_fields_and_permission_bypasses(action):
    with pytest.raises(ValidationError):
        Draft202012Validator(control_schema('actions', tools(), 2)).validate({'actions': [action]})


def test_schema_encodes_lengths_caps_and_refuses_legacy_contradiction():
    validator = Draft202012Validator(control_schema('remaining_plan', tools(), 1))
    validator.validate(dict(remaining_plan=[], reason='Evidence sufficient'))
    for value in (dict(remaining_plan=[], reason='x' * 513),
                  dict(remaining_plan=[], reason='done', action='execute'),
                  dict(remaining_plan=[dict(tool='get_mri_report', query=None, purpose='x')] * 2, reason='x')):
        with pytest.raises(ValidationError):
            validator.validate(value)


def test_empty_plan_stops_without_duplicated_action_field_or_more_information(tmp_path):
    model = Sequence(dict(remaining_plan=[], reason='Enough observed evidence'))
    items = repaired_items(tmp_path, model)
    state = acquire(tmp_path, items)
    assert items[-1] == ['MRI'] and state['stop_reason'] == 'policy_stop'
    assert items[1].snapshot()['spent_units'] == 1
    opts = model.calls[0][1]
    assert opts['response_format']['json_schema']['strict']
    assert 'remaining_plan' in str(opts['response_format'])
    decision = next(e for e in items[5].events if e['kind'] == 'policy_decision')['decision']
    assert decision['action'] == 'stop' and decision['remaining_plan'] == []


def test_semantic_whitespace_query_remains_rejected_even_if_backend_accepts_string(tmp_path):
    bad = dict(remaining_plan=[dict(tool='search_guidelines', query=' ', purpose='x')], reason='x')
    items = repaired_items(tmp_path, Sequence(bad, bad))
    with pytest.raises(ValueError, match='bounded retries'):
        acquire(tmp_path, items)
    assert items[-1] == ['MRI'] and items[1].snapshot()['admitted_attempts'] == 1


def test_control_recovery_is_bounded_audited_and_replayed_without_new_calls(tmp_path):
    model = Sequence(AIMessage(content='not JSON'), dict(remaining_plan=[], reason='Stop'))
    items = repaired_items(tmp_path, model)
    schema = control_schema('remaining_plan', items[2], 5)
    args = ('policy', 'recover', [HumanMessage(content='Synthetic permitted context')], sp.PolicyPlan, lambda _: None)
    first = items[6].structured(*args, output_schema=schema)
    replay = items[6].structured(*args, output_schema=schema)
    assert first == replay and len(model.calls) == 2
    assert model.calls[1][1]['extra_body']['chat_template_kwargs']['enable_thinking'] is False
    assert model.calls[1][1]['extra_body']['reasoning_budget_tokens'] == 0
    assert all('response_format' in kw for _, kw in model.calls)


def test_truncated_control_is_not_silently_accepted_even_if_json_parses(tmp_path):
    raw = json.dumps(dict(remaining_plan=[], reason='Stop'))
    model = Sequence(AIMessage(content=raw, response_metadata={'finish_reason': 'length'}), AIMessage(content=raw))
    items = repaired_items(tmp_path, model)
    acquire(tmp_path, items)
    assert len(model.calls) == 2 and items[-1] == ['MRI']


def test_unsupported_backend_errors_do_not_fallback_to_unconstrained_generation(tmp_path):
    model = Sequence(RuntimeError('Unsupported schema'))
    items = repaired_items(tmp_path, model)
    with pytest.raises(RuntimeError, match='Unsupported schema'):
        acquire(tmp_path, items)
    assert len(model.calls) == 1 and 'response_format' in model.calls[0][1]


def test_empty_diagnosis_recovers_once_with_same_evidence_and_persists_return(tmp_path):
    model = Sequence(AIMessage(content=''), AIMessage(content='Synthetic assessment'))
    items = repaired_items(tmp_path, model)
    messages = [HumanMessage(content='SYNTHETIC_PAID_OBSERVATION')]
    before = items[1].snapshot()
    response = items[6].diagnostic(messages)
    replay = items[6].diagnostic(messages)
    assert response == replay and response.content == 'Synthetic assessment' and len(model.calls) == 2
    assert model.calls[0][0][0] == model.calls[1][0][0] == messages[0]
    assert model.calls[1][1]['extra_body']['reasoning_budget_tokens'] == 0
    assert items[1].snapshot() == before and not items[-1]
    assert sum(e['kind'] == 'model_return' for e in items[5].events) == 2


def test_exhausted_diagnosis_recovery_produces_failure_not_default_decision(tmp_path):
    model = Sequence(AIMessage(content=''), AIMessage(content='  '))
    items = repaired_items(tmp_path, model)
    with pytest.raises(ValueError, match='bounded output recovery'):
        items[6].diagnostic([HumanMessage(content='Synthetic')])
    assert len(model.calls) == 2 and not (tmp_path / 'prediction.json').exists()


def test_diagnostic_tool_call_does_not_trigger_information_acquisition(tmp_path):
    model = Sequence(AIMessage(content='Use a tool', tool_calls=[dict(id='x', name='get_mri_report', args={})]),
                     AIMessage(content='Synthetic assessment'))
    items = repaired_items(tmp_path, model)
    assert items[6].diagnostic([HumanMessage(content='Synthetic')]).content == 'Synthetic assessment'
    assert not items[-1] and items[1].snapshot()['admitted_attempts'] == 0


def test_legacy_v1_empty_diagnosis_still_fails_without_new_retry(tmp_path):
    items = list(setup(tmp_path));model = Sequence(AIMessage(content=''))
    recorded = sp.RecordedModel(model, items[0], items[5], lambda *_: {})
    with pytest.raises(ValueError, match='Common diagnostic response'):
        recorded.diagnostic([HumanMessage(content='Synthetic')])
    assert len(model.calls) == 1 and 'response_format' not in model.calls[0][1]


def test_context_checker_uses_actual_retry_template_options():
    guard = LlamaContextGuard.__new__(LlamaContextGuard)
    guard.limit = 100;guard.reserve = 1
    seen = []
    def request(path, body):
        seen.append((path, body))
        return {'prompt': 'templated'} if path == '/apply-template' else {'tokens': [1, 2]}
    guard._request = request
    result = guard([HumanMessage(content='Synthetic')], 10, chat_template_kwargs={'enable_thinking': False})
    assert result['fits'] and seen[0][1]['chat_template_kwargs'] == {'enable_thinking': False}


def test_v2_configuration_rejects_unbounded_recovery(tmp_path):
    items = repaired_items(tmp_path, Sequence())
    items[0].agent.shared_plan.diagnosis_attempts = 3
    with pytest.raises(ValueError, match='diagnosis_attempts'):
        sp.validate_config(items[0])
