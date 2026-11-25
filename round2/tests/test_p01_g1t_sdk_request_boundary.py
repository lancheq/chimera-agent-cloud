"""In-memory SDK/body parity tests; no tokenizer service or model generation."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import httpx
import openai
import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from chimera_agent_baseline.models import request_meter
from chimera_agent_baseline.models.openai_compat import ChatOpenAICompat
from chimera_agent_baseline.runtime_audit import RuntimeFatal, content_digest
from test_p01_g1a_runtime import completion, events, run_async, runtime

BASELINE = Path(__file__).with_name('fixtures') / 'p01_g1t_sdk_shapes.json'
SHAPES = json.loads(BASELINE.read_text())['shapes']
SCHEMA = next(row['sdk_input']['response_format'] for row in SHAPES if row['id'].startswith('C01'))
PLAIN = {'model': 'synthetic-no-server', 'messages': [{'role': 'user', 'content': 'fixture'}],
         'max_completion_tokens': 12}


def sdk(handler, *, metered=True):
    transport = httpx.MockTransport(handler)
    model = ChatOpenAICompat(model='synthetic-no-server', base_url='http://synthetic.invalid/v1',
        api_key='test-only', max_tokens=32, max_retries=0,
        http_client=httpx.Client(transport=transport, trust_env=False),
        http_async_client=httpx.AsyncClient(transport=transport, trust_env=False))
    return request_meter.meter_openai(model) if metered else model


def persist(tmp_path, label, **details):
    (tmp_path / 'boundary_comparison.json').write_text(
        json.dumps({'fixture': label, **details}, indent=2, ensure_ascii=False) + '\n')


@pytest.mark.parametrize('shape', SHAPES, ids=[s['id'] for s in SHAPES])
def test_replay_frozen_g1t_shapes(shape, tmp_path):
    seen = []
    counted = []

    def handler(request):
        seen.append((json.loads(request.content), sha256(request.content).hexdigest()))
        return completion()

    def counter(effective):
        counted.append(deepcopy(effective))
        return {'input_tokens': 23, 'fits': True, 'tokenizer': 'synthetic_fixture_only'}

    model = sdk(handler)
    r = runtime(tmp_path, token_counter=counter)
    payload = deepcopy(shape['sdk_input'])
    if 'response_format' in payload:
        del payload['stream']  # LangChain does this before calling the SDK parse method.
        target = model.root_client.chat.completions.with_raw_response.parse
    else:
        target = model.client.with_raw_response.create
    with r.activate():
        if shape['id'].startswith('X01'):
            with pytest.raises(RuntimeFatal, match='extra_body'):
                target(**payload)
            assert seen == counted == [] and r.attempts == r.reserved_tokens == 0
            persist(tmp_path, shape['id'], status='rejected_before_count_and_send',
                    previous_http=shape['http_json'], sdk_input=payload, wire_attempts=0)
            return
        target(**payload).parse()

    assert len(seen) == len(counted) == r.attempts == 1
    actual, raw_digest = seen[0]
    assert actual == shape['http_json'] == counted[0]
    assert r.reserved_tokens == 23 + actual['max_completion_tokens']
    rows = events(r)
    preflight = next(e for e in rows if e['kind'] == 'request_preflight')
    attempt = next(e for e in rows if e['kind'] == 'wire_attempt')
    verified = next(e for e in rows if e['kind'] == 'http_request_verified')
    assert preflight['seq'] < attempt['seq'] < verified['seq']
    assert preflight['payload'] == actual and attempt['payload'] == actual
    assert preflight['sdk_payload_digest'] == verified['sdk_payload_digest'] == content_digest(payload)
    assert preflight['payload_digest'] == verified['payload_digest'] == content_digest(actual)
    assert verified['body_sha256'] == raw_digest and verified['effective_output_max'] == actual['max_completion_tokens']
    persist(tmp_path, shape['id'], status='sdk_http_parity', sdk_input=payload, previous_http=shape['http_json'],
            counted_body=counted[0], actual_http=actual, body_sha256=raw_digest,
            request_event=attempt['digest'], verification_event=verified['digest'], wire_attempts=r.attempts,
            synthetic_reserved_tokens=r.reserved_tokens)


@pytest.mark.parametrize('async_path', [False, True], ids=['sync', 'async'])
@pytest.mark.parametrize('outcome', ['ok', 'refusal', 'malformed_content', 'length', 'content_filter', 'http_error'])
def test_native_schema_parse_parity(async_path, outcome, tmp_path):
    wire = []

    def handler(request):
        wire.append((json.loads(request.content), sha256(request.content).hexdigest()))
        if outcome == 'http_error':
            return httpx.Response(400, json={'error': {'message': 'synthetic schema failure'}})
        data = completion('{"actions":[]}').json()
        choice = data['choices'][0]
        if outcome in ('length', 'content_filter'):
            choice['finish_reason'] = outcome
        if outcome == 'refusal':
            choice['message'].update(content=None, refusal='synthetic refusal')
        if outcome == 'malformed_content':
            choice['message']['content'] = '{not JSON'
        return httpx.Response(200, json=data)

    def run(model):
        args = ([HumanMessage(content='Structure test fixture')],)
        options = dict(response_format=deepcopy(SCHEMA), extra_body={'seed': 1, 'reasoning_budget_tokens': 0,
                       'chat_template_kwargs': {'enable_thinking': False}})
        try:
            result = run_async(model.ainvoke(*args, **options)) if async_path else model.invoke(*args, **options)
        except (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError, openai.BadRequestError) as error:
            return {'error_type': type(error).__name__}
        return {'content': result.content, 'metadata': result.additional_kwargs}

    r = runtime(tmp_path)
    expected = run(sdk(handler, metered=False))
    with r.activate():
        observed = run(sdk(handler))
    assert observed == expected and len(wire) == 2 and wire[0] == wire[1]
    last_body, _ = wire[-1]
    assert last_body['response_format'] == SCHEMA and last_body['stream'] is False
    assert r.attempts == 1 and r.reserved_tokens == 39
    event_names = [e['kind'] for e in events(r)]
    assert event_names.count('http_request_verified') == 1
    if outcome == 'http_error':
        assert expected['error_type'] == 'BadRequestError' and 'wire_error' in event_names
    elif outcome in ('length', 'content_filter'):
        assert expected['error_type'] in ('LengthFinishReasonError', 'ContentFilterFinishReasonError')
        assert 'response_parse_error' in event_names
    else:
        assert 'model_response' in event_names
        if outcome == 'refusal':
            assert expected['metadata']['refusal'] == 'synthetic refusal'
        if outcome == 'malformed_content':
            assert expected['content'] == '{not JSON'
    persist(tmp_path, f'{outcome}-{"async" if async_path else "sync"}', legacy_http=wire[0][0],
            metered_http=last_body, body_sha256=wire[-1][1], response=observed,
            wire_attempts=r.attempts, events=event_names)


@pytest.mark.parametrize('updates', [
    {'extra_body': {'max_completion_tokens': 17}},
    {'max_tokens': 12},
    {'extra_body': {'max_tokens': 12}},
    {'extra_body': {'n_predict': 17}},
    {'extra_body': {'reasoning_budget_tokns': 0}},
    {'extra_body': {'chat_template_kwargs': {'enable_thinking': False, 'custom': True}}},
    {'tool_choice': {'type': 'function', 'function': {'name': 'x'}}},
    {'stream': True},
    {'n': 2},
    {'extra_query': {'other': 'endpoint'}},
    {'messages': [{'role': 'tool', 'tool_call_id': 'unmatched', 'content': 'keep fixture'}]},
    {'messages': [{'role': 'developer', 'content': 'x'}]},
    {'messages': [{'role': ['unverified'], 'content': 'x'}]},
    {'max_completion_tokens': True},
    {'max_completion_tokens': 0},
    {'temperature': '0.3'},
    {'model': '\ud800'},
])
def test_unverified_request_never_reaches_count_or_transport(updates, tmp_path):
    model = sdk(lambda _: pytest.fail('unexpected transport call'))
    r = runtime(tmp_path, token_counter=lambda _: pytest.fail('unexpected token count'))
    with r.activate(), pytest.raises(RuntimeFatal):
        model.client.create(**(PLAIN | updates))
    assert r.attempts == r.reserved_tokens == 0
    assert not any(e['kind'] == 'http_request_verified' for e in events(r))


class TypedSchema(BaseModel):
    action: str


@pytest.mark.parametrize('schema', [{'type': 'json_object'}, TypedSchema,
    {'type': 'json_schema', 'json_schema': {'name': 'anything', 'schema': {}, 'strict': False}}])
def test_unverified_structure_is_still_blocked(schema, tmp_path):
    model = sdk(lambda _: pytest.fail('unexpected transport call'))
    r = runtime(tmp_path, token_counter=lambda _: pytest.fail('unexpected token count'))
    with r.activate(), pytest.raises(RuntimeFatal):
        model.invoke([HumanMessage(content='fixture')], response_format=schema)
    assert r.attempts == 0


@pytest.mark.parametrize('reason', ['serialization_changed', 'encoder_failed', 'transport_failed'])
def test_verified_sdk_attempt_is_charged_even_if_it_fails(reason, tmp_path, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        raise httpx.ConnectError('synthetic offline transport failure')

    model = sdk(handler)
    root = model.client.boundary.root
    original = root._build_request

    def build(*args, **kwargs):
        if reason == 'encoder_failed':
            raise ValueError('synthetic encoder failed')
        request = original(*args, **kwargs)
        # This injects a different serialized body before the sole HTTP hook.
        return httpx.Request(request.method, request.url, json=PLAIN | {'max_completion_tokens': 17})

    if reason != 'transport_failed':
        monkeypatch.setattr(root, '_build_request', build)
    r = runtime(tmp_path)
    error = {'encoder_failed': ValueError, 'serialization_changed': RuntimeFatal,
             'transport_failed': openai.APIConnectionError}[reason]
    with r.activate(), pytest.raises(error):
        model.client.create(**PLAIN)
    assert r.attempts == 1 and r.reserved_tokens == 19
    assert len(seen) == (reason == 'transport_failed')
    assert [e['kind'] for e in events(r)].count('wire_error') == 1


def test_budget_exhaustion_has_no_http_verification_or_send(tmp_path):
    model = sdk(lambda _: pytest.fail('unexpected transport call'))
    r = runtime(tmp_path, max_reserved_tokens=18)
    with r.activate(), pytest.raises(RuntimeFatal, match='budget exhausted'):
        model.client.create(**PLAIN)
    assert r.attempts == r.reserved_tokens == 0
    assert not any(e['kind'] == 'http_request_verified' for e in events(r))


def test_counter_cannot_modify_effective_output_limit(tmp_path):
    model = sdk(lambda _: pytest.fail('unexpected transport call'))

    def invalid_counter(body):
        body['max_completion_tokens'] = 1
        return {'input_tokens': 7, 'fits': True}

    r = runtime(tmp_path, token_counter=invalid_counter)
    with r.activate(), pytest.raises(RuntimeFatal, match='modified'):
        model.client.create(**PLAIN)
    assert r.attempts == 0


def test_removed_http_hook_stops_before_reservation(tmp_path):
    model = sdk(lambda _: pytest.fail('unexpected transport call'))
    model.client.boundary.client.event_hooks['request'].clear()
    r = runtime(tmp_path, token_counter=lambda _: pytest.fail('unexpected token count'))
    with r.activate(), pytest.raises(RuntimeFatal, match='configuration changed'):
        model.client.create(**PLAIN)
    assert r.attempts == 0


def test_sdk_versions_are_frozen(monkeypatch):
    monkeypatch.setattr(request_meter, 'version', lambda _: 'modified')
    with pytest.raises(RuntimeFatal, match='versions'):
        sdk(lambda _: pytest.fail('unexpected transport call'))
