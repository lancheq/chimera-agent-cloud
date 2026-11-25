"""MCP representation checks on software-only messages and return objects."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.messages.content import create_text_block
from mcp import types

from chimera_agent_baseline.agent.evidence_reconciliation import EvidenceSession
from chimera_agent_baseline.information_access import digest
from chimera_agent_baseline.runtime_audit import RuntimeFatal
from test_b_evidence_reconciliation_build import CASE, LAB, UPFRONT, binding, fixture
from test_p01_g1a_runtime import run_async

NAME = 'get_lab_results'
CALL_ID = 'software-only-tool-id'
RAW = json.dumps(LAB, indent=2)
TEXT = types.TextContent(type='text', text=RAW)


def make(variant):
    session = EvidenceSession(fixture(), 'synthetic-run', binding(), UPFRONT)
    call = dict(id=CALL_ID, name=NAME, args={'case_id': CASE})
    request = SimpleNamespace(name=NAME, args=call['args'], server_name='chimera',
                              runtime=SimpleNamespace(tool_call_id=CALL_ID))
    native = types.CallToolResult(content=[TEXT], structuredContent={'result': RAW})
    event = dict(status='returned', allowed=True, success=True, session_id=session.binding['session_id'],
                 case_id=CASE, tool=NAME, fields=['laboratory_results'], response_digest=digest(LAB),
                 request_id='software-only-receipt', timestamp_unix=20)
    if variant == 'multiple_native':
        native.content.append(TEXT)
    if variant == 'mixed_native':
        native.content.append(types.ImageContent(type='image', data='c29mdHdhcmU=', mimeType='image/png'))
    if variant == 'embedded':
        native.content = [types.EmbeddedResource(type='resource',
            resource=types.TextResourceContents(uri='file:///software-only', text=RAW))]
    if variant == 'image':
        native.content = [types.ImageContent(type='image', data='c29mdHdhcmU=', mimeType='image/png')]
    if variant == 'extra_native':
        native.content = [types.TextContent(type='text', text=RAW, unknown=None)]
    if variant == 'unknown_result_fields':
        native = types.CallToolResult(content=[TEXT], structuredContent={'result': RAW}, other=None)
    if variant == 'structured_mismatch':
        native.structuredContent = {'result': 'unverified text'}
    if variant in ('malformed_native', 'duplicate_json'):
        text = '{' if variant == 'malformed_native' else RAW.replace('"val": 6', '"val": 9, "val": 6')
        native = types.CallToolResult(content=[types.TextContent(type='text', text=text)],
                                      structuredContent={'result': text})
    if variant == 'native_error':
        native.isError = True
    if variant == 'unknown_source':
        request.server_name = 'unregistered-source'
    if variant == 'wrong_native_case':
        request.args = {'case_id': 'elsewhere'}
    if variant == 'other_runtime_id':
        request.runtime.tool_call_id = 'unmatched'

    async def handler(req):
        assert req is request
        return native

    if variant != 'self_reported_success':
        assert run_async(session.attest_mcp_result(request, handler)) is native

    block = create_text_block(RAW)
    if variant == 'unknown_lc_field':
        block['unapproved'] = None
    if variant == 'lc_invalid_id':
        block['id'] = 'not-the-installed-id-format'
    if variant == 'different_lc_text':
        block['text'] = RAW + ' '
    message = ToolMessage(content=[block], name=NAME, tool_call_id=CALL_ID,
                          artifact={'structured_content': {'result': RAW}},
                          additional_kwargs={'information_access_clinical_success': True})
    if variant == 'self_reported_success':
        message.additional_kwargs['receipt'] = event
    if variant == 'unknown_lc_metadata':
        message.additional_kwargs['other'] = None
    if variant == 'artifact_mismatch':
        message.artifact['structured_content']['unknown'] = True
    if variant == 'lc_mixed':
        message.content.append({'type': 'custom', 'text': 'Unverified'})
    if variant == 'wrong_session':
        event = event | {'session_id': 'previous-run'}
    if variant == 'cache':
        event = event | {'status': 'cache_returned'}
    before = [{'status': 'session_initialized'}]
    after = [*before, event]
    if variant == 'skipped_settlement':
        after = before
    if variant == 'duplicate_call':
        calls = [call, call | {'id': 'ambiguous'}]
    else:
        calls = [call]
    session.observe(calls, [message], before, after)
    return session, message


@pytest.mark.parametrize('variant', [
    'multiple_native', 'mixed_native', 'embedded', 'image', 'extra_native', 'unknown_result_fields',
    'structured_mismatch', 'malformed_native', 'duplicate_json', 'native_error', 'unknown_source',
    'wrong_native_case', 'other_runtime_id', 'self_reported_success', 'unknown_lc_field', 'lc_invalid_id',
    'different_lc_text', 'lc_mixed', 'unknown_lc_metadata', 'artifact_mismatch',
    'wrong_session', 'cache', 'skipped_settlement', 'duplicate_call',
    'duplicate_history',
])
def test_noncanonical_tool_content_does_not_reach_sdk(variant):
    session, message = make(variant)
    messages = [message, message] if variant == 'duplicate_history' else [message]
    with pytest.raises(RuntimeFatal, match='Unbound or unsupported'):
        session.sdk_messages(messages)


def test_sdk_copy_preserves_exact_text_order_and_source_messages():
    session, message = make('positive')
    untouched = deepcopy(message)
    leading = HumanMessage(content='Synthetic only')
    sdk_view = session.sdk_messages([leading, message])
    assert sdk_view[0] is leading and sdk_view[1].content == RAW and sdk_view[1].tool_call_id == CALL_ID
    assert message == untouched and sdk_view[1] is not message
    assert session.sources[1].receipt['request_id'] == 'software-only-receipt'


def test_legacy_string_and_expected_error_are_unchanged():
    session, _ = make('self_reported_success')
    original = ToolMessage(content=RAW, name=NAME, tool_call_id=CALL_ID)
    denial = ToolMessage(content='Error: information_access:cross_case',
                         name=NAME, tool_call_id='denied', status='error')
    view = session.sdk_messages([original, denial])
    assert view[0] is original and view[1] is denial
