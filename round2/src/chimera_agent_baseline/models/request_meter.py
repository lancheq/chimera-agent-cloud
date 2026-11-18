"""Meter at SDK create boundaries, shared by bind_tools-derived model copies.

SDK retries must be zero. Caller retries and parser/prefill fallbacks each
reach create again and get a new reservation. Unsupported streaming/parse/
Responses APIs fail explicitly before transmission under an active runtime.
"""
from contextlib import nullcontext

from chimera_agent_baseline.runtime_audit import current_runtime, RuntimeFatal


def wire_scope(payload, backend):
    runtime = current_runtime()
    return runtime.wire(payload, backend=backend) if runtime else nullcontext()


class MeteredEndpoint:
    def __init__(self, endpoint, *, asynchronous=False):
        self.endpoint, self.asynchronous = endpoint, asynchronous

    @property
    def with_raw_response(self):
        return MeteredEndpoint(self.endpoint.with_raw_response, asynchronous=self.asynchronous)

    def __getattr__(self, key):
        if current_runtime():
            raise RuntimeFatal('Unsupported metered model SDK endpoint: ' + key)
        return getattr(self.endpoint, key)

    def create(self, **payload):
        if self.asynchronous:
            return self._acreate(payload)
        if current_runtime() and payload.get('stream'):
            raise RuntimeFatal('Streaming is not approved for this finite runtime')
        with wire_scope(payload, 'openai_chat_completions') as attempt:
            response = self.endpoint.create(**payload)
            return record_response(response, attempt)

    async def _acreate(self, payload):
        if current_runtime() and payload.get('stream'):
            raise RuntimeFatal('Streaming is not approved for this finite runtime')
        with wire_scope(payload, 'openai_chat_completions') as attempt:
            response = await self.endpoint.create(**payload)
            return record_response(response, attempt)


class UnsupportedRoot:
    def __getattr__(self, key):
        raise RuntimeFatal('Root SDK/Responses/parse path requires a separately verified adapter')


def meter_openai(model):
    if model.max_retries != 0 or model.streaming:
        raise RuntimeFatal('Metered OpenAI requires SDK max_retries=0 and no streaming')
    # The SDK root options must agree, including injected clients.
    for root in (model.root_client, model.root_async_client):
        if root is None or root.max_retries != 0:
            raise RuntimeFatal('Cannot verify SDK retry setting')
    model.client = MeteredEndpoint(model.client)
    model.async_client = MeteredEndpoint(model.async_client, asynchronous=True)
    model.root_client = UnsupportedRoot()
    model.root_async_client = UnsupportedRoot()
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
