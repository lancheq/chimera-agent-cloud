import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import threading
import pytest
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from chimera_agent_baseline.information_access import InformationAccess,AccessDenied,BoundCaseDataStore


def test_checked_in_contract_matches_actual_registry():
    from chimera_agent_baseline.information_access import tool_contract
    snapshot=json.loads((Path(__file__).resolve().parents[1]/'configs/tool_access_cost_v1.json').read_text())
    assert snapshot['tasks']=={str(t):tool_contract(t) for t in (1,2,3)}
    assert snapshot['currency_prices'] is None and snapshot['units_are_proxy']


def make(tmp_path,**kwargs):
    options={'ledger_path':tmp_path/'ledger.sqlite','session_id':'run','case_id':'A','task':1,
        'snapshot_id':'input-v1','budget_units':1,'max_attempts':8,**kwargs}
    return InformationAccess(**options)


def test_budget_and_case_denial_precede_backend_read(tmp_path):
    x=make(tmp_path,budget_units=0);calls=[]
    for tool,case in [('get_mri_report','A'),('get_mri_report','B'),('get_image_predictor','A')]:
        with pytest.raises(AccessDenied):x.execute(tool,case,lambda:calls.append('READ'))
    assert calls==[] and x.snapshot()['spent_units']==0


def test_cache_persists_across_instances_without_recharging(tmp_path):
    x=make(tmp_path);payload={'case_id':'A','radiology_report':'synthetic source'}
    assert x.execute('get_mri_report','A',lambda:payload)==payload
    y=make(tmp_path)
    assert y.execute('get_mri_report','A',lambda:pytest.fail('cached read'))==payload
    assert y.snapshot()['spent_units']==1 and y.snapshot()['admitted_attempts']==2
    assert y.require_visible('A',['radiology_report'])=={'radiology_report':'synthetic source'}
    with pytest.raises(AccessDenied):y.require_visible('A',['laboratory_results'])
    with pytest.raises(ValueError,match='binding differs'):make(tmp_path,budget_units=9)


def test_failures_and_missing_sections_only_consume_attempts(tmp_path):
    x=make(tmp_path)
    def fail():raise RuntimeError('secret backend state must not leak')
    with pytest.raises(AccessDenied,match='^fetch_failed$'):x.execute('get_mri_report','A',fail)
    x.execute('get_pathology_report','A',lambda:{'case_id':'A'})
    s=x.snapshot();assert s['admitted_attempts']==2 and s['spent_units']==s['reserved_units']==0 and not s['visible_sections']
    x.execute('get_lab_results','A',lambda:{'case_id':'A','laboratory_results':[]})
    assert x.snapshot()['spent_units']==1  # Explicit empty section is information.


@pytest.mark.parametrize('payload',[{'case_id':'B','radiology_report':'wrong patient'},
    {'case_id':'A','radiology_report':'ok','hidden_reference':'must not return'}, {'error':'backend failure'}])
def test_undeclared_wrong_case_or_error_output_never_becomes_visible(tmp_path,payload):
    x=make(tmp_path)
    with pytest.raises(AccessDenied):x.execute('get_mri_report','A',lambda:payload)
    assert not x.snapshot()['visible_sections'] and x.snapshot()['spent_units']==0


def test_concurrent_calls_reserve_budget_before_fetch(tmp_path):
    x=make(tmp_path);entered=threading.Event();finish=threading.Event()
    def slow():entered.set();assert finish.wait(5);return {'case_id':'A','radiology_report':'source'}
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(x.execute,'get_mri_report','A',slow)
        assert entered.wait(5)
        try:
            with pytest.raises(AccessDenied,match='information_budget'):make(tmp_path).execute('get_lab_results','A',lambda:pytest.fail('overbudget fetch'))
            with pytest.raises(AccessDenied,match='request_pending'):make(tmp_path).execute('get_mri_report','A',lambda:pytest.fail('duplicate fetch'))
            assert x.snapshot()['reserved_units']==1
        finally:finish.set()
        future.result()
    assert x.snapshot()['spent_units']==1 and x.snapshot()['reserved_units']==0


def test_initial_sections_are_free_but_require_binding(tmp_path):
    with pytest.raises(ValueError,match='source binding'):make(tmp_path,initial_sections={'radiology_report':'already visible'})
    x=make(tmp_path,budget_units=0,initial_sections={'radiology_report':'already visible'},initial_source_sha256='trusted-upfront')
    assert x.execute('get_mri_report','A',lambda:pytest.fail('upfront read'))['radiology_report']=='already visible'
    assert x.snapshot()['spent_units']==0


def test_versioned_knowledge_has_zero_information_fee_and_bounded_attempts(tmp_path):
    x=make(tmp_path,budget_units=0,max_attempts=2,knowledge_snapshot_id='kb-v1')
    fetch=lambda:{'query':'q','results':[{'text':'synthetic guideline'}]}
    x.execute('search_guidelines','A',fetch,query='q')
    x.execute('search_guidelines','A',lambda:pytest.fail('cache not used'),query='q')
    assert x.snapshot()['spent_units']==0
    with pytest.raises(AccessDenied,match='attempt_budget'):x.execute('search_guidelines','A',fetch,query='other')
    assert x.snapshot()['visible_sections']==[]


def test_unverified_knowledge_cannot_be_retrieved(tmp_path):
    x=make(tmp_path)
    with pytest.raises(AccessDenied,match='knowledge_snapshot'):x.execute('search_guidelines','A',lambda:pytest.fail('unverified search'),query='q')


def test_real_stdio_reconnections_share_budget_and_protocol_errors(tmp_path):
    case=tmp_path/'task1'/'A';case.mkdir(parents=True)
    clinical=case/'prostate-biopsy-decision-clinical-data.json'
    clinical.write_text(json.dumps({'case_id':'A','radiology_report':'SYNTHETIC_MRI',
        'laboratory_results':[{'val':'MUST_NOT_RETURN_LAB'}]}))
    cfg={'ledger_path':str(tmp_path/'stdio.sqlite'),'session_id':'stdio-run','case_id':'A','task':1,
         'snapshot_id':hashlib.sha256(clinical.read_bytes()).hexdigest(),'budget_units':1,'max_attempts':8}
    config=tmp_path/'access.json';config.write_text(json.dumps(cfg))
    env={'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src'),'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2'}
    async def call(name,args):
        params=StdioServerParameters(command=sys.executable,args=['-m','chimera_agent_baseline.mcp_server',
            '--data-dir',str(case.parent),'--access-session',str(config)],env=env)
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer) as session:
                await session.initialize()
                return await session.call_tool(name,args)
    # Each call opens a NEW server process. Memory-only budgets would fail here.
    a=asyncio.run(call('get_mri_report',{'case_id':'A'}));assert not a.isError
    b=asyncio.run(call('get_lab_results',{'case_id':'A'}));assert b.isError
    assert 'MUST_NOT_RETURN_LAB' not in str(b)
    c=asyncio.run(call('get_mri_report',{'case_id':'A'}));assert not c.isError
    d=asyncio.run(call('get_mri_report',{'case_id':'B'}));assert d.isError
    e=asyncio.run(call('search_guidelines',{'query':'q'}));assert e.isError
    assert InformationAccess(**cfg).snapshot()['spent_units']==1


def test_mcp_config_checks_actual_upfront_values_and_snapshot(tmp_path):
    case=tmp_path/'task1'/'A';case.mkdir(parents=True)
    c=case/'prostate-biopsy-decision-clinical-data.json';c.write_text('{"case_id":"A"}')
    p=case/'prompt.json';p.write_text('{"radiology_report":"actually upfront"}')
    cfg={'ledger_path':str(tmp_path/'x.sqlite'),'session_id':'x','case_id':'A','task':1,
        'snapshot_id':hashlib.sha256(c.read_bytes()).hexdigest(),'budget_units':0,'max_attempts':2,
        'initial_sections':{'radiology_report':'invented upfront'},'initial_source_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    with pytest.raises(ValueError,match='not actually upfront'):InformationAccess.from_config(case.parent,cfg)
    with pytest.raises(ValueError,match='absolute'):InformationAccess.from_config(case.parent,{**cfg,'ledger_path':'relative.sqlite'})
    cfg['initial_sections']['radiology_report']='actually upfront'
    x=InformationAccess.from_config(case.parent,cfg);assert x.require_visible('A',['radiology_report'])
    c.write_text('{"case_id":"A","changed":true}')
    with pytest.raises(ValueError,match='snapshot differs'):InformationAccess.from_config(case.parent,cfg)


def test_bound_loader_cannot_be_overridden_by_another_directory(tmp_path):
    a=tmp_path/'task1/A';b=tmp_path/'task1/B';a.mkdir(parents=True);b.mkdir()
    name='prostate-biopsy-decision-clinical-data.json'
    (a/name).write_text('{"case_id":"A","radiology_report":"safe A"}')
    (b/name).write_text('{"case_id":"A","radiology_report":"B pretending to be A"}')
    x=make(tmp_path,snapshot_id=hashlib.sha256((a/name).read_bytes()).hexdigest())
    store=BoundCaseDataStore(a.parent,x)
    assert store.extract('A',('radiology_report',))['radiology_report']=='safe A'
    (a/name).write_text('{"case_id":"A","radiology_report":"mutated"}')
    with pytest.raises(ValueError,match='snapshot changed'):store.extract('A',('radiology_report',))
