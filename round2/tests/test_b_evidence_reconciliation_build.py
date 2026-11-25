"""B evidence fixtures through the real guarded entry and SDK meter."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from shutil import copytree

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool, ToolException
from mcp import types as mcp_types
from omegaconf import OmegaConf

import inference
from chimera_agent_baseline import run as runner
from chimera_agent_baseline.agent import form_fill
from chimera_agent_baseline.agent.evidence_reconciliation import (
    Annotation, EvidenceSession, MARKER, Source, SyntheticEvidence, bound_tool_sources,
)
from chimera_agent_baseline.information_access import digest, tool_contract
from chimera_agent_baseline.output.schema import eligible_variables
from chimera_agent_baseline.runtime_audit import RuntimeFatal, canonical, content_digest
from test_p01_g1a_runtime import completion, model_with_transport, profile_for

ROOT = Path(__file__).resolve().parents[1]
CASE = 'gc-case'
UPFRONT = {'case_id': CASE, 'task': 1, 'psa': 5}
LAB = {'case_id': CASE, 'laboratory_results': [{'name': 'PSA', 'val': 6, 'unit': 'fixture-unit'}]}


def fixture(task=1, *, case=CASE, cutoff='2020-07-01', lab=True):
    common = dict(state='observed', entity='fixture-entity', definition='same-sample-quantity',
                  method='fixture-method', unit='fixture-unit', observation_id='sample-a',
                  observed='2020-01-02', reported='2020-01-03', available='2020-01-04')
    a = Annotation('upfront', digest(UPFRONT | {'task': task}), ('psa',), **common)
    b = Annotation('get_lab_results', digest(LAB), ('laboratory_results', 0, 'val'), **common)
    return SyntheticEvidence(task, case, cutoff, (a, b) if lab else (a,))


def binding(task=1):
    return dict(task=task, case_id=CASE, session_id='bound-session', snapshot_id='fixture-snapshot',
                initial_source_sha256='upfront-bytes', contract=tool_contract(task))


@pytest.mark.parametrize('variant,expected', [
    ({}, 'same_observation_conflict_unresolved'),
    ({'observation_id': 'sample-b', 'observed': '2019-12-11'}, 'distinct_times'),
    ({'definition': None}, 'not_comparable'),
    ({'entity': 'different-lesion'}, 'not_comparable'),
    ({'method': 'different-method'}, 'not_comparable'),
    ({'unit': 'unit-mismatch'}, 'not_comparable'),
    ({'observed': None, 'reported': '2019-12-11'}, 'order_or_observation_unknown'),
    ({'observed': '2020-01', 'observation_id': 'sample-b'}, 'order_or_observation_unknown'),
])
def test_comparability_keeps_original_values(variant, expected):
    authority = fixture()
    a, b = authority.annotations
    session = EvidenceSession(replace(authority, annotations=(a, replace(b, **variant))),
                              'fixture-audit', binding(), UPFRONT)
    session.sources.append(Source('get_lab_results', LAB, {'acquired_unix': 22}))
    projection, checked = session._project()
    assert len(projection['observations']) == len(checked) == 2
    pair = projection['relations'][0]
    assert pair['status'] == expected
    if expected == 'distinct_times':
        assert pair['left_relative_to_right'] == 'after'
    assert LAB['laboratory_results'][0]['val'] == 6 and UPFRONT['psa'] == 5
    assert all('value' not in obs for obs in projection['observations'])


@pytest.mark.parametrize('change,status', [
    ({'reported': '2020-08-02'}, 'after_cutoff'),
    ({'available': '2020-08-02'}, 'after_cutoff'),
    ({'observed': '2020-08-02'}, 'after_cutoff'),
    ({'available': None}, 'cutoff_availability_unknown'),
    ({'observed': '2020-07'}, 'cutoff_availability_unknown'),
    ({'available': '2020-01-01'}, 'inconsistent_availability_metadata'),
])
def test_dated_ineligible_observation_excluded(change, status):
    authority = fixture()
    a, b = authority.annotations
    session = EvidenceSession(replace(authority, annotations=(a, replace(b, **change))),
                              'fixture-audit', binding(), UPFRONT)
    session.sources.append(Source('get_lab_results', LAB, {'acquired_unix': 22}))
    projection, checked = session._project()
    assert projection['relations'] == [] and len(projection['observations']) == 1
    assert checked[1]['status'] == status


def test_runtime_acquisition_cutoff_is_separate_from_report():
    session = EvidenceSession(replace(fixture(), acquisition_deadline_unix=21),
                              'fixture-audit', binding(), UPFRONT)
    session.sources.append(Source('get_lab_results', LAB, {'acquired_unix': 22}))
    projection, checked = session._project()
    assert checked[1]['status'] == 'after_cutoff'
    assert checked[1]['times']['reported'] == '2020-01-03'
    assert checked[1]['times']['acquired_unix'] == 22
    assert len(projection['observations']) == 1


def test_distinct_assertion_states_and_unknown_cutoff():
    raw = UPFRONT | {'psad': None, 'vol': 'fixture intention', 'pirads_int': False}
    authority = fixture(lab=False)
    template = replace(authority.annotations[0], document_digest=digest(raw))
    annotations = tuple(replace(template, path=(p,), state=s) for p, s in [
        ('psa', 'uncertain'), ('psad', 'missing'), ('vol', 'planned'), ('pirads_int', 'negated')])
    authority = replace(authority, annotations=annotations)
    session = EvidenceSession(authority, 'fixture-audit', binding(), raw)
    projection, _ = session._project()
    assert [item['state'] for item in projection['observations']] == ['uncertain', 'missing', 'planned', 'negated']
    assert not projection['relations']
    session = EvidenceSession(replace(authority, cutoff=None), 'fixture-audit', binding(), raw)
    assert session._project()[0]['observations'] == []


def test_equivalent_json_number_does_not_create_disagreement():
    lab = deepcopy(LAB)
    lab['laboratory_results'][0]['val'] = 5.0
    authority = fixture()
    a, b = authority.annotations
    session = EvidenceSession(replace(authority, annotations=(a, replace(b, document_digest=digest(lab)))),
                              'fixture-audit', binding(), UPFRONT)
    session.sources.append(Source('get_lab_results', lab, {}))
    assert session._project()[0]['relations'][0]['status'] == 'same_observation_same_value'


def test_duplicate_metadata_for_one_source_path_is_an_error():
    authority = fixture(lab=False)
    duplicated = replace(authority, annotations=authority.annotations * 2)
    with pytest.raises(ValueError, match='Duplicate'):
        EvidenceSession(duplicated, 'fixture-audit', binding(), UPFRONT)._project()


@pytest.mark.parametrize('change', ['forged_receipt', 'old_session', 'wrong_case', 'wrong_digest',
    'wrong_active_id', 'duplicate_call', 'cache', 'unsuccessful', 'not_observed', 't3'])
def test_no_tool_evidence_without_verified_in_run_binding(change):
    bind = binding(3 if change == 't3' else 1)
    calls = [{'id': 'call-fixture', 'name': 'get_lab_results', 'args': {'case_id': CASE}}]
    message = ToolMessage(content=json.dumps(LAB), name='get_lab_results', tool_call_id='call-fixture',
                          additional_kwargs={'information_access_clinical_success': True})
    event = dict(status='returned', success=True, allowed=True, session_id=bind['session_id'],
        case_id=CASE, tool='get_lab_results', fields=['laboratory_results'], response_digest=digest(LAB),
        request_id='local-ledger-id', timestamp_unix=20)
    if change in ('old_session', 'wrong_case', 'wrong_digest', 'cache', 'unsuccessful'):
        key, value = dict(old_session=('session_id', 'previous'), wrong_case=('case_id', 'elsewhere'),
                          wrong_digest=('response_digest', 'forged'), cache=('status', 'cache_returned'),
                          unsuccessful=('success', False))[change]
        event[key] = value
    if change == 'wrong_active_id':
        calls[0]['id'] = 'different'
    if change == 'duplicate_call':
        calls.append(calls[0] | {'id': 'other'})
    if change == 'not_observed':
        message.additional_kwargs.clear()
    if change == 'forged_receipt':
        message.additional_kwargs['receipt'] = event
    new_events = [] if change == 'forged_receipt' else [event]
    assert bound_tool_sources(bind, calls, [message], new_events, set()) == []


class TextFixtureMCPClient:
    """Replace the external transport. Use the same FastMCP handler and ledger."""

    def __init__(self, connections, *, tool_interceptors=None):
        from chimera_agent_baseline.mcp_server import _REGISTRIES, create_server
        args = connections['chimera']['args']
        config = json.loads(Path(args[args.index('--access-session') + 1]).read_text())
        self.server = create_server(args[args.index('--data-dir') + 1],
            tools=_REGISTRIES[args[args.index('--tool-registry') + 1]], access_config=config)

    async def get_tools(self):
        return [self._tool(spec) for spec in await self.server.list_tools()]

    def _tool(self, spec):
        async def call(**arguments):
            handler = self.server._mcp_server.request_handlers[mcp_types.CallToolRequest]
            result = (await handler(mcp_types.CallToolRequest(method='tools/call',
                params=mcp_types.CallToolRequestParams(name=spec.name, arguments=arguments)))).root
            assert len(result.content) == 1 and isinstance(result.content[0], mcp_types.TextContent)
            text = result.content[0].text
            if result.isError:
                raise ToolException(text)
            return text

        return StructuredTool(name=spec.name, description=spec.description or '',
                              args_schema=spec.inputSchema, coroutine=call)


def run_entry(tmp_path, monkeypatch, task, case_mode):
    monkeypatch.chdir(tmp_path)
    copytree(ROOT/'templates', tmp_path/'templates')
    input_dir = tmp_path/'input'
    input_dir.mkdir()
    slug = next(k for k, v in inference.CLINICAL_SLUG_TO_TASK.items() if v == task)
    (input_dir/'inputs.json').write_text(json.dumps([
        {'socket': {'slug': slug, 'relative_path': 'clinical.json'}},
        {'socket': {'slug': inference.STRUCTURED_PROMPT_SLUG, 'relative_path': 'prompt.json'}}]))
    # T3 contains inaccessible sentinel fields to catch accidental cross-task promotion.
    clinical = LAB | {'future_outcome': 'FORBIDDEN_FIXTURE_FUTURE'}
    if case_mode == 'stdio_failure':
        clinical['case_id'] = 'wrong-snapshot-case'
    (input_dir/'clinical.json').write_text(json.dumps(clinical))
    (input_dir/'prompt.json').write_text(json.dumps({'psa': 5}))
    config_path = tmp_path/'config.yaml'
    cfg = OmegaConf.load(ROOT/'configs/config.yaml')
    cfg.agent.information_access = dict(enabled=True, run_id='fixture-b', budget_units=1, max_attempts=2)
    cfg.agent.workflow = 'react'
    cfg.agent.max_model_turns = 3
    cfg.agent.predictor.enabled = False
    cfg.agent.predictor.decision_override = False
    cfg.agent.predictor.decision_override_tasks = []
    cfg.agent.form_fill.self_refine = False
    cfg.agent.form_fill.atomic_check = False
    cfg.agent.form_fill.max_retries = 1
    OmegaConf.save(cfg, config_path)
    for name, value in dict(INPUT_PATH=input_dir, OUTPUT_PATH=tmp_path/'sockets',
                            CONFIG_PATH=config_path, RESOURCE_PATH=tmp_path/'empty_resources',
                            MODEL_PATH=tmp_path/'no_model').items():
        monkeypatch.setattr(inference, name, value)
    monkeypatch.setenv('CHIMERA_FORMFILL_SELF_REFINE', '0')
    monkeypatch.setenv('CHIMERA_STRICT_RUNTIME', '0')
    monkeypatch.setenv('PYTHONPATH', str(ROOT/'src'))
    monkeypatch.setattr(inference, 'start_embedding_service', lambda *_: pytest.fail('model service startup'))
    if task != 3 and not (case_mode.startswith('stdio_') or case_mode == 'upfront_only'):
        monkeypatch.setattr(runner, 'MultiServerMCPClient', TextFixtureMCPClient)
    cfg = inference._load_config(input_dir, tmp_path/'not_used', task)
    profile = replace(profile_for(monkeypatch, tmp_path, cfg, config_path), max_reserved_tokens=200000)
    authority = fixture(task)
    if case_mode == 'fallback':
        authority = replace(authority, cutoff='bad fixture time')
    if case_mode == 'unknown':
        authority = replace(authority, cutoff=None)
    if case_mode == 'wrong_case':
        authority = replace(authority, case_id='other')

    bodies, counted, read = [], [], []

    def counter(payload):
        # Character size is an intentionally artificial test counter.
        value = len(canonical(payload).encode())
        counted.append((content_digest(payload), value))
        fits = not (case_mode == 'budget' and MARKER in canonical(payload) or case_mode == 'stdio_budget'
                    and any(m['role'] == 'tool' for m in payload['messages']))
        return {'input_tokens': value, 'fits': fits,
                'tokenizer': 'synthetic_utf8_bytes_only'}

    def handler(request):
        body = json.loads(request.content)
        messages = body['messages']
        assert 'FORBIDDEN_FIXTURE_FUTURE' not in canonical(body)
        bodies.append(body)
        if messages[0]['content'] == form_fill._SYSTEM_PROMPT:
            judgment = {'free_text': 'Fixture only, no medical interpretation is attributed to this software response.',
                        'repeat_test': None}
            if task == 3:
                judgment.update(event=0, months_to_recurrence=12.0)
            else:
                judgment.update(confidence='uncertain', variable_weights={k: 'not_used' for k in
                                eligible_variables(task, {'get_lab_results'})})
                if task == 1:
                    judgment['biopsy_decision'] = 'no'
                else:
                    judgment['treatment_recommendation'] = {'primary': 'active_surveillance'}
            return completion(canonical(judgment)[1:])
        blocks = [m['content'].split('\n', 1)[1] for m in messages if isinstance(m['content'], str)
                  and m['content'].startswith(MARKER)]
        read.append([json.loads(block) for block in blocks])
        if task != 3 and case_mode != 'upfront_only' and len(read) == 1:
            tool_case = 'other' if case_mode == 'stdio_denied' else CASE
            return completion('', tool_calls=[{'id': 'call-fixture-lab', 'type': 'function',
                'function': {'name': 'get_lab_results', 'arguments': json.dumps({'case_id': tool_case})}}])
        return completion('Synthetic final consumer read the supplied fixture.')

    monkeypatch.setattr(runner, 'load_model', lambda _: model_with_transport(handler))
    kwargs = dict(_profile_registry={'synthetic': profile}, _token_counter=counter,
                  _evidence_test=None if case_mode == 'off' else authority)
    if case_mode == 'stdio_failure':
        with pytest.raises(RuntimeError, match='required case predictions missing'):
            inference.run(**kwargs)
        assert len(bodies) == len(counted) == 1 and not (tmp_path/'sockets').exists()
        ledger = next((tmp_path/'private').glob('*/events.jsonl'))
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert rows[-1]['kind'] == 'run_failed' and not any(e['stage'] == 'final_sockets' for e in rows)
        assert not any(e['stage'] == 'b_mcp_sdk_text' for e in rows)
        (tmp_path/'fixture_summary.json').write_text(json.dumps(dict(task=task,mode=case_mode,
            ledger=str(ledger),requests_before_rejection=len(bodies)), indent=2)+'\n')
        return
    if case_mode == 'stdio_budget':
        with pytest.raises(RuntimeFatal, match='exhausted exact context reservation'):
            inference.run(**kwargs)
        assert len(bodies) == 1 and len(counted) == 2 and not (tmp_path/'sockets').exists()
        ledger = next((tmp_path/'private').glob('*/events.jsonl'))
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        boundary = [e for e in rows if e['kind'] == 'request_preflight']
        assert len(boundary) == 2 and rows[-1]['kind'] == 'run_failed'
        projected = [e for e in rows if e['stage'] == 'b_evidence_agent_input']
        assert projected[-1]['after']['relations'][0]['status'] == 'same_observation_conflict_unresolved'
        assert any(e['stage'] == 'b_mcp_sdk_text' and e['status'] == 'projected_for_sdk' for e in rows)
        assert not any(e['stage'] == 'final_sockets' for e in rows)
        (tmp_path/'fixture_summary.json').write_text(json.dumps(dict(task=task, mode=case_mode,
            ledger=str(ledger), requests_before_rejection=len(bodies), tool_resolution_event=projected[-1]['digest'],
            rejected_preflight=boundary[-1]['digest']), indent=2)+'\n')
        return
    if case_mode in ('budget', 'wrong_case'):
        with pytest.raises(RuntimeFatal) as failure:
            inference.run(**kwargs)
        assert not bodies and not (tmp_path/'sockets').exists()
        assert len(counted) == (case_mode == 'budget')
        ledger = next((tmp_path/'private').glob('*/events.jsonl'), None)
        if ledger:
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            assert rows[-1]['kind'] == 'run_failed' and not any(e['kind'] == 'wire_attempt' for e in rows)
        (tmp_path/'fixture_summary.json').write_text(json.dumps(dict(task=task, mode=case_mode,
            ledger=str(ledger) if ledger else None, model_requests=0, output_files=0,
            reason=str(failure.value), counted_calls=len(counted)), indent=2)+'\n')
        return
    assert inference.run(**kwargs) == 0
    ledger = next((tmp_path/'private').glob('*/events.jsonl'))
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    attempts = [row for row in rows if row['kind'] == 'wire_attempt']
    finals = next(e for e in rows if e['stage'] == 'final_sockets')
    no_tool = task == 3 or case_mode == 'upfront_only'
    assert len(attempts) == len(counted) == len(bodies) == (2 if no_tool else 3)
    assert [(e['payload_digest'], e['context']['input_tokens']) for e in attempts] == counted
    assert all(content_digest(body) == item[0] for body, item in zip(bodies, counted, strict=True))
    snapshot = next(ledger.parent.rglob('information_access_snapshot.json'))
    budget = json.loads(snapshot.read_text())
    assert budget['admitted_attempts'] == budget['spent_units'] == (0 if no_tool or case_mode == 'stdio_denied' else 1)
    assert len(finals['files']) == 2 and rows[-1]['kind'] == 'run_completed'
    for obj in finals['files'].values():
        assert sha256((tmp_path/'sockets'/obj['file']).read_bytes()).hexdigest() == obj['sha256']
    events = [e for e in rows if e['stage'] == 'b_evidence_agent_input']
    if case_mode == 'upfront_only':
        assert len(read) == 1 and len(read[0][0]['observations']) == 1
        assert read[0][0]['observations'][0]['reference'] == {'source': 'upfront', 'path': ['psa']}
        assert read[0][0]['relations'] == []
    elif case_mode in ('on', 'stdio_on') and task != 3:
        assert read[1][0]['relations'][0]['status'] == 'same_observation_conflict_unresolved'
        assert all('value' not in ref for ref in read[1][0]['observations'])
        assert events[1]['sources'][1]['receipt']['tool_call_id'] == 'call-fixture-lab'
        assert {'get_lab_results', 'upfront'} == {o['reference']['source'] for o in read[1][0]['observations']}
        assert any(m['role'] == 'tool' and 'fixture-unit' in m['content'] for m in bodies[1]['messages'])
    elif case_mode == 'stdio_denied':
        assert read[1][0]['relations'] == [] and len(read[1][0]['observations']) == 1
        assert any(m['role'] == 'tool' and 'cross_case' in m['content'] for m in bodies[1]['messages'])
        assert any(e['status'] == 'cross_case' and not e['success'] for e in budget['events'])
    else:
        assert not any(read)
        expected = ('skipped_t3_no_approved_adapter' if task == 3 and case_mode == 'on' else
                    'fallback_original_context' if case_mode == 'fallback' else 'skipped_no_eligible_evidence')
        assert ({e['status'] for e in events} == {expected}) if case_mode != 'off' else not events
    conversions = [e for e in rows if e['stage'] == 'b_mcp_sdk_text']
    assert len(conversions) == (1 if case_mode == 'stdio_on' else 0)
    if conversions:
        event = conversions[0]
        assert len(event['before']) == 1 and event['before'][0]['text'] == event['after']
        assert event['receipt'] == events[1]['sources'][1]['receipt']
        assert digest(event['native_result']) == event['native_result_digest']
        assert any(m['role'] == 'tool' and m['tool_call_id'] == event['tool_call_id']
                   and m['content'] == event['after'] for m in bodies[1]['messages'])
    (tmp_path/'fixture_summary.json').write_text(json.dumps(dict(task=task, mode=case_mode, model_requests=len(bodies),
        information_units=budget['spent_units'], admitted_tool_attempts=budget['admitted_attempts'],
        synthetic_context_sizes=[n for _, n in counted], consumer_read=read, wire_event_digests=[e['digest'] for e in attempts],
        b_event_digests=[e['digest'] for e in events], final_event_digest=finals['digest'],
        files=finals['files'], ledger=str(ledger)), indent=2)+'\n')
    return bodies, finals['after']


@pytest.mark.parametrize('task', [1, 2, 3])
def test_real_entry_candidate_and_baseline(tmp_path, monkeypatch, task):
    base = tmp_path/'baseline'
    comparison = tmp_path/'candidate'
    base.mkdir()
    comparison.mkdir()
    with monkeypatch.context() as env:
        base_requests, base_out = run_entry(base, env, task, 'off')
    with monkeypatch.context() as env:
        candidate, out = run_entry(comparison, env, task, 'on')
    assert out == base_out  # The scripted software oracle's responses are fixed.
    for actual, original in zip(candidate, base_requests, strict=True):
        actors = [m for m in actual['messages'] if not (isinstance(m['content'], str)
                  and m['content'].startswith(MARKER))]
        assert {**actual, 'messages': actors} == original


@pytest.mark.parametrize('case_mode', ['fallback', 'unknown', 'budget', 'wrong_case'])
def test_real_entry_fallback_skip_and_refusal(tmp_path, monkeypatch, case_mode):
    run_entry(tmp_path, monkeypatch, 1, case_mode)


@pytest.mark.parametrize('case_mode', ['upfront_only', 'stdio_budget', 'stdio_denied', 'stdio_failure'])
def test_real_stdio_boundary(tmp_path, monkeypatch, case_mode):
    run_entry(tmp_path, monkeypatch, 1, case_mode)


@pytest.mark.parametrize('task', [1, 2])
def test_real_stdio_tool_text_matches_string_control(tmp_path, monkeypatch, task):
    control = tmp_path/'string_control'
    control.mkdir()
    transport = tmp_path/'stdio'
    transport.mkdir()
    with monkeypatch.context() as env:
        expected = run_entry(control, env, task, 'on')
    with monkeypatch.context() as env:
        actual = run_entry(transport, env, task, 'stdio_on')
    assert actual == expected
