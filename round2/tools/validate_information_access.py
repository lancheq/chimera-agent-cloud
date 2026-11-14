#!/usr/bin/env python3
"""Real stdio, synthetic-only S2 acceptance. No LLM or medical target labels."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from chimera_agent_baseline.information_access import InformationAccess


async def validate(out):
    if out.exists():raise FileExistsError('Preserve previous acceptance artifacts')
    out.mkdir(parents=True)
    root=Path(__file__).resolve().parents[1]
    case=out/'task1/A';case.mkdir(parents=True)
    file=case/'prostate-biopsy-decision-clinical-data.json'
    file.write_text(json.dumps({'case_id':'A','radiology_report':'SYNTHETIC_MRI_A',
        'laboratory_results':[{'val':'SYNTHETIC_LAB_MUST_NOT_BE_RETURNED'}]}))
    cfg={'session_id':'s2-synthetic-stdio','case_id':'A','task':1,
        'ledger_path':str((out/'ledger.sqlite').resolve()),'snapshot_id':hashlib.sha256(file.read_bytes()).hexdigest(),
        'budget_units':1,'max_attempts':8}
    config=out/'access_session.json';config.write_text(json.dumps(cfg,indent=2)+'\n')
    env={k:os.environ[k] for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS') if k in os.environ}
    env['PYTHONPATH']=str(root/'src')
    rows=[]
    requests=[('get_mri_report',{'case_id':'A'},False),('get_mri_report',{'case_id':'A'},False),
        ('get_lab_results',{'case_id':'A'},True),('get_mri_report',{'case_id':'B'},True),
        ('search_guidelines',{'query':'synthetic question'},True)]
    for name,args,expected_error in requests:
        started=time.monotonic()
        params=StdioServerParameters(command=sys.executable,args=['-m','chimera_agent_baseline.mcp_server',
            '--data-dir',str(case.parent.resolve()),'--access-session',str(config.resolve())],env=env)
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer) as session:
                await session.initialize();result=await session.call_tool(name,args)
        assert bool(result.isError)==expected_error
        assert 'SYNTHETIC_LAB_MUST_NOT_BE_RETURNED' not in str(result)
        rows.append({'tool':name,'arguments':args,'new_server_process':True,'expected_error':expected_error,
            'isError':bool(result.isError),'content':[b.model_dump(mode='json') for b in result.content],
            'elapsed_seconds':time.monotonic()-started})
    state=InformationAccess(**cfg).snapshot()
    assert state['spent_units']==1 and state['reserved_units']==0 and state['admitted_attempts']==2
    assert state['visible_sections']==['radiology_report']
    assert len({e['request_id'] for e in state['events'] if 'request_id' in e})==5
    summary={'stage':'S2_P4_and_partial_P5_engineering','real_MCP_invocations':len(rows),
        'new_server_process_per_invocation':True,'spent_information_units':1,'admitted_attempts':2,
        'cached_return_without_new_information_cost':True,'budget_cross_case_unverified_knowledge_denied':True,
        'clinical_source_hash':cfg['snapshot_id'],'new_LLM_calls':0,'GT_read':False,
        'production_defaults_changed':False,'whole_agent_authorization_complete':False,
        'remaining':['per-case runner binding','planner/few-shot/predictor/modalities visibility','knowledge resource manifest','full shared state and graph acceptance'],
        'source_sha256':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in
            ['src/chimera_agent_baseline/information_access.py','src/chimera_agent_baseline/mcp_server.py','configs/tool_access_cost_v1.json']}}
    for name,data in [('transport_calls.json',rows),('ledger_snapshot.json',state),('summary.json',summary)]:
        (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',required=True,type=Path)
    asyncio.run(validate(p.parse_args().output_dir))
