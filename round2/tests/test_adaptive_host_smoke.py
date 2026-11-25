"""Adaptive v2 on the root entry, with real guarded stdio and a mock SDK transport."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from shutil import copyfile

import pytest
from omegaconf import OmegaConf

import inference
from chimera_agent_baseline import run as runner
from chimera_agent_baseline.host_smoke import INPUTS, NN, verify_fixture
from chimera_agent_baseline.information_access import tool_contract
from chimera_agent_baseline.runtime_audit import RuntimeFatal, content_digest, current_stage
from test_p01_g1a_runtime import completion, model_with_transport, profile_for

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'output/s4_control_repair_20261114/source_snapshot'
FIXTURE_DIR = ROOT / 'output/p01_mechanism_acceptance_20261117/fixtures'


def config_for(task):
    cfg = OmegaConf.merge(OmegaConf.load(SNAPSHOT / 'configs/config.yaml'),
                          OmegaConf.load(SNAPSHOT / 'configs/round2_shared_plan_repair_v2.yaml'))
    cfg.agent.tasks = [task]
    cfg.agent.workflow = 'adaptive_v2_smoke'
    cfg.agent.shared_plan.arm = 'adaptive'
    cfg.agent.information_access.run_id = f'adaptive-host-smoke-20261118-t{task}'
    return cfg


def files_for(directory, task):
    directory.mkdir()
    for name in INPUTS:
        copyfile(FIXTURE_DIR / NN[task] / name, directory / name)
    verify_fixture(directory, task)


def action(tool):
    return {'tool': tool, 'query': None, 'purpose': 'Check synthetic observation'}


UPFRONT_HEADER = '[Complete supplied upfront record; not newly acquired clinical data]\n'


@pytest.mark.parametrize('task,mode', [
    (1, 'good'), (2, 'good'), (3, 'good'), (1, 'wrong_profile'), (1, 'wire_budget'),
    (1, 'form_missing'), (3, 'wrong_tool'), (1, 'host_purpose_via_synthetic'),
    (1, 'wrong_fixture'), (1, 'evaluation_file_not_input'),
    (3, 'source_extensions'), (3, 'wrong_projection'), (3, 'wire_budget'),
])
def test_formal_adaptive_entry(tmp_path, monkeypatch, task, mode):
    inp, out = tmp_path / 'inputs', tmp_path / 'sockets'
    files_for(inp, task)
    if mode == 'evaluation_file_not_input':
        (inp / 'expectation_not_agent_input.json').write_text('{}')
        with pytest.raises(RuntimeFatal, match='three frozen input files'):
            verify_fixture(inp, task)
        return
    if mode == 'wrong_fixture':
        (inp / 'structured-prompt.json').write_text('{}')
        with pytest.raises(RuntimeFatal, match='fixture changed'):
            verify_fixture(inp, task)
        return
    source = inp / 'structured-prompt.json'
    original = json.loads(source.read_bytes())
    if task in (1, 2) and mode == 'good':
        # The two other tasks still use their separate interval-since-PSA view.
        original['months'] = 47
        source.write_text(json.dumps(original))
    if mode == 'source_extensions':
        original.update(dre='synthetic-DRE-marker',
            active_treatment_prior_to_surgery='synthetic-treatment-marker',
            event=0, recurrence='synthetic-reference-event',
            months_to_recurrence=87921, last_follow_up=87922,
            unknown_extension={'marker': 'synthetic-rejected-extension'},
            previous_notes={'marker': 'synthetic-unreleased-clinical-section'})
        source.write_text(json.dumps(original))
    cfg = config_for(task)
    config_file = tmp_path / 'config.yaml'
    OmegaConf.save(cfg, config_file)
    for k, v in dict(INPUT_PATH=inp, OUTPUT_PATH=out, CONFIG_PATH=config_file).items():
        monkeypatch.setattr(inference, k, v)
    for name in ('CHIMERA_BASE_URL', 'CHIMERA_T1_TOOL_DENY'):
        monkeypatch.delenv(name, raising=False)
    eff = inference._load_config(tmp_path / 'not-used-in', tmp_path / 'not-used-out', task)
    profile = replace(profile_for(monkeypatch, tmp_path, eff, config_file),
                      max_attempts=12, max_reserved_tokens=195072)
    if mode == 'wrong_profile':
        profile = replace(profile, effective_digest='wrong')
    if mode == 'host_purpose_via_synthetic':
        profile = replace(profile, purpose='adaptive_host_smoke')
    if mode == 'wire_budget':
        profile = replace(profile, max_attempts=1)
    if mode == 'wrong_projection':
        materialise = inference._materialise_case

        def replace_admitted_view(task, sockets, root, case, **kwargs):
            materialise(task, sockets, root, case, **kwargs)
            changed = root / 'task3/agent_input' / case / 'prompt.json'
            data = json.loads(changed.read_bytes())
            data['months'] = 12
            changed.write_text(json.dumps(data))

        monkeypatch.setattr(inference, '_materialise_case', replace_admitted_view)

    first, revised = ('get_mri_report', 'get_surgical_pathology_report') if task == 3 else (
        'get_lab_results', 'get_mri_report')
    start = [action(first), action('get_previous_notes')]
    cases = {
        1: dict(biopsy_decision='yes', confidence='clear', variable_weights={},
                free_text='Synthetic retained biopsy rationale for the output contract.'),
        2: dict(treatment_recommendation={'primary': 'active_treatment'}, confidence='clear',
                variable_weights={}, free_text='Synthetic retained treatment rationale for the output contract.'),
        3: dict(event=1, months_to_recurrence=25.0, free_text='Synthetic retained recurrence rationale for the output contract.'),
    }
    replies = cases[task]
    sends, sdk_digests, progress = [], [], iter((
        {'remaining_plan': [action(revised)], 'reason': 'Replace second action'},
        {'remaining_plan': [], 'reason': 'Stop with the verified observations'},
    ))

    def assert_view(body):
        user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
        record, _ = json.JSONDecoder().raw_decode(user.split(UPFRONT_HEADER, 1)[1])
        if task == 3:
            expected = {key: original[key] for key in (
                'age', 'psa', 'dre', 'active_treatment_prior_to_surgery') if key in original}
            assert record == dict(expected, case_id='gc-case', task=3)
            for forbidden in ('"months"', '87921', '87922', 'synthetic-reference-event',
                              'synthetic-rejected-extension', 'synthetic-unreleased-clinical-section'):
                assert forbidden not in user
        elif mode == 'good':
            assert record['months'] == 47
        return record

    def mock(request):
        stage = current_stage()
        sends.append(stage)
        body = json.loads(request.content)
        sdk_digests.append(content_digest(body))
        assert "expectation_not_agent_input" not in str(body)
        record = assert_view(body)
        user = next(m['content'] for m in body['messages'] if m['role'] == 'user')
        if task != 3 and stage != 'form_fill' and mode == 'good':
            assert 'Months since last PSA: 47' in user
        if stage in ("planner", "policy"):
            assert body['response_format']['json_schema']['strict'] is True
            assert body['max_completion_tokens'] == 1024
            if stage == 'planner':
                if mode == 'wrong_tool':
                    return completion(json.dumps({'actions': [action('get_lab_results')]}))
                return completion(json.dumps({'actions': start}))
            assert 'Verified observations' in str(body)
            return completion(json.dumps(next(progress)))
        if stage == 'diagnosis':
            # Echo the actual decision payload, so a leak would propagate to
            # the formatter request instead of being hidden by the stub.
            return completion('Synthetic diagnosis draft.\n' + UPFRONT_HEADER + json.dumps(record))
        assert stage == 'form_fill' and body['messages'][-1]['content'] == '{'
        return completion('}' if mode == 'form_missing' else json.dumps(replies)[1:])

    def counter(body):
        assert body['max_completion_tokens'] in (1024, 4096)
        return {'input_tokens': 11, 'fits': True, 'fixture': 'not_real_tokens'}

    monkeypatch.setattr(runner, 'load_model', lambda _: model_with_transport(mock))
    arguments = dict(_profile_registry={profile.name: profile}, _token_counter=counter)
    if mode in ('wrong_profile', 'host_purpose_via_synthetic'):
        with pytest.raises(RuntimeFatal):
            inference.run(**arguments)
        assert not sends and not out.exists()
        return
    if mode == 'wrong_projection':
        with pytest.raises(RuntimeFatal, match='does not match the admitted view'):
            inference.run(**arguments)
        assert not sends and not out.exists()
        return
    if mode in ('wire_budget', 'form_missing', 'wrong_tool'):
        with pytest.raises((RuntimeFatal, RuntimeError)):
            inference.run(**arguments)
        assert not out.exists() or not any(out.iterdir())
    else:
        assert inference.run(**arguments) == 0

    (events_file,) = (tmp_path / 'private').glob('*/events.jsonl')
    events = [json.loads(line) for line in events_file.read_text().splitlines()]
    assert sdk_digests == [e['payload_digest'] for e in events if e['kind'] == 'http_request_verified']
    dispatch = next(e for e in events if e['kind'] == 'adaptive_dispatch')
    expected_tools = set(tool_contract(task)['tools']) | {'search_guidelines'}
    if task == 1:
        expected_tools -= {'get_pathology_report', 'get_family_history'}
    assert set(dispatch['tools']) == expected_tools
    assert dispatch['controller'] == 'shared_plan.acquire'
    case = events_file.parent / 'temporary_evidence/output' / f'task{task}/gc-case'
    assert (case / 'information_access.sqlite').is_file()
    if mode == 'wire_budget':
        assert sends == ['planner']
        assert any(e.get('reason') == 'fatal_budget' for e in events)
    if mode == 'wrong_tool':
        assert sends == ['planner', 'planner']
        plan_events = (case / 'common_plan/model_calls.jsonl').read_text()
        assert 'unavailable action tool' in plan_events
    if mode == 'form_missing':
        assert sends[-3:] == ['form_fill'] * 3
        assert events[-1]['kind'] == 'run_failed'
    if mode not in ('good', 'source_extensions'):
        assert not any(e.get('stage') == 'final_sockets' for e in events)
        return

    assert sends == ['planner', 'policy', 'policy', 'diagnosis', 'form_fill']
    journal = [json.loads(line) for line in (case / 'candidate_events.jsonl').read_text().splitlines()]
    decisions = [e for e in journal if e['kind'] == 'policy_decision']
    assert decisions[0]['previous_plan'] == start[1:] and decisions[0]['plan_replaced']
    assert decisions[0]['decision']['remaining_plan'] == [action(revised)]
    assert decisions[1]['decision']['remaining_plan'] == []
    assert [e['tool'] for e in journal if e['kind'] == 'tool_request'] == [first, revised]
    assert any(e['kind'] == 'acquisition_stop' and e['reason'] == 'policy_stop' for e in journal)
    assert {p.name for p in out.iterdir()} == set(inference.OUTPUT_SOCKETS[task].values())
    final = next(e for e in events if e.get('stage') == 'final_sockets')
    assert final['authority'] == 'engineering_form_and_platform_projection'
    reasoning = final['after']['reasoning']
    assert (reasoning if task == 3 else reasoning['free_text']) == replies['free_text']
    assert final['after']['decision'] == {1: 'yes', 2: 'active_treatment',
        3: {'event': 1, 'months_to_recurrence': 25.0}}[task]
    assert not any(e.get('stage') == 'reasoning_align' for e in events)
    budget = json.loads((case / 'information_access_snapshot.json').read_text())
    assert budget['admitted_attempts'] == budget['spent_units'] == 2
    if task == 3:
        admitted = next(e for e in events if e['kind'] == 't3_upfront_admitted')
        bound = next(e for e in events if e['kind'] == 't3_upfront_bound')
        projection = case.parents[2] / 'input/task3/agent_input/gc-case/prompt.json'
        assert (events_file.parent / admitted['archived_file']).read_bytes() == source.read_bytes()
        assert admitted['original_sha256'] == sha256(source.read_bytes()).hexdigest()
        assert admitted['rejected_fields']['months'] == 'ambiguous_time_semantics'
        assert bound['admission_digest'] == admitted['digest']
        assert bound['projected_sha256'] == admitted['projected_sha256'] == sha256(projection.read_bytes()).hexdigest()
        receipt = json.loads((case / 'information_access_receipt.json').read_text())['binding']
        assert receipt['initial_source_sha256'] == bound['projected_sha256']
        assert receipt['session_id'] == bound['session_id']
        assert receipt['snapshot_id'] == bound['snapshot_id']
        form = next(e for e in events if e.get('stage') == 'formfill_input')
        form_state = json.dumps(form['after'])
        assert '"months"' not in form['after']['transcript']
        assert 'synthetic-unreleased-clinical-section' not in form_state
        if mode == 'source_extensions':
            assert admitted['rejected_fields']['event'] == 'outcome_or_follow_up_not_an_upfront_field'
            assert admitted['rejected_fields']['previous_notes'] == 'field_outside_official_t3_upfront'
            assert 'synthetic-DRE-marker' in form_state and 'synthetic-treatment-marker' in form_state


def test_t3_upfront_shape_checks(tmp_path):
    from chimera_agent_baseline.runtime_audit import PrivateRuntime
    runtime = PrivateRuntime(tmp_path / 'private', max_attempts=0, max_reserved_tokens=0,
                             token_counter=lambda _: None)
    source = tmp_path / 'structured-prompt.json'
    source.write_text('{"age": true, "psa": {"value": 7}, "dre": [], "active_treatment_prior_to_surgery": null}')
    projected, provenance = inference._t3_upfront_view(source, runtime)
    assert projected == {'active_treatment_prior_to_surgery': None}
    assert provenance['rejected_fields'] == dict.fromkeys(
        ('age', 'psa', 'dre'), 'not_an_allowed_scalar_for_field')


@pytest.mark.parametrize('value,reason', [('[]', 'expected_json_object'),
    ('{"ground_truth": "synthetic"}', 'reference_audit_fields_in_input')])
def test_t3_rejected_source_has_private_receipt(tmp_path, value, reason):
    from chimera_agent_baseline.runtime_audit import PrivateRuntime
    runtime = PrivateRuntime(tmp_path / 'private', max_attempts=0, max_reserved_tokens=0,
                             token_counter=lambda _: None)
    source = tmp_path / 'structured-prompt.json'
    source.write_text(value)
    with pytest.raises(RuntimeFatal):
        inference._t3_upfront_view(source, runtime)
    event = json.loads(runtime.path.read_text().splitlines()[-1])
    assert event['kind'] == 't3_upfront_source_rejected'
    assert event['reason'] == reason
    assert (runtime.root / event['archived_file']).read_text() == value
