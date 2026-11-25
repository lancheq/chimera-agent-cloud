"""Focused guarded closure and same-F06 replay. No model endpoint is contacted."""
import asyncio
from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool, ToolException
from omegaconf import OmegaConf

import inference
from chimera_agent_baseline import guarded_runtime, information_access, run as runner, shared_plan
from chimera_agent_baseline.information_access import AccessDenied, InformationAccess
from chimera_agent_baseline.runtime_audit import PrivateRuntime, RuntimeFatal, content_digest, current_stage
from test_adaptive_host_smoke import action, config_for, files_for, ROOT
from test_p01_g1a_runtime import completion, model_with_transport, profile_for

OUT = ROOT / 'output/a_budget_close_20261119'
F06 = ROOT / 'output/a_f06_real_recheck_20261119'
SCRIPT = 'Synthetic-only formatted text based on the verified observations supplied to this fixture.'


def rows(file):
    return [json.loads(line) for line in file.read_text().splitlines()]


def wire_payload(request):
    return json.loads(request.content)


def configure(tmp_path, monkeypatch, *, budget=3):
    inp, out = tmp_path/'input', tmp_path/'sockets'
    files_for(inp, 3)
    cfg = config_for(3)
    cfg.agent.information_access.budget_units = budget
    config = tmp_path/'config.yaml'
    OmegaConf.save(cfg, config)
    monkeypatch.delenv('CHIMERA_BASE_URL', raising=False)
    for key,value in dict(INPUT_PATH=inp,OUTPUT_PATH=out,CONFIG_PATH=config).items():
        monkeypatch.setattr(inference,key,value)
    eff = inference._load_config(tmp_path/'data',tmp_path/'out',3)
    profile = replace(profile_for(monkeypatch,tmp_path,eff,config),max_attempts=12,max_reserved_tokens=195072)
    return out, {'_profile_registry':{profile.name:profile},'_token_counter':lambda body:
                 {'input_tokens':31,'fits':True,'kind':'synthetic_only'}}


def runtime_evidence(tmp_path):
    (events,) = (tmp_path/'private').glob('*/events.jsonl')
    case=events.parent/'temporary_evidence/output/task3/gc-case'
    return rows(events),case


def old_acquire():
    source = OUT/'before/src/chimera_agent_baseline/shared_plan.py'
    spec = importlib.util.spec_from_file_location('recorded_budget_baseline', source)
    prior = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prior)
    return prior.acquire


def assert_http_binding(events, captured):
    assert captured == [e['payload_digest'] for e in events if e['kind']=='http_request_verified']
    wire = [e for e in events if e['kind']=='wire_attempt']
    assert captured == [e['payload_digest'] for e in wire]
    assert all(e['context']['kind']=='synthetic_only' for e in wire)


@pytest.mark.parametrize('branch', ['before', 'after'])
def test_f06_budget_close_real_entry(tmp_path, monkeypatch, branch):
    out,args = configure(tmp_path,monkeypatch)
    if branch == 'before':
        monkeypatch.setattr(shared_plan, 'acquire', old_acquire())
    history = json.loads((F06/'review_text.json').read_text())['responses']
    script = iter(history)
    captured, stages = [], []
    expected_tools = ['get_previous_notes','get_surgical_pathology_report',
                      'get_pathology_report','get_mri_report','get_family_history']

    def replies(request):
        body = wire_payload(request)
        captured.append(content_digest(body))
        stage = current_stage()
        stages.append(stage)
        if stage in ('planner','policy'):
            original = next(script)
            assert original['stage'] == stage
            return completion(original['content'])
        if stage == 'diagnosis':
            user = body['messages'][-1]['content']
            observed, _ = json.JSONDecoder().raw_decode(user.split('Actual verified observations: ',1)[1])
            assert [v['tool'] for v in observed] == expected_tools[:4]
            assert observed[0]['observation']['previous_notes'] == []
            assert 'family_history' not in json.dumps(observed)
            assert 'months' not in json.dumps(observed)
            return completion(SCRIPT)
        assert stage == 'form_fill'
        assert SCRIPT in body['messages'][-2]['content']
        return completion(json.dumps({'event':0,'months_to_recurrence':24.0,'free_text':SCRIPT})[1:])

    monkeypatch.setattr(runner,'load_model',lambda _: model_with_transport(replies))
    if branch == 'before':
        with pytest.raises(RuntimeFatal,match='tool request was denied'):
            inference.run(**args)
    else:
        assert inference.run(**args)==0
    events,case=runtime_evidence(tmp_path)
    assert_http_binding(events,captured)
    assert stages[:5] == ['planner','policy','policy','policy','policy']
    journal=rows(case/'candidate_events.jsonl')
    attempts=[e['tool'] for e in journal if e['kind']=='tool_request']
    with sqlite3.connect(case/'information_access.sqlite') as db:
        ledger=[json.loads(r[0]) for r in db.execute('SELECT event FROM events ORDER BY seq')]
        spent,admitted=db.execute('SELECT spent,attempts FROM meta').fetchone()
        assert not db.execute('SELECT 1 FROM pending').fetchall()
    assert (spent,admitted)==(3,4)
    if branch=='before':
        assert attempts==expected_tools and len(stages)==5
        assert ledger[-1]['status']=='information_budget_exhausted'
        assert not out.exists() and events[-1]['kind']=='run_failed'
        return
    assert attempts==expected_tools[:4] and stages[5:]==['diagnosis','form_fill']
    assert len(ledger)==9 and all(e.get('allowed') is not False for e in ledger)
    stop = next(e for e in journal if e['kind']=='acquisition_stop')
    checkpoint=json.loads((case/'candidate_checkpoint.json').read_text())
    stopped=stop['closure']['stopped_action']
    assert stopped['action']['tool']=='get_family_history'
    assert stopped['preview']['remaining_units']==0 and stopped['preview']['required_units']==1
    assert stop['reason']=='information_budget_before_dispatch' and stop['requests']==4
    assert checkpoint['closure_sha256']==stop['sha256'] and checkpoint['phase']=='complete'
    assert stop['closure']['ledger']['pending_requests']==0
    audit=next(e for e in events if e.get('stage')=='final_sockets')
    assert set(audit['files'])=={'decision','reasoning'}
    for receipt in audit['files'].values():
        assert sha256((out/receipt['file']).read_bytes()).hexdigest()==receipt['sha256']
    assert json.loads((out/inference.OUTPUT_SOCKETS[3]['decision']).read_text())=={'event':0,'months_to_recurrence':24.0}
    assert json.loads((out/inference.OUTPUT_SOCKETS[3]['reasoning']).read_text())==SCRIPT


def access_for(tmp_path, *, budget=1, attempts=2, knowledge=False, initial=None):
    case=tmp_path/'task3/gc-case'
    case.mkdir(parents=True)
    clinical=case/'prostate-time-to-recurrence-or-last-follow-up-clinical-data.json'
    clinical.write_text('{"case_id":"gc-case","radiology_report":"authorized synthetic MRI"}')
    prompt=case/'prompt.json'
    prompt.write_text(json.dumps({'case_id':'gc-case',**(initial or {})}))
    options=dict(ledger_path=str(tmp_path/'access.sqlite'),session_id='bound-fixture',case_id='gc-case',task=3,
                 snapshot_id=sha256(clinical.read_bytes()).hexdigest(),budget_units=budget,max_attempts=attempts,
                 initial_sections=initial,initial_source_sha256=sha256(prompt.read_bytes()).hexdigest())
    if knowledge:
        manifest=ROOT/'output/s2_knowledge_snapshot_20261113_v4/manifest.json'
        options.update(knowledge_manifest=str(manifest),knowledge_snapshot_id=sha256(manifest.read_bytes()).hexdigest())
    return InformationAccess.from_config(case.parent,options),clinical


@pytest.mark.parametrize('next_tool',['get_mri_report','search_guidelines'])
def test_zero_balance_then_attempt_cap_and_closed_resume(tmp_path,next_tool):
    access,_ = access_for(tmp_path,knowledge=next_tool=='search_guidelines')
    cfg=config_for(3)
    acquired, paid = [],[]
    def tool(name):
        async def call(**kwargs):
            acquired.append(name)
            query=kwargs.get('query')
            def fetch():
                paid.append(name)
                return ({'query':query,'results':[]} if query is not None else
                        {'case_id':'gc-case','radiology_report':'authorized synthetic MRI'})
            return json.dumps(access.execute(name,'gc-case',fetch,query=query))
        return StructuredTool(name=name,description='synthetic contract',args_schema={'type':'object'},coroutine=call)
    tools={name:tool(name) for name in {'get_mri_report',next_tool}}
    chosen=shared_plan.Action(tool=next_tool,query='synthetic knowledge query' if next_tool=='search_guidelines' else None,
                              purpose='Keep same legal action')
    def policy(*args,**kwargs):
        return shared_plan.PolicyPlan(remaining_plan=[chosen],reason='original choice retained')
    recorded=SimpleNamespace(formal_smoke=True,structured=policy)
    case=tmp_path/'case'
    journal=shared_plan.Journal(case/'journal.jsonl')
    artifact={'plan_id':'frozen-plan','actions':[action('get_mri_report')]}
    runtime=PrivateRuntime(tmp_path/'private',max_attempts=0,max_reserved_tokens=0,token_counter=lambda _:None)
    with runtime.activate():
        state=asyncio.run(shared_plan.acquire(cfg,'adaptive',artifact,'synthetic',list(tools.values()),
            access,case,recorded,journal))
        assert state['stop_reason']=='tool_attempt_budget_before_dispatch' and acquired==['get_mri_report',next_tool]
        assert paid==(['get_mri_report','search_guidelines'] if next_tool=='search_guidelines' else ['get_mri_report'])
        closed=journal.events[-1]['closure']
        assert closed['stopped_action']['preview']['remaining_attempts']==0
        assert closed['stopped_action']['preview']['required_units']==0
        assert closed['ledger']['spent_units']==1 and closed['ledger']['admitted_attempts']==2
        assert state==asyncio.run(shared_plan.acquire(cfg,'adaptive',artifact,'synthetic',list(tools.values()),access,
            case,recorded,journal))
        state['stop_reason']='policy_stop'
        shared_plan.save_checkpoint(case/'candidate_checkpoint.json',state)
        with pytest.raises(ValueError,match='does not match checkpoint'):
            asyncio.run(shared_plan.acquire(cfg,'adaptive',artifact,'synthetic',list(tools.values()),access,
                case,recorded,journal))
        assert acquired==['get_mri_report',next_tool]


@pytest.mark.parametrize('condition,match', [
    ('tool','unknown_or_disabled_tool'), ('case','cross_case'), ('query','invalid_query'),
    ('knowledge','knowledge_snapshot_unverified'), ('source','unverified_acquisition_source'),
    ('identity','acquisition_ledger_binding_differs'), ('pending','unsettled_acquisition'),
    ('accounting','settled_acquisition_accounting_differs'),
])
def test_preview_nonbudget_failure_has_priority(tmp_path,condition,match):
    access,path=access_for(tmp_path,budget=0,attempts=0)
    name,case,query='get_mri_report','gc-case',None
    if condition=='tool':
        name='get_lab_results'
    elif condition=='case':
        case='different-case'
    elif condition=='query':
        query='not valid for clinical tool'
    elif condition=='knowledge':
        name,query='search_guidelines','valid form without KB authority'
    elif condition=='source':
        path.write_text('{}')
    else:
        with sqlite3.connect(access.path) as db:
            if condition=='identity':
                db.execute("UPDATE meta SET binding='{}'")
            elif condition=='pending':
                db.execute("INSERT INTO pending VALUES ('unknown', '[]', 0)")
            else:
                db.execute("UPDATE meta SET attempts=1")
    before=access.path.read_bytes()
    with pytest.raises(AccessDenied,match=match):
        access.preview_action(name,case,query=query)
    assert access.path.read_bytes()==before


def test_preview_partial_clinical_section_cost(tmp_path,monkeypatch):
    original=information_access.tool_contract
    def with_compound(task):
        contract=original(task)
        contract['tools']['get_mri_report']['fields'].append('pathology_report')
        return contract
    monkeypatch.setattr(information_access,'tool_contract',with_compound)
    access,_=access_for(tmp_path,budget=0,initial={'radiology_report':'shown up front'})
    before=access.path.read_bytes()
    receipt=access.preview_action('get_mri_report','gc-case')
    assert receipt['required_units']==1 and receipt['missing_keys']==['clinical:pathology_report']
    assert receipt['denial']=='information_budget_exhausted'
    assert access.path.read_bytes()==before


@pytest.mark.parametrize('failure', ['raced_denial','tool_error','bad_return','audit'])
def test_formal_initial_failure_never_reaches_diagnosis(tmp_path,monkeypatch,failure):
    out,args=configure(tmp_path,monkeypatch,budget=0 if failure=='audit' else 3)
    stage_calls=[]
    def reply(request):
        stage_calls.append(current_stage())
        assert current_stage()=='planner'
        return completion(json.dumps({'actions':[action('get_mri_report')]}))
    monkeypatch.setattr(runner,'load_model',lambda _:model_with_transport(reply))
    async def call(**kwargs):
        if failure=='raced_denial':
            raise ToolException('information_access:information_budget_exhausted')
        if failure=='bad_return':
            return json.dumps({'case_id':'wrong','radiology_report':'unbound'})
        raise RuntimeError('synthetic tool fault')
    transport_calls=[]
    async def listed(*args,**kwargs):
        async def wrapped(**kwargs):
            transport_calls.append(kwargs)
            return await call(**kwargs)
        return [StructuredTool(name='get_mri_report',description='fault fixture',
                               args_schema={'type':'object'},coroutine=wrapped)]
    monkeypatch.setattr(runner,'_load_mcp_tools',listed)
    if failure=='audit':
        append=shared_plan.Journal.append
        def cannot_seal(journal,event,**details):
            if event=='acquisition_stop':
                raise OSError('synthetic stop receipt persistence fault')
            return append(journal,event,**details)
        monkeypatch.setattr(shared_plan.Journal,'append',cannot_seal)
    with pytest.raises((RuntimeFatal,RuntimeError)):
        inference.run(**args)
    assert stage_calls==['planner']
    assert bool(transport_calls)==(failure!='audit')
    assert not out.exists()
    events,case=runtime_evidence(tmp_path)
    assert not any(e.get('stage')=='final_sockets' for e in events)
    assert not any(e['kind']=='acquisition_stop' for e in rows(case/'candidate_events.jsonl'))


def test_late_inflight_blocks_diagnosis_retry_and_sockets(tmp_path,monkeypatch):
    out,args=configure(tmp_path,monkeypatch,budget=0)
    ledger=None
    stage_calls=[]

    async def listed(cfg,input_dir,registry,task,access_path,**kwargs):
        nonlocal ledger
        ledger=Path(json.loads(access_path.read_text())['ledger_path'])

        async def forbidden(**kwargs):
            pytest.fail('Budget stop must precede the planned tool')
        return [StructuredTool(name='get_mri_report',description='synthetic tool',
                               args_schema={'type':'object'},coroutine=forbidden)]

    def reply(request):
        stage_calls.append(current_stage())
        if current_stage()=='planner':
            return completion(json.dumps({'actions':[action('get_mri_report')]}))
        assert current_stage()=='diagnosis'
        with sqlite3.connect(ledger) as db:
            db.execute("INSERT INTO pending VALUES ('synthetic-inflight', '[]', 0)")
        return completion('')  # Normally retried, but the sealed ledger has changed.

    monkeypatch.setattr(runner,'_load_mcp_tools',listed)
    monkeypatch.setattr(runner,'load_model',lambda _:model_with_transport(reply))
    with pytest.raises(RuntimeFatal,match='Acquisition closure changed before model request'):
        inference.run(**args)
    assert stage_calls==['planner','diagnosis']
    assert not out.exists()
    events,case=runtime_evidence(tmp_path)
    assert events[-1]['kind']=='run_failed'
    assert not any(e.get('stage')=='final_sockets' for e in events)
    assert any(e['kind']=='acquisition_stop' for e in rows(case/'candidate_events.jsonl'))
