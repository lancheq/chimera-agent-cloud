"""Account for the fixed OpenAI SDK chat body before sending it."""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from hashlib import sha256
from importlib.metadata import version
import json
from types import SimpleNamespace

import httpx
import openai
from openai._base_client import AsyncHttpxClientWrapper, SyncHttpxClientWrapper

from chimera_agent_baseline.message_norm import normalize_payload
from chimera_agent_baseline.runtime_audit import RuntimeFatal, content_digest, current_runtime


_SDK_VERSIONS = {'openai': '2.48.0', 'httpx': '0.28.1', 'langchain-openai': '1.4.1',
                 'langchain-core': '1.5.1', 'pydantic': '2.13.4'}
_OUTPUT_KEYS = {'max_tokens', 'max_completion_tokens'}
_STANDARD = _OUTPUT_KEYS | {
    'model', 'messages', 'temperature', 'top_p', 'stream', 'stop', 'tools',
    'tool_choice', 'parallel_tool_calls', 'response_format', 'seed',
}
_EXTRAS = {'seed', 'reasoning_budget_tokens', 'chat_template_kwargs'}
_ROLES = {'system', 'user', 'assistant', 'tool'}
_HTTP_REQUEST = ContextVar('metered_openai_http_request', default=None)


def wire_scope(payload, backend):
    runtime = current_runtime()
    return runtime.wire(payload, backend=backend) if runtime else nullcontext()


def effective_chat_body(payload, *, parse=False):
    """Normalize only the pinned SDK subset and leave the caller's arguments intact."""
    extra = payload.get('extra_body', {})
    standard = {k: v for k, v in payload.items() if k != 'extra_body'}
    if type(extra) is not dict or set(standard) & set(extra):
        raise RuntimeFatal('Conflicting or invalid OpenAI extra_body')
    if len((set(standard) | set(extra)) & _OUTPUT_KEYS) != 1:
        raise RuntimeFatal('Exactly one SDK output limit is required')
    if set(standard) - _STANDARD or set(extra) - _EXTRAS:
        raise RuntimeFatal('Unknown OpenAI chat request option')
    if 'stream' in standard and (parse or standard['stream'] is not False):
        raise RuntimeFatal('Streaming is not approved for this finite runtime')
    structured = standard.get('response_format')
    if parse:
        if (type(structured) is not dict or set(structured) != {'type', 'json_schema'}
                or structured['type'] != 'json_schema'):
            raise RuntimeFatal('Only the verified JSON schema parse path is supported')
        wrapper = structured['json_schema']
        if (type(wrapper) is not dict or set(wrapper) != {'name', 'strict', 'schema'}
                or wrapper['strict'] is not True or not isinstance(wrapper['name'], str)
                or not wrapper['name'] or type(wrapper['schema']) is not dict
                or {'tools', 'tool_choice', 'parallel_tool_calls'} & set(standard)):
            raise RuntimeFatal('Unsupported OpenAI JSON schema parse options')
        standard['stream'] = False  # parse() emits this in the JSON body itself.
    elif 'response_format' in standard:
        raise RuntimeFatal('Structured output requires the verified raw parse route')

    body = standard | extra
    maximum = body.get('max_completion_tokens', body.get('max_tokens'))
    if type(maximum) is not int or maximum <= 0:
        raise RuntimeFatal('OpenAI requests require a positive integer output limit')
    if not isinstance(body.get('model'), str) or not body['model']:
        raise RuntimeFatal('Missing OpenAI model identity')
    if 'tool_choice' in body and body['tool_choice'] not in ('auto', 'none', 'required'):
        raise RuntimeFatal('Unsupported OpenAI tool_choice')
    if 'parallel_tool_calls' in body and type(body['parallel_tool_calls']) is not bool:
        raise RuntimeFatal('Invalid OpenAI parallel tool flag')
    if 'seed' in body and type(body['seed']) is not int:
        raise RuntimeFatal('Invalid OpenAI seed')
    for key in ('temperature', 'top_p'):
        if key in body and (type(body[key]) not in (float, int) or not 0 <= body[key] <= (1 if key == 'top_p' else 2)):
            raise RuntimeFatal('Invalid OpenAI sampling option: ' + key)
    if 'stop' in body:
        stop = body['stop']
        if not (isinstance(stop, str) or type(stop) is list and all(isinstance(x, str) for x in stop)):
            raise RuntimeFatal('Invalid OpenAI stop option')
    if 'chat_template_kwargs' in extra:
        kwargs = extra['chat_template_kwargs']
        if type(kwargs) is not dict or set(kwargs) != {'enable_thinking'} or type(kwargs['enable_thinking']) is not bool:
            raise RuntimeFatal('Unverified OpenAI template option')
    if 'reasoning_budget_tokens' in extra and (
            type(extra['reasoning_budget_tokens']) is not int or extra['reasoning_budget_tokens'] < 0):
        raise RuntimeFatal('Invalid OpenAI reasoning budget')
    _validate_messages(body.get('messages'))
    if 'tools' in body:
        tools = body['tools']
        if type(tools) is not list or any(
                type(t) is not dict or set(t) != {'type', 'function'} or t['type'] != 'function'
                or type(t['function']) is not dict or set(t['function']) - {'name', 'description', 'parameters'}
                or not isinstance(t['function'].get('name'), str)
                or ('parameters' in t['function'] and type(t['function']['parameters']) is not dict)
                or ('description' in t['function'] and not isinstance(t['function']['description'], str))
                for t in tools):
            raise RuntimeFatal('Unverified OpenAI tool definition')
    try:
        # Accept only JSON-native values. SDK sentinels, typed schemas and
        # implicit coercions cannot cross the audited serialization boundary.
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        if json.loads(encoded) != body:
            raise ValueError('JSON changes the request')
        json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError) as error:
        raise RuntimeFatal('Unverified OpenAI JSON request encoding') from error
    return body


def _validate_messages(messages):
    if type(messages) is not list or not messages:
        raise RuntimeFatal('Invalid OpenAI message list')
    pending = set()
    for message in messages:
        if (type(message) is not dict or not isinstance(message.get('role'), str)
                or message['role'] not in _ROLES):
            raise RuntimeFatal('Unsupported OpenAI message role')
        role = message['role']
        keys = {'role', 'content', 'name'}
        if role == 'assistant':
            keys.add('tool_calls')
        elif role == 'tool':
            keys.add('tool_call_id')
        if set(message) - keys or ('name' in message and not isinstance(message['name'], str)):
            raise RuntimeFatal('Unsupported OpenAI message metadata')
        if not isinstance(message.get('content'), str) and not (
                role == 'assistant' and 'content' in message and message['content'] is None
                and message.get('tool_calls')):
            raise RuntimeFatal('Unsupported OpenAI message content')
        if pending and role != 'tool':
            raise RuntimeFatal('Missing OpenAI tool result for assistant call')
        if role == 'tool':
            call_id = message.get('tool_call_id')
            if not isinstance(call_id, str) or call_id not in pending:
                raise RuntimeFatal('Unmatched OpenAI tool result ID')
            pending.remove(call_id)
        if 'tool_calls' in message:
            calls = message['tool_calls']
            if type(calls) is not list or not calls:
                raise RuntimeFatal('Invalid OpenAI assistant tool calls')
            for call in calls:
                if (type(call) is not dict or set(call) != {'id', 'type', 'function'}
                        or call['type'] != 'function' or not isinstance(call['id'], str) or not call['id']
                        or call['id'] in pending or type(call['function']) is not dict
                        or set(call['function']) != {'name', 'arguments'}
                        or not isinstance(call['function']['name'], str)
                        or not isinstance(call['function']['arguments'], str)):
                    raise RuntimeFatal('Invalid OpenAI assistant tool call')
                pending.add(call['id'])
    if pending:
        raise RuntimeFatal('Missing final OpenAI tool result')


def _unique_object(pairs):
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise ValueError('Duplicate serialized OpenAI JSON key')
    return obj


class HTTPBoundary:
    def __init__(self, root, *, asynchronous):
        self.root = root
        self.client = root._client
        self.url = root.base_url.join('chat/completions')
        allowed = ((httpx.AsyncClient, openai.DefaultAsyncHttpxClient, AsyncHttpxClientWrapper) if asynchronous
                   else (httpx.Client, openai.DefaultHttpxClient, SyncHttpxClientWrapper))
        if type(self.client) not in allowed or any(self.client.event_hooks.values()):
            raise RuntimeFatal('Unverified OpenAI HTTP client or hooks')
        self.client.follow_redirects = False
        self.hook = self._async_verify if asynchronous else self._verify
        self.client.event_hooks['request'].append(self.hook)

    async def _async_verify(self, request):
        self._verify(request)

    def _verify(self, request):
        runtime = current_runtime()
        if runtime is None:
            return
        state = _HTTP_REQUEST.get()
        if (state is None or state.boundary is not self or state.seen
                or request.method != 'POST' or request.url != self.url
                or request.headers.get('Content-Type') != 'application/json'):
            raise RuntimeFatal('OpenAI request bypassed the verified single HTTP boundary')
        try:
            actual = content_digest(json.loads(request.content, object_pairs_hook=_unique_object))
        except (ValueError, TypeError, httpx.RequestNotRead) as error:
            raise RuntimeFatal('Unverified serialized OpenAI HTTP body') from error
        if state.payload_digest != actual:
            raise RuntimeFatal('Serialized OpenAI HTTP body differs from budgeted request')
        runtime.emit('http_request_verified', attempt=state.attempt, payload_digest=actual,
                     sdk_payload_digest=state.sdk_payload_digest, body_sha256=sha256(request.content).hexdigest(),
                     effective_output_max=state.maximum)
        state.seen = True

    def _verify_client(self):
        if (self.root._client is not self.client or self.root.max_retries != 0 or self.client.follow_redirects
                or self.client.event_hooks != {'request': [self.hook], 'response': []}
                or self.root.base_url.join('chat/completions') != self.url):
            raise RuntimeFatal('OpenAI SDK HTTP boundary configuration changed')

    @contextmanager
    def submit(self, runtime, payload, *, parse):
        self._verify_client()
        try:
            effective = effective_chat_body(payload, parse=parse)
        except RuntimeFatal as error:
            runtime.emit('request_rejected', backend='openai_chat_completions',
                         reason='unverified_sdk_request', detail=str(error))
            raise
        digest = content_digest(effective)
        sdk_digest = content_digest(payload)
        with runtime.wire(effective, backend='openai_chat_completions', sdk_payload_digest=sdk_digest) as attempt:
            self._verify_client()
            state = SimpleNamespace(boundary=self, seen=False, attempt=attempt, payload_digest=digest,
                                    sdk_payload_digest=sdk_digest,
                                    maximum=effective.get('max_completion_tokens', effective.get('max_tokens')))
            token = _HTTP_REQUEST.set(state)
            try:
                yield attempt
                if not state.seen:
                    raise RuntimeFatal('OpenAI SDK returned without verifying its serialized request')
            finally:
                _HTTP_REQUEST.reset(token)


class MeteredEndpoint:
    def __init__(self, endpoint, boundary, *, asynchronous=False, structured=False, raw=False):
        self.endpoint, self.boundary = endpoint, boundary
        self.asynchronous, self.structured, self.raw = asynchronous, structured, raw

    @property
    def with_raw_response(self):
        return MeteredEndpoint(self.endpoint.with_raw_response, self.boundary,
                               asynchronous=self.asynchronous, structured=self.structured, raw=True)

    def __getattr__(self, key):
        if current_runtime() or self.structured:
            raise RuntimeFatal('Unsupported metered model SDK endpoint: ' + key)
        return getattr(self.endpoint, key)

    def create(self, **payload):
        if self.structured:
            raise RuntimeFatal('Root chat route supports raw parse only')
        return self._call('create', payload)

    def parse(self, **payload):
        if not (self.structured and self.raw and current_runtime()):
            raise RuntimeFatal('Only metered raw chat JSON schema parse is supported')
        return self._call('parse', payload)

    def _call(self, method, payload):
        target = getattr(self.endpoint, method)
        if self.asynchronous:
            return self._acall(target, method, payload)
        runtime = current_runtime()
        if runtime is None:
            return target(**payload)
        payload = normalize_payload(payload)
        with self.boundary.submit(runtime, payload, parse=method == 'parse') as attempt:
            response = target(**payload)
        return record_response(response, attempt)

    async def _acall(self, target, method, payload):
        runtime = current_runtime()
        if runtime is None:
            return await target(**payload)
        payload = normalize_payload(payload)
        with self.boundary.submit(runtime, payload, parse=method == 'parse') as attempt:
            response = await target(**payload)
        return record_response(response, attempt)


class ChatRoot:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)

    def __getattr__(self, key):
        raise RuntimeFatal('Unsupported OpenAI root/Responses SDK route')


def meter_openai(model):
    if any(version(package) != expected for package, expected in _SDK_VERSIONS.items()):
        raise RuntimeFatal('OpenAI SDK serialization versions are unverified')
    if model.max_retries != 0 or model.streaming:
        raise RuntimeFatal('Metered OpenAI requires SDK max_retries=0 and no streaming')
    sync, asynchronous = model.root_client, model.root_async_client
    if (type(sync) is not openai.OpenAI or type(asynchronous) is not openai.AsyncOpenAI
            or sync.max_retries != 0 or asynchronous.max_retries != 0
            or model.client is not sync.chat.completions or model.async_client is not asynchronous.chat.completions):
        raise RuntimeFatal('Cannot verify OpenAI SDK clients and retry settings')
    sync_boundary = HTTPBoundary(sync, asynchronous=False)
    async_boundary = HTTPBoundary(asynchronous, asynchronous=True)
    model.client = MeteredEndpoint(model.client, sync_boundary)
    model.async_client = MeteredEndpoint(model.async_client, async_boundary, asynchronous=True)
    model.root_client = ChatRoot(MeteredEndpoint(sync.chat.completions, sync_boundary, structured=True))
    model.root_async_client = ChatRoot(MeteredEndpoint(asynchronous.chat.completions, async_boundary,
                                                      asynchronous=True, structured=True))
    return model


class RecordedRawResponse:
    def __init__(self, response, runtime, attempt):
        self.response, self.runtime, self.attempt = response, runtime, attempt

    def __getattr__(self, key):
        return getattr(self.response, key)

    def parse(self, *args, **kwargs):
        try:
            value = self.response.parse(*args, **kwargs)
        except BaseException as exc:
            self.runtime.emit('response_parse_error', attempt=self.attempt, error_type=type(exc).__name__)
            raise
        self.runtime.emit('model_response', attempt=self.attempt, response=value)
        return value


def record_response(response, attempt):
    runtime = current_runtime()
    if runtime is None:
        return response
    if hasattr(response, 'parse'):
        return RecordedRawResponse(response, runtime, attempt)
    runtime.emit('model_response', attempt=attempt, response=response)
    return response
