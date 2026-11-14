"""Whole runner/real stdio integration and guarded consumer negative cases."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import pytest
from langchain_core.messages import ToolMessage
from chimera_agent_baseline import guarded_runtime, run as runner
from chimera_agent_baseline.agent import form_fill, graph
from chimera_agent_baseline.case_loader import load_cases
from chimera_agent_baseline.information_access import AccessDenied
from tools.validate_guarded_runner import fixture_config, validate


@pytest.mark.parametrize('task', [1, 2, 3])
@pytest.mark.parametrize('workflow', ['react', 'planned_react'])
def test_real_runner_mcp_formfill_is_case_bound_and_resumable(monkeypatch, tmp_path, workflow, task):
    monkeypatch.setenv('CHIMERA_OUTPUT_DIR', str(tmp_path / 'output'))
    monkeypatch.setenv('CHIMERA_STRICT_RUNTIME', '1')
    def forbidden(*args, **kwargs):
        pytest.fail('Unverified consumer attempted a filesystem fallback')
    monkeypatch.setattr(graph, '_build_t1_fewshot', forbidden)
    monkeypatch.setattr(form_fill, '_load_case_fields', forbidden)
    monkeypatch.setattr(form_fill, '_load_weight_table', forbidden)
    asyncio.run(validate(tmp_path, workflow, task))


def prepare(tmp_path):
    cfg, source = fixture_config(tmp_path)
    query = load_cases(source, task=1)[0]
    output = Path(cfg.paths.output_dir) / 'task1/A'
    path, access, bound = guarded_runtime.prepare_case(cfg, source, 1, query, output)
    return cfg, source, query, output, access


@pytest.mark.parametrize('change', ['run_id', 'budget_units', 'max_attempts', 'source', 'prompt', 'agent'])
def test_resume_refuses_changed_identity_source_or_policy(tmp_path, change):
    cfg, source, query, output, access = prepare(tmp_path)
    if change in ('run_id', 'budget_units', 'max_attempts'):
        cfg.agent.information_access[change] = 'other' if change == 'run_id' else 9
    elif change == 'source':
        clinical = next((source / 'A').glob('*clinical-data.json'))
        clinical.write_text(clinical.read_text() + '\n')
    elif change == 'prompt':
        prompt = source / 'A/prompt.json'
        raw = json.loads(prompt.read_text()); raw['psa'] = 9
        prompt.write_text(json.dumps(raw))
    else:
        cfg.agent.max_model_turns = 6
    with pytest.raises(ValueError, match='differs'):
        guarded_runtime.prepare_case(cfg, source, 1, query, output)
    assert access.snapshot()['spent_units'] == 0


def test_guarded_resume_rejects_unguarded_completion_before_creating_ledger(tmp_path):
    cfg, source = fixture_config(tmp_path)
    output = Path(cfg.paths.output_dir) / 'task1/A'; output.mkdir(parents=True)
    (output / 'prediction.json').write_text('{}')
    with pytest.raises(ValueError, match='valid receipt'):
        guarded_runtime.verify_completed(cfg, source, 1, load_cases(source, task=1)[0], output)
    assert not (output / 'information_access.sqlite').exists()


def test_unsafe_configuration_rejected_before_model_load(monkeypatch, tmp_path):
    cfg, source = fixture_config(tmp_path)
    cfg.agent.predictor.enabled = True
    monkeypatch.setattr(runner, 'load_model', lambda *_: pytest.fail('model must not load'))
    with pytest.raises(ValueError, match='predictor/modality'):
        asyncio.run(runner.run_agent(cfg))


@pytest.mark.parametrize('content,name,status', [
    ('{"case_id":"A","radiology_report":"fabricated"}', 'get_mri_report', 'success'),
    ('{"case_id":"B","radiology_report":"wrong case"}', 'get_mri_report', 'success'),
    ('{"case_id":"A"}', 'unknown_tool', 'success'),
    ('Error: backend failed', 'get_mri_report', 'error'),
])
def test_ungranted_forged_or_unexpected_tool_result_fails(tmp_path, content, name, status):
    *_, access = prepare(tmp_path)
    with pytest.raises(AccessDenied):
        guarded_runtime.verify_messages(access, [ToolMessage(content=content, name=name, status=status, tool_call_id='x')])


def test_known_policy_denial_marked_and_missing_data_not_revealed(tmp_path):
    *_, access = prepare(tmp_path)
    denial = ToolMessage(content='Error: information_access:information_budget_exhausted',
                         name='get_lab_results', status='error', tool_call_id='x')
    empty = ToolMessage(content='{"case_id":"A","note":"No data"}',
                        name='get_pathology_report', tool_call_id='y')
    guarded_runtime.verify_messages(access, [denial, empty])
    assert denial.additional_kwargs['information_access_expected_denial']
    assert not empty.additional_kwargs['information_access_clinical_success']
    assert not access.snapshot()['visible_sections']


def test_guarded_invalid_form_never_defaults_to_diagnosis(monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage
    class BrokenModel:
        def invoke(self, messages):
            return AIMessage(content='invalid')
    monkeypatch.setenv('CHIMERA_OUTPUT_DIR', str(tmp_path))
    monkeypatch.setenv('CHIMERA_STRICT_RUNTIME', '0')
    fill = form_fill.make_form_fill_node(BrokenModel(), max_retries=1, postprocess=False)
    with pytest.raises(RuntimeError, match='legacy decision recovery'):
        fill(dict(messages=[], case_id='A', task=1, information_access={}))


def test_concurrent_runners_cannot_write_same_output(tmp_path):
    with guarded_runtime.output_lease(tmp_path):
        with pytest.raises(RuntimeError, match='active runner'):
            with guarded_runtime.output_lease(tmp_path):
                pytest.fail('duplicate writer admitted')
    with guarded_runtime.output_lease(tmp_path):
        pass


def test_completed_receipt_detects_prediction_and_ledger_tampering(tmp_path):
    cfg, source, query, output, access = prepare(tmp_path)
    (output / 'prediction.json').write_text('{"fixture": true}')
    (output / 'trace.json').write_text('{"status": "success"}')
    guarded_runtime.save_receipt(access, output)
    guarded_runtime.verify_completed(cfg, source, 1, query, output)
    original = (output / 'prediction.json').read_text()
    (output / 'prediction.json').write_text('{}')
    with pytest.raises(ValueError, match='receipt differs'):
        guarded_runtime.verify_completed(cfg, source, 1, query, output)
    (output / 'prediction.json').write_text(original)
    with pytest.raises(AccessDenied):
        access.execute('get_mri_report', 'B', lambda: pytest.fail('cross case fetch'))
    with pytest.raises(ValueError, match='receipt differs'):
        guarded_runtime.verify_completed(cfg, source, 1, query, output)


@pytest.mark.parametrize('task,question', [
    (1, 'do you recommend biopsy'),
    (2, 'What is your treatment recommendation'),
    (3, 'predict this patient\'s time to biochemical recurrence'),
])
def test_official_prompt_without_task_uses_directory_task(monkeypatch, tmp_path, task, question):
    cfg, source = fixture_config(tmp_path, task=task)
    path = source / 'A/prompt.json'
    raw = json.loads(path.read_text()); raw.pop('task'); path.write_text(json.dumps(raw))
    query = load_cases(source, task=task)[0]
    _, access, bound = guarded_runtime.prepare_case(cfg, source, task, query, tmp_path / 'bound')
    assert f'Task: {task}' in bound['context']
    assert question in bound['context']
    if task != 1:
        assert 'do you recommend biopsy?' not in bound['context']
    assert access.snapshot()['spent_units'] == 0
    assert 'task' not in json.loads(path.read_text())  # Original input stays untouched.


def test_retry_schedule_can_change_without_resetting_case_ledger(tmp_path):
    cfg, source, query, output, access = prepare(tmp_path)
    cfg.agent.retry_failed = True
    _, resumed, _ = guarded_runtime.prepare_case(cfg, source, 1, query, output)
    assert resumed.binding == access.binding
    assert resumed.snapshot() == access.snapshot()


def test_changed_effective_system_prompt_refuses_old_session(tmp_path):
    cfg, source, query, output, access = prepare(tmp_path)
    query['_system_prompt_digest'] = 'different_effective_prompt'
    with pytest.raises(ValueError, match='session differs'):
        guarded_runtime.prepare_case(cfg, source, 1, query, output)
