"""Read back two preserved native R11 runs, then replay the same fixture as a string."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import sys
from tempfile import TemporaryDirectory
from time import monotonic

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
REG = ROOT / 'output/b12_regression_20261122'
PLAN = ROOT / 'output/b12_regression_planning_review_20261122'
sys.path.insert(0, str(REG))
from review_saved import rows as regression_rows
from matrix import LATE, formal_cutoff
from chimera_agent_baseline.information_access import digest
from chimera_agent_baseline.runtime_audit import canonical, content_digest


def hash_bytes(x):
    return sha256(x).hexdigest()


def fingerprint(path):
    data = path.read_bytes()
    return {'sha256': hash_bytes(data), 'bytes': len(data)}


def read(path):
    return json.loads(path.read_bytes())


def relative(path):
    return str(path.relative_to(ROOT))


def event_rows(path):
    result = [json.loads(line) for line in path.read_bytes().splitlines()]
    previous = None
    for i, event in enumerate(result):
        assert (event['seq'], event['previous']) == (i, previous), (path, i)
        assert content_digest({k:v for k,v in event.items() if k != 'digest'}) == event['digest'], (path, i)
        previous = event['digest']
    return result


def one(items):
    item, = items
    return item


def has_late(value):
    return LATE in canonical(value)


def inspect_r11(workdir, evidence, transport):
    native = transport == 'native_stdio'
    audit = workdir / evidence['audit_log']
    assert fingerprint(audit)['sha256'] == evidence['audit_sha256']
    rows = event_rows(audit)
    snapshot_path = one(audit.parent.rglob('information_access_snapshot.json'))
    snapshot = read(snapshot_path)
    returned = one(r for r in snapshot['events'] if r['status'] == 'returned')
    reserved = one(r for r in snapshot['events'] if r['status'] == 'reserved')
    assert returned['request_id'] == reserved['request_id']
    assert returned['allowed'] is True and returned['success'] is True and returned['charged_units'] == 1
    assert (snapshot['spent_units'], snapshot['admitted_attempts'], snapshot['reserved_units']) == (1,1,0)
    assert not snapshot['pending_requests'] and returned['fields'] == ['laboratory_results']

    stages = {s: one(r for r in rows if r['stage']==s and r['kind']=='consumption')
              for s in ('formfill_input','formfill_request_input','graph_output','prediction_file','final_sockets')}
    b = one(r for r in rows if r['stage']=='b_evidence_agent_input' and
            any(s['status']=='after_cutoff' for s in r['sources']))
    late = one(s for s in b['sources'] if s['status']=='after_cutoff')
    eligible = one(s for s in b['sources'] if s.get('reference',{}).get('path') == ['laboratory_results',0,'val'])
    assert eligible['status']=='eligible' and late['reference']['path']==['laboratory_results',1,'val']
    assert late['receipt'] == eligible['receipt']
    receipt = late['receipt']
    assert (returned['request_id'],returned['response_digest'],returned['timestamp_unix']) == (
        receipt['request_id'],receipt['response_digest'],receipt['acquired_unix'])
    assert returned['session_id']==b['binding']['session_id'] and returned['case_id']==b['binding']['case_id']
    assert b['binding']['run_id']==b['run_id'] and b['binding']['task']==1
    assert eligible['times']['available']=='2020-01-04' and late['times']['available']=='2020-08-02'
    assert late['times']['cutoff']=='2020-07-01' and not has_late(b['after'])
    assert not any(o['reference'].get('path')==['laboratory_results',1,'val'] for o in b['after']['observations'])
    assert b['after']['relations'][0]['status']=='same_observation_conflict_unresolved'

    form_state=stages['formfill_input']['after']
    tool = one(x for x in form_state['messages'] if x['type']=='tool')
    assert tool['tool_call_id']==receipt['tool_call_id'] and tool['name']=='get_lab_results' and has_late(tool)
    assert not has_late(form_state['transcript']) and tool['name'] in form_state['tool_order']
    conversions=[r for r in rows if r['stage']=='b_mcp_sdk_text']
    assert len(conversions)==int(native)
    normalized = None
    if native:
        projected=conversions[0]
        normalized=projected['native_result']
        assert normalized['isError'] is False and normalized.get('_meta') is None
        block=one(normalized['content'])
        assert block['type']=='text' and block['text']==normalized['structuredContent']['result']
        assert projected['receipt']==receipt and projected['binding']==b['binding']
        assert projected['before']==tool['content'] and tool['artifact']=={'structured_content':normalized['structuredContent']}
        text=projected['after']
        assert text==block['text']==one(tool['content'])['text']
        assert projected['native_result_digest']==digest(normalized)
    else:
        assert isinstance(tool['content'],str)
        text=tool['content']
    data=json.loads(text)
    assert digest(data)==receipt['response_digest'] and data['laboratory_results'][0]['val']==6
    assert data['laboratory_results'][1]['val']==LATE

    attempts = [e for e in rows if e['kind']=='wire_attempt']
    preflights = [e for e in rows if e['kind']=='request_preflight']
    http = [e for e in rows if e['kind']=='http_request_verified']
    responses = [e for e in rows if e['kind']=='model_response']
    assert len(attempts)==len(http)==len(preflights)==len(responses)==3
    send_list=[]
    for i,(pre, attempt, outbound, response) in enumerate(zip(preflights,attempts,http,responses,strict=True),1):
        assert pre['seq']<attempt['seq']<outbound['seq']<response['seq']
        assert attempt['attempt']==outbound['attempt']==response['attempt']==i
        assert pre['payload']==attempt['payload']
        payload = attempt['payload']
        pd = content_digest(payload)
        assert pd==pre['payload_digest']==attempt['payload_digest']==outbound['payload_digest']
        assert pre['sdk_payload_digest']==attempt['sdk_payload_digest']==outbound['sdk_payload_digest']
        assert not has_late(response['response'])
        found=[idx for idx,m in enumerate(payload['messages']) if has_late(m)]
        if i==2:
            assert found==[3]
            message=payload['messages'][3]
            assert message['role']=='tool' and message['tool_call_id']==receipt['tool_call_id'] and message['content']==text
        else:
            assert not found
        send_list.append({'attempt':i,'stage':attempt['stage'],'wire_seq':attempt['seq'],
                          'http_seq':outbound['seq'],'payload_sha256':pd,
                          'http_body_sha256':outbound['body_sha256'],'late_message_indices':found,
                          'allowed_lab_row_in_tool_message':i==2,'output_ceiling':attempt['effective_output_max'],
                          'count_unit':'fixture UTF-8 bytes','input_count':attempt['context']['input_tokens']})
    assert [e['payload_sha256'] for e in send_list]==evidence['http_payload_digests']
    assert [e['stage'] for e in send_list]==['agent','agent','form_fill']
    final=stages['final_sockets']
    assert not has_late(final['after']) and final['files']==evidence['socket_files']
    assert rows[-1]['kind']=='run_completed'
    output_path = audit.parents[2] / 'sockets'
    assert output_path.is_dir()
    files = {k:{'file':str((output_path/v['file']).relative_to(workdir)),'sha256':v['sha256']}
             for k,v in final['files'].items()}
    for v in files.values():
        assert fingerprint(workdir/v['file'])['sha256']==v['sha256']
    local_paths = {p:fingerprint(workdir/p) for p in
                   (str(audit.relative_to(workdir)),str(snapshot_path.relative_to(workdir)),
                    *[v['file'] for v in files.values()])}
    optional_reflection = {r['stage']:r['status'] for r in rows if r['kind']=='consumption'
                           and r['stage'] in ('internal_reflection','external_reflection','atomic_verification','reasoning_align')}
    return {
        'transport':transport,'run_directory':relative(workdir),
        'audit_log':relative(audit),'ledger_snapshot':relative(snapshot_path),
        'files_relative_to_run':local_paths,'scope':b['binding'],'receipt':receipt,
        'ledger_status':returned['status'],'b_time_status':late['status'],
        'synthetic_tool_admitted':1,'synthetic_units_spent':1,'pending':0,
        'eligible_laboratory_row':eligible['reference']['path'],'eligible_available':eligible['times']['available'],
        'late_reference':late['reference']['path'],'late_times':late['times'],
        'tool_text_utf8_sha256':hash_bytes(text.encode()),'funded_response_document_digest':digest(data),
        'normalized_native_result_digest':digest(normalized) if native else None,
        'native_stdio_raw_frames_captured':False,
        'graph_tool_content_type':type(tool['content']).__name__,
        'graph_tool_artifact_has_raw':has_late(tool.get('artifact')),
        'b_projection_event':{'seq':b['seq'],'late_in_projection':False},
        'actual_sdk_requests':send_list,
        'graph_output_private_trace_has_raw':has_late(stages['graph_output']['after']),
        'formfill_state_messages_has_raw':has_late(form_state['messages']),
        'formfill_state_seq':stages['formfill_input']['seq'],
        'formfill_transcript_has_raw':False,
        'formfill_request_preparation_has_raw':has_late(stages['formfill_request_input']['after']),
        'mock_responses_contain_sentinel':False,
        'socket_values_contain_sentinel':False,'sockets':files,
        'review_layer':optional_reflection,'distinct_adaptive_or_common_diagnosis':'NO_APPLICABLE_ENTRY_IN_R11',
    }


def comparable_requests(readback):
    return [{k:v for k,v in row.items() if k not in ('wire_seq','http_seq')}
            for row in readback['actual_sdk_requests']]


def source_readbacks():
    h = read(REG/'handoff.json')
    assert h['status']=='HOLD' and h['frozen_before']==h['frozen_after']
    expected_pins = h['frozen_after'] | h['deliverables']
    current_pins = {name:fingerprint(ROOT/name) for name in expected_pins}
    changes = {name:{'frozen':v,'present':current_pins[name]} for name,v in expected_pins.items()
               if v!=current_pins[name]}
    plan_name='discussion/B12_EXECUTION_PLAN_20261122.md'
    assert set(changes) <= {plan_name}, changes
    if changes:
        plan=(ROOT/plan_name).read_text()
        matrix=dict((r,{'condition':c,'required':x})
                    for r,c,x in re.findall(r'^\| (R\d{2}) ([^|]+?) \| ([^|]+?) \|$',plan,re.M))
        assert matrix==read(REG/'frozen_baseline.json')['plan_expected']
    rows = regression_rows(REG/'expected_actual.jsonl')
    assert [{k:r[k] for k in ('rid','case','probe','inputs','expected')} for r in rows] == read(REG/'expected.json')
    reg_row=one(r for r in rows if r['case']=='formal_sdk_late_body')
    acceptance=read(PLAN/'acceptance.json')
    assert acceptance['status']=='PARTIAL'
    independent=PLAN/'r11_independent.json'
    assert fingerprint(independent)['sha256']==acceptance['r11_replay_sha256']
    # The acceptance's legacy key actually pins the handoff; the handoff pins the report.
    assert fingerprint(REG/'handoff.json')['sha256']==acceptance['source_report_sha256']
    assert fingerprint(ROOT/'reports/B12_REGRESSION_MATRIX.md')==h['deliverables']['reports/B12_REGRESSION_MATRIX.md']
    other=read(independent)
    assert reg_row['actual']==other['actual']
    a=inspect_r11(REG,reg_row['evidence'],'native_stdio')
    b=inspect_r11(PLAN,other['evidence'],'native_stdio')
    assert a['actual_sdk_requests']==b['actual_sdk_requests']
    assert a['tool_text_utf8_sha256']==b['tool_text_utf8_sha256']
    extra=(REG/'handoff.json', PLAN/'acceptance.json', independent,
           ROOT/'reports/B12_REGRESSION_PLANNING_REVIEW_20261122.md',
           ROOT/'discussion/tasks/20261122_B12_R11_BOUNDARY.md',
           ROOT/'output/b_time_eligibility_20261118/late_evidence_isolation.md',
           ROOT/'docs/ROUND2_TOOL_ACCESS_COST_CONTRACT_20261113.md')
    pins=current_pins | {relative(p):fingerprint(p) for p in extra}
    for item in (a,b):
        for p,f in item['files_relative_to_run'].items():
            pins[relative(ROOT/item['run_directory']/p)]=f
    assert all(fingerprint(ROOT/p)==f for p,f in pins.items())
    return a,b,pins,changes


def replay_string(target):
    from pytest import MonkeyPatch
    import test_b_evidence_reconciliation_build as formal
    run_entry=formal.run_entry

    def string_control(work,env,task,mode):
        # Select the existing string-return test route. Production functions remain untouched.
        assert (task,mode)==(1,'stdio_on')
        return run_entry(work,env,task,'on')

    with MonkeyPatch.context() as patch:
        patch.setattr(formal,'run_entry',string_control)
        actual,evidence=formal_cutoff('r11_string', target/'case')
    assert actual=={'sdk_sends':3,'late_in_b_projection':False,'late_sdk_request_indexes':[1],
                    'ledger_status':'after_cutoff','two_sockets':True}
    return inspect_r11(target,evidence,'string_fastmcp_handler')


def main(target):
    assert len(os.sched_getaffinity(0))==1
    class SerialPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            loop=super().new_event_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            return loop
    policy=SerialPolicy()
    policy.set_child_watcher(asyncio.SafeChildWatcher())
    asyncio.set_event_loop_policy(policy)
    start=monotonic()
    native, independent, pins, changes = source_readbacks()
    target.mkdir(mode=0o700, exist_ok=True)
    string=replay_string(target)
    assert comparable_requests(string)==comparable_requests(native)
    assert string['tool_text_utf8_sha256']==native['tool_text_utf8_sha256']
    after={p:fingerprint(ROOT/p) for p in pins}
    assert after==pins
    rss=resource.getrusage(resource.RUSAGE_SELF)
    return {'native_regression':native,'native_independent':independent,'string_live_replay':string,
            'protected_before':pins,'protected_after':after,
            'planning_doc_history':changes,
            'evidence_level':'real local MCP stdio for two frozen runs; fresh string control through actual FastMCP handler; all inference MockTransport',
            'resources':{'elapsed_seconds':round(monotonic()-start,4),'cpu_user_sec':round(rss.ru_utime,4),
                         'cpu_system_sec':round(rss.ru_stime,4),'max_rss_kib':rss.ru_maxrss,
                         'active_cpu_cores':list(os.sched_getaffinity(0)),'workers':1,
                         'mock_sdk_requests_fresh':3,'synthetic_acquisition_calls_fresh':1,'synthetic_information_units':1,
                         'real_models':0,'gpu':0,'clinical_tools':0,'real_information_units':0,'real_cases':0}}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--capture',action='store_true')
    mode.add_argument('--check',action='store_true')
    args=parser.parse_args()
    os.umask(0o077)
    if args.capture:
        result=main(OUT/'string_replay')
        with (OUT/'boundary_readback.json').open('x',encoding='utf-8') as f:
            json.dump(result,f,indent=2,ensure_ascii=False)
            f.write('\n')
    else:
        pinned=read(OUT/'boundary_readback.json')
        with TemporaryDirectory(prefix='recheck.',dir=OUT) as folder:
            result=main(Path(folder))
        assert pinned['protected_after']==result['protected_after']
        assert pinned['string_live_replay']['actual_sdk_requests']==result['string_live_replay']['actual_sdk_requests']
        assert pinned['string_live_replay']['tool_text_utf8_sha256']==result['string_live_replay']['tool_text_utf8_sha256']
    print(json.dumps(result['resources'],ensure_ascii=False))
