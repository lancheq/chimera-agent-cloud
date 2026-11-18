"""G1A engineering evidence only: synthetic payloads, in-memory HTTP transports.
No patient inputs, listening sockets, model server, GPU or downloads.
"""
import asyncio
import dataclasses
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from omegaconf import OmegaConf

from chimera_agent_baseline.runtime_audit import (
    PrivateRuntime, RuntimeFatal, case_scope, content_digest, current_runtime,
)
from chimera_agent_baseline.models.openai_compat import ChatOpenAICompat
from chimera_agent_baseline.models.request_meter import meter_openai
from chimera_agent_baseline.entry_profile import (
    EntryProfile, effective_config, effective_environment, runtime_flags,
    source_identity, verify_profile, verify_identity,
)
from chimera_agent_baseline.agent import form_fill as ff

ROOT = Path(__file__).resolve().parents[1]


def run_async(coroutine):
    # Restricted local sandbox can suppress socketpair wakeups from worker
    # threads. A test-only timer wakes the selector during native SDK async
    # result conversion and executor shutdown; no production behavior changes.
    from concurrent.futures import ThreadPoolExecutor
    loop = asyncio.new_event_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    handle = None
    def tick():
        nonlocal handle
        handle = loop.call_later(0.01, tick)
    tick()
    try:
        return loop.run_until_complete(asyncio.wait_for(coroutine, timeout=5))
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        handle.cancel()
        loop.close()


def counter(payload):
    # Fixture-defined tokenization ONLY. Real tool templates remain unverified.
    assert isinstance(payload['messages'], list)
    return dict(input_tokens=7, fits=True, tokenizer='synthetic_stub_only')


def runtime(tmp_path, **kwargs):
    return PrivateRuntime(tmp_path / 'private', max_attempts=kwargs.get('max_attempts', 20),
                          max_reserved_tokens=kwargs.get('max_reserved_tokens', 10000),
                          token_counter=kwargs.get('token_counter', counter))


def events(r):
    return [json.loads(line) for line in r.path.read_text().splitlines()]


def model_with_transport(handler, *, retries=0):
    transport = httpx.MockTransport(handler)
    model = ChatOpenAICompat(model='synthetic-no-server', base_url='http://synthetic.invalid/v1',
        api_key='synthetic', max_tokens=32, max_retries=retries,
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport))
    return meter_openai(model)


def completion(text='synthetic', *, tool_calls=None):
    message = dict(role='assistant', content=text)
    if tool_calls:
        message['tool_calls'] = tool_calls
    return httpx.Response(200, json=dict(id='synthetic', object='chat.completion', created=0,
        model='synthetic-no-server', choices=[dict(index=0, finish_reason='stop', message=message)],
        usage=dict(prompt_tokens=7, completion_tokens=3, total_tokens=10)))


def profile_for(monkeypatch, tmp_path, cfg, config_path):
    monkeypatch.setenv('CHIMERA_ENTRY_PROFILE', 'synthetic')
    return EntryProfile(name='synthetic', config_path=str(config_path.resolve()),
        config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
        effective_digest=content_digest(effective_config(cfg)), sources=source_identity(),
        environment=effective_environment(), runtime_flags=runtime_flags(),
        audit_root=str(tmp_path / 'private'), max_attempts=20, max_reserved_tokens=10000)


def test_sync_async_bound_tools_and_full_roles_share_ledger(tmp_path):
    payloads = []
    def handler(request):
        payloads.append(json.loads(request.content))
        return completion()
    model = model_with_transport(handler)
    bound = model.bind_tools([dict(type='function', function=dict(name='synthetic_tool',
        description='software fixture', parameters=dict(type='object', properties={})))])
    messages = [HumanMessage(content='synthetic'), AIMessage(content='reason',
        tool_calls=[dict(id='call_1', name='synthetic_tool', args={})]),
        ToolMessage(content='synthetic result intact', tool_call_id='call_1')]
    r = runtime(tmp_path)
    with r.activate(), case_scope(1, 'synthetic'):
        model.invoke([HumanMessage(content='plain')])
        bound.invoke(messages)
        run_async(bound.ainvoke(messages))
    assert r.attempts == len(payloads) == 3
    assert payloads[1] == payloads[2]
    assert payloads[1]['tools'][0]['function']['name'] == 'synthetic_tool'
    assert payloads[1]['messages'][-1]['content'] == 'synthetic result intact'
    assert payloads[1]['messages'][1]['tool_calls'][0]['id'] == 'call_1'
    attempts = [e for e in events(r) if e['kind'] == 'wire_attempt']
    assert [e['payload'] for e in attempts] == payloads
    assert all(e['case_id'] == 'synthetic' and e['run_id'] == r.run_id for e in attempts)
    assert len([e for e in events(r) if e['kind'] == 'model_response']) == 3


@pytest.mark.parametrize('asynchronous', [False, True])
def test_hidden_sdk_retries_disabled_failed_attempt_charged(tmp_path, asynchronous):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(500, json={'error': {'message': 'synthetic failure'}})
    model = model_with_transport(handler)
    r = runtime(tmp_path)
    with r.activate(), pytest.raises(Exception):
        if asynchronous:
            run_async(model.ainvoke([HumanMessage(content='synthetic')]))
        else:
            model.invoke([HumanMessage(content='synthetic')])
    assert len(seen) == r.attempts == 1
    assert r.reserved_tokens == 39
    assert any(e['kind'] == 'wire_error' for e in events(r))


@pytest.mark.parametrize('kind', ['attempts', 'tokens', 'counter'])
def test_fatal_budget_before_wire_cannot_be_default_success(tmp_path, kind):
    seen = []
    model = model_with_transport(lambda request: seen.append(request) or completion())
    r = runtime(tmp_path, max_attempts=0 if kind == 'attempts' else 20,
        max_reserved_tokens=0 if kind == 'tokens' else 10000,
        token_counter=(lambda p: (_ for _ in ()).throw(ValueError('unsupported tools'))) if kind == 'counter' else counter)
    with r.activate(), pytest.raises(RuntimeFatal):
        try:
            model.invoke([HumanMessage(content='synthetic')])
        except Exception:
            pytest.fail('fatal entered legacy fallback')
    assert len(seen) == r.attempts == 0


def test_prefill_transport_fallback_and_parse_retry_all_counted(tmp_path, monkeypatch):
    monkeypatch.setenv('CHIMERA_OUTPUT_DIR', str(tmp_path / 'temporary'))
    payload = ff._default_judgment(1)
    payload['free_text'] = 'Synthetic software fixture with no clinical meaning or patient facts.'
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={'error': {'message': 'synthetic prefill unsupported'}})
        if len(requests) == 2:
            return completion('malformed')
        return completion(json.dumps(payload)[1:])
    model = model_with_transport(handler)
    r = runtime(tmp_path)
    state = dict(task=1, case_id='synthetic', messages=[AIMessage(content='Synthetic software parser fixture.')],
                 patient={}, refined_transcript='Synthetic unaccepted proposal text')
    with r.activate(), case_scope(1, 'synthetic'):
        result = ff.make_form_fill_node(model, max_retries=2, postprocess=False)(state)
    assert r.attempts == len(requests) == 3
    assert requests[0]['messages'][-1]['role'] == 'assistant'
    assert requests[1]['messages'][-1]['role'] == 'user'
    assert not any('DEFAULTED' in x for x in result['form_fill_warnings'])
    inp = next(e for e in events(r) if e.get('stage') == 'formfill_input')
    assert inp['reflection']['consumed'] is False
    assert inp['reflection']['acceptance'] == 'not_selected_at_input'
    assert inp['reflection']['legacy_text_present']
    assert inp['after']['transcript'] != state['refined_transcript']
    blocked = next(e for e in events(r) if e.get('stage') == 'external_reflection')
    assert blocked['status'] == 'blocked_missing_authorization'


def test_formfill_fatal_not_salvaged_or_retried(tmp_path, monkeypatch):
    monkeypatch.setenv('CHIMERA_OUTPUT_DIR', str(tmp_path / 'temporary'))
    calls = []
    model = model_with_transport(lambda q: calls.append(q) or completion('bad'))
    r = runtime(tmp_path, max_attempts=0)
    with r.activate(), pytest.raises(RuntimeFatal):
        ff.make_form_fill_node(model, max_retries=3, self_refine=True, atomic_check=True)(
            dict(task=1, case_id='synthetic', messages=[AIMessage(content='software fixture')]))
    assert not calls
    assert not any(e.get('stage') == 'parsed_judgment' for e in events(r))


@pytest.mark.parametrize('change', ['config', 'effective', 'environment', 'source', 'flags', 'purpose'])
def test_trusted_profile_rejects_identity_changes(tmp_path, monkeypatch, change):
    cfg = OmegaConf.load(ROOT / 'configs/config.yaml')
    config_path = tmp_path / 'config.yaml'
    OmegaConf.save(cfg, config_path)
    p = profile_for(monkeypatch, tmp_path, cfg, config_path)
    assert verify_profile(cfg, config_path, registry={'synthetic': p}) == p
    if change == 'config':
        config_path.write_text(config_path.read_text() + '\n# altered\n')
    elif change == 'effective':
        cfg.agent.workflow = 'unapproved'
    elif change == 'environment':
        monkeypatch.setenv('CHIMERA_COST_ALIGN', '0')
    elif change == 'source':
        p = dataclasses.replace(p, sources=p.sources[:-1])
    elif change == 'flags':
        monkeypatch.setattr(ff, '_PRE_CALIB_DUMP', not ff._PRE_CALIB_DUMP)
    else:
        p = dataclasses.replace(p, purpose='submission')
    with pytest.raises(RuntimeFatal):
        verify_identity(p, cfg, config_path)


def test_no_profile_unchanged_unknown_profile_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv('CHIMERA_ENTRY_PROFILE', raising=False)
    assert verify_profile(None, tmp_path / 'absent') is None
    monkeypatch.setenv('CHIMERA_ENTRY_PROFILE', 'unapproved')
    with pytest.raises(RuntimeFatal):
        verify_profile(None, tmp_path / 'absent')


def test_synthetic_profile_cannot_load_real_model(tmp_path):
    from chimera_agent_baseline.models import load_model
    r = runtime(tmp_path)
    with r.activate(), pytest.raises(RuntimeFatal, match='cannot load'):
        load_model(OmegaConf.create({'model': {'provider': 'openai'}}))


def test_meter_rejects_invisible_retry_configuration():
    with pytest.raises(RuntimeFatal, match='max_retries'):
        model_with_transport(lambda q: completion(), retries=2)


@pytest.mark.parametrize('mode', ['tool_role', 'assistant_calls', 'schema', 'extra_fields'])
def test_unverified_tool_templates_refused_without_content_loss(mode):
    from chimera_agent_baseline.model_context import LlamaContextGuard
    guard = object.__new__(LlamaContextGuard)
    def no_network(*args, **kwargs):
        pytest.fail('Unverified tool template reached tokenizer endpoint')
    guard._request = no_network
    guard.model_id = 'synthetic'
    messages = [HumanMessage(content='keep content')]
    if mode == 'tool_role':
        messages.append(ToolMessage(content='keep tool text', tool_call_id='call_1'))
    elif mode == 'assistant_calls':
        messages.append(AIMessage(content='keep reasoning', tool_calls=[dict(id='call_1', name='x', args={})]))
    elif mode == 'extra_fields':
        messages.append(AIMessage(content='keep reasoning', additional_kwargs={'reasoning_content': 'keep trace'}))
    with pytest.raises(ValueError):
        if mode == 'schema':
            guard.count_wire_payload(dict(model='synthetic', messages=[dict(role='user', content='keep')], tools=[{'keep': 'schema'}], max_tokens=1))
        else:
            guard(messages, 1)


def test_plain_exact_context_adapter_retains_full_prompt():
    from chimera_agent_baseline.model_context import LlamaContextGuard
    guard = object.__new__(LlamaContextGuard)
    guard.limit, guard.reserve = 100, 5
    guard.model_id = 'synthetic' 
    seen = []
    def request(path, body):
        seen.append((path, body))
        return {'prompt': 'full synthetic template'} if path == '/apply-template' else {'tokens': [1, 2, 3]}
    guard._request = request
    value = guard.count_wire_payload(dict(model='synthetic', messages=[dict(role='user', content='full content')], max_tokens=2))
    assert value['input_tokens'] == 3 and value['fits']
    assert seen[0][1]['messages'][0]['content'] == 'full content'
    assert seen[1][1]['content'] == 'full synthetic template'
    with pytest.raises(ValueError, match='model differs'):
        guard.count_wire_payload(dict(model='unapproved', messages=[], max_tokens=2))
    assert len(seen) == 2


def test_persistence_failure_is_fatal(tmp_path):
    r = runtime(tmp_path)
    r.path.unlink()
    r.path.mkdir()
    with pytest.raises(RuntimeFatal, match='persistence'):
        r.emit('synthetic')


@pytest.mark.parametrize('task', [1, 2, 3])
def test_actual_entry_stub_exports_before_cleanup_and_links_final_sockets(tmp_path, monkeypatch, task):
    import inference
    input_dir = tmp_path / 'input'
    input_dir.mkdir()
    clinical_slug = next(k for k, v in inference.CLINICAL_SLUG_TO_TASK.items() if v == task)
    (input_dir / 'inputs.json').write_text(json.dumps([
        dict(socket=dict(slug=clinical_slug, relative_path='clinical.json'))]))
    (input_dir / 'clinical.json').write_text('{}')  # no patient data
    monkeypatch.setattr(inference, 'INPUT_PATH', input_dir)
    monkeypatch.setattr(inference, 'OUTPUT_PATH', tmp_path / 'sockets')
    monkeypatch.setattr(inference, 'CONFIG_PATH', ROOT / 'configs/config.yaml')
    monkeypatch.setenv('CHIMERA_ENTRY_PROFILE', 'synthetic')
    cfg = inference._load_config(tmp_path / 'unused_input', tmp_path / 'unused_output', task)
    p = profile_for(monkeypatch, tmp_path, cfg, inference.CONFIG_PATH)
    temp_paths = []
    async def fake_run(cfg):
        monkeypatch.setenv('CHIMERA_OUTPUT_DIR', str(cfg.paths.output_dir))
        temp_paths.append(Path(cfg.paths.output_dir).parent)
        from chimera_agent_baseline.runtime_audit import consumption_event
        judgment = ff._default_judgment(task)
        judgment['free_text'] = 'Synthetic software fixture without medical interpretation.'
        consumption_event('parsed_judgment', {}, judgment, status='synthetic_stub_only')
        # Use real formfill parsing/postprocessing with synthetic transport.
        model = model_with_transport(lambda q: completion(json.dumps(judgment)[1:]))
        out = ff.make_form_fill_node(model, max_retries=1)(dict(task=task, case_id=inference.CASE_ID,
            patient={}, messages=[AIMessage(content='Software fixture only.')]))
        dest = Path(cfg.paths.output_dir) / f'task{task}' / inference.CASE_ID
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'prediction.json').write_text(json.dumps(out['structured_response']))
    monkeypatch.setattr(inference, 'run_agent', fake_run)
    monkeypatch.setattr(inference, 'start_embedding_service', lambda *a: pytest.fail('no services'))
    assert inference.run(_profile_registry={'synthetic': p}, _token_counter=counter) == 0
    assert not temp_paths[0].exists()
    roots = list((tmp_path / 'private').iterdir())
    assert len(roots) == 1 and roots[0].stat().st_mode & 0o777 == 0o700
    journal = [json.loads(line) for line in (roots[0] / 'events.jsonl').read_text().splitlines()]
    previous = None
    for e in journal:
        digest = e.pop('digest')
        assert e['previous'] == previous and content_digest(e) == digest
        e['digest'] = digest
        previous = digest
    final = next(e for e in journal if e.get('stage') == 'final_sockets')
    assert final['status'] == 'finalized' and set(final['files']) == {'decision', 'reasoning'}
    assert final['stage_digests'] and final['overwrite_history']
    assert len(list((tmp_path / 'sockets').iterdir())) == 2
    for kind, value in final['files'].items():
        data = (tmp_path / 'sockets' / value['file']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == value['sha256']
    export = next(e for e in journal if e['kind'] == 'temporary_evidence_exported')
    for relative, digest in export['files'].items():
        assert hashlib.sha256((roots[0] / 'temporary_evidence' / relative).read_bytes()).hexdigest() == digest
    assert any('prediction.json' in x for x in export['files'])


def test_fake_vllm_bind_invoke_async_share_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from chimera_agent_baseline.models import vllm_offline as backend
    calls = []
    class FakeTokenizer:
        def decode(self, *a, **k):
            return 'synthetic output'
    class FakeEngine:
        def get_tokenizer(self):
            return FakeTokenizer()
        def chat(self, **payload):
            calls.append(payload)
            return [SimpleNamespace(outputs=[SimpleNamespace(token_ids=[1], text='synthetic output')])]
    monkeypatch.setattr(backend, '_parse_tool_calls', lambda *a: [])
    model = backend.ChatVLLM(llm=FakeEngine(), sampling_params=SimpleNamespace(max_tokens=32))
    bound = model.bind_tools([dict(type='function', function=dict(name='software',
        description='fixture', parameters=dict(type='object', properties={})))])
    r = runtime(tmp_path)
    with r.activate(), case_scope(2, 'synthetic'):
        model.invoke([HumanMessage(content='software')])
        bound.invoke([HumanMessage(content='software')])
        run_async(bound.ainvoke([HumanMessage(content='software')]))
    assert r.attempts == len(calls) == 3
    assert calls[1]['tools'] == calls[2]['tools']
    assert len([e for e in events(r) if e['kind'] == 'model_response']) == 3


@pytest.mark.parametrize('mechanism', ['internal_reflection', 'atomic_verification'])
def test_optional_model_consumers_charge_and_propagate_fatal(tmp_path, monkeypatch, mechanism):
    monkeypatch.setenv('CHIMERA_OUTPUT_DIR', str(tmp_path / 'temporary'))
    judgment = ff._default_judgment(1)
    judgment['free_text'] = 'Synthetic software fixture without any patient data or medical meaning.'
    model = model_with_transport(lambda q: completion(json.dumps(judgment)[1:]))
    r = runtime(tmp_path, max_attempts=1)
    with r.activate(), pytest.raises(RuntimeFatal):
        ff.make_form_fill_node(model, max_retries=3, postprocess=False,
            self_refine=mechanism == 'internal_reflection', atomic_check=mechanism == 'atomic_verification')(
            dict(task=1, case_id='synthetic', messages=[AIMessage(content='software fixture')]))
    assert r.attempts == 1
    rejection = next(e for e in events(r) if e['kind'] == 'request_rejected')
    assert rejection['stage'] == mechanism


def test_actual_graph_requests_keep_node_identity_and_fatal(tmp_path, monkeypatch):
    from chimera_agent_baseline.agent.graph import create_graph
    monkeypatch.setenv('CHIMERA_OUTPUT_DIR', str(tmp_path / 'temporary'))
    model = model_with_transport(lambda q: completion('Synthetic software routing fixture only.'))
    graph = create_graph([], model, 'Synthetic system fixture', workflow='planned_react',
                         self_refine=False, atomic_check=False)
    r = runtime(tmp_path, max_attempts=1)
    with r.activate(), case_scope(1, 'synthetic'), pytest.raises(RuntimeFatal):
        run_async(graph.ainvoke(dict(task=1, case_id='synthetic', patient={},
            messages=[HumanMessage(content='Software fixture with no patient facts.')]),
            {'recursion_limit': 15}))
    assert r.attempts == 1
    attempt = next(e for e in events(r) if e['kind'] == 'wire_attempt')
    assert attempt['stage'] != 'model_request_unattributed'


def test_profile_approval_rejected_before_any_service_or_runner(tmp_path, monkeypatch):
    import inference
    monkeypatch.setenv('CHIMERA_ENTRY_PROFILE', 'unapproved')
    monkeypatch.setattr(inference, 'CONFIG_PATH', ROOT / 'configs/config.yaml')
    monkeypatch.setattr(inference, '_socket_paths', lambda: {})
    monkeypatch.setattr(inference, '_detect_task', lambda s: 1)
    monkeypatch.setattr(inference, '_materialise_case', lambda *a: None)
    monkeypatch.setattr(inference, 'run_agent', lambda *a: pytest.fail('unapproved runner'))
    monkeypatch.setattr(inference, 'start_embedding_service', lambda *a: pytest.fail('unapproved service'))
    with pytest.raises(RuntimeFatal, match='Unapproved'):
        inference.run()


def test_counter_and_identity_errors_are_fatal_not_ordinary_fallback(tmp_path):
    r = runtime(tmp_path, token_counter=lambda p: None)
    payload = dict(messages=[], max_tokens=1)
    with r.activate(), pytest.raises(RuntimeFatal):
        with r.wire(payload, backend='synthetic'):
            pytest.fail('invalid counter allowed wire')
    r.token_counter = counter
    r.identity_check = lambda: (_ for _ in ()).throw(OSError('removed source'))
    with r.activate(), pytest.raises(RuntimeFatal):
        with r.wire(payload, backend='synthetic'):
            pytest.fail('missing source allowed wire')


def test_final_socket_mismatch_and_symlink_export_are_fatal(tmp_path):
    r = runtime(tmp_path)
    socket = tmp_path / 'socket.json'
    socket.write_text('"actual"')
    with pytest.raises(RuntimeFatal, match='readback'):
        r.finalize_sockets(1, 'synthetic', {}, {'decision': socket}, {'decision': 'expected'})
    tree = tmp_path / 'temporary'
    tree.mkdir()
    (tree / 'link').symlink_to(socket)
    with pytest.raises(RuntimeFatal, match='Symlink'):
        r.export_tree(tree)


@pytest.mark.parametrize('path', ['stream', 'parse', 'responses'])
def test_unverified_sdk_paths_refused_before_wire(tmp_path, path):
    calls = []
    model = model_with_transport(lambda q: calls.append(q) or completion())
    r = runtime(tmp_path)
    with r.activate(), pytest.raises(RuntimeFatal):
        if path == 'stream':
            list(model.stream([HumanMessage(content='software')]))
        elif path == 'parse':
            model.invoke([HumanMessage(content='software')], response_format={'type': 'json_object'})
        else:
            model.use_responses_api = True
            model.invoke([HumanMessage(content='software')])
    assert r.attempts == len(calls) == 0


def test_failed_entry_exports_partial_evidence_and_never_finalizes(tmp_path, monkeypatch):
    import inference
    monkeypatch.setattr(inference, 'CONFIG_PATH', ROOT / 'configs/config.yaml')
    monkeypatch.setattr(inference, '_socket_paths', lambda: {})
    monkeypatch.setattr(inference, '_detect_task', lambda s: 1)
    monkeypatch.setattr(inference, '_materialise_case', lambda *a: None)
    monkeypatch.setenv('CHIMERA_ENTRY_PROFILE', 'synthetic')
    cfg = inference._load_config(tmp_path / 'unused', tmp_path / 'unused_output', 1)
    p = profile_for(monkeypatch, tmp_path, cfg, inference.CONFIG_PATH)
    temp_paths = []
    async def fail(cfg):
        root = Path(cfg.paths.output_dir)
        root.mkdir()
        (root / 'partial.json').write_text('{"synthetic": true}')
        temp_paths.append(root.parent)
        raise RuntimeFatal('synthetic failure')
    monkeypatch.setattr(inference, 'run_agent', fail)
    with pytest.raises(RuntimeFatal, match='synthetic failure'):
        inference.run(_profile_registry={'synthetic': p}, _token_counter=counter)
    assert not temp_paths[0].exists()
    journal_path = next((tmp_path / 'private').glob('*/events.jsonl'))
    rows = [json.loads(x) for x in journal_path.read_text().splitlines()]
    assert any(e['kind'] == 'run_failed' for e in rows)
    assert any(e['kind'] == 'temporary_evidence_exported' for e in rows)
    assert not any(e.get('stage') == 'final_sockets' or e['kind'] == 'run_completed' for e in rows)
    assert (journal_path.parent / 'temporary_evidence/output/partial.json').is_file()


def test_concurrent_reservations_cannot_overdraw_shared_budget(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    r = runtime(tmp_path, max_attempts=1)
    barrier = Barrier(2)
    def request():
        with r.activate():
            barrier.wait(timeout=5)
            try:
                with r.wire(dict(messages=[], max_tokens=1), backend='synthetic'):
                    return 'charged'
            except RuntimeFatal:
                return 'rejected'
    # Two concurrent callers, using the main thread plus one worker so the
    # imported numerical libraries' background threads stay within the cap.
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(request)
        results = [request(), future.result(timeout=5)]
    assert sorted(results) == ['charged', 'rejected']
    assert r.attempts == 1 and r.reserved_tokens == 8


def test_successful_real_graph_stub_counts_plan_agent_and_formfill(tmp_path, monkeypatch):
    from chimera_agent_baseline.agent.graph import create_graph
    monkeypatch.setenv('CHIMERA_OUTPUT_DIR', str(tmp_path / 'temporary'))
    # Test-local graph configuration; no approved production profile implied.
    monkeypatch.setenv('CHIMERA_FORMFILL_SELF_REFINE', '0')
    judgment = ff._default_judgment(1)
    judgment['free_text'] = 'Software routing fixture without patient facts or medical meaning.'
    requests = []
    def handler(q):
        p = json.loads(q.content)
        requests.append(p)
        if p['messages'][0]['content'] == ff._SYSTEM_PROMPT:
            return completion(json.dumps(judgment)[1:])
        return completion('Synthetic planning or agent software fixture.')
    model = model_with_transport(handler)
    graph = create_graph([], model, 'Synthetic system fixture', workflow='planned_react',
                         self_refine=False, atomic_check=False)
    r = runtime(tmp_path)
    with r.activate(), case_scope(1, 'synthetic'):
        result = run_async(graph.ainvoke(dict(task=1, case_id='synthetic', patient={},
            messages=[HumanMessage(content='Software fixture with no patient facts.')]),
            {'recursion_limit': 15}))
    attempts = [e for e in events(r) if e['kind'] == 'wire_attempt']
    assert r.attempts == len(requests) == 3
    assert [e['stage'] for e in attempts] == ['plan', 'agent', 'form_fill']
    assert 'structured_response' in result


def test_denied_request_retains_full_tool_observation_before_fatal(tmp_path):
    calls = []
    model = model_with_transport(lambda q: calls.append(q) or completion())
    r = runtime(tmp_path, max_attempts=0)
    with r.activate(), pytest.raises(RuntimeFatal):
        model.invoke([HumanMessage(content='software'),
            AIMessage(content='', tool_calls=[dict(id='c', name='x', args={})]),
            ToolMessage(content='keep complete synthetic observation', tool_call_id='c')])
    preflight = next(e for e in events(r) if e['kind'] == 'request_preflight')
    assert preflight['payload']['messages'][-1]['content'] == 'keep complete synthetic observation'
    assert calls == [] and r.attempts == 0
