"""S3 complete fixed/adaptive software candidate through real case-bound MCP.

Arbitrary scripted labels are software fixtures, never clinical evidence.
"""
import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import re
from langchain_core.messages import AIMessage, HumanMessage
from omegaconf import OmegaConf
from chimera_agent_baseline.agent.evidence_features import decode_payload
from chimera_agent_baseline.case_loader import load_cases
from chimera_agent_baseline.knowledge_access import sha
from chimera_agent_baseline.output.schema import eligible_variables
from chimera_agent_baseline.shared_plan import run_pair
from chimera_agent_baseline.model_context import LlamaContextGuard
from tools.validate_guarded_runner import fixture_config

ROOT=Path(__file__).resolve().parents[1]


def candidate_config(root,manifest,task=1):
    cfg,source=fixture_config(root,task=task)
    cfg=OmegaConf.merge(cfg,OmegaConf.load(ROOT/'configs/round2_shared_plan_candidate.yaml'))
    cfg.paths.output_dir=str(root/'candidate_output')
    cfg.agent.information_access.knowledge_manifest=str(manifest.resolve())
    cfg.agent.information_access.run_id='synthetic-shared-plan-acceptance-v1'
    for cid,marker in [('A','ENOUGH'),('B','NEEDS_GUIDELINE')]:
        path=source/cid/'prompt.json'
        raw=json.loads(path.read_text());raw.pop('task',None);path.write_text(json.dumps(raw))
        from chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK
        clinical=source/cid/CASE_DATA_FILENAMES_BY_TASK[task]
        raw=json.loads(clinical.read_text());raw['radiology_report']='PERMITTED_MRI_'+cid+'_'+marker
        raw['laboratory_results']=[{'val':'PERMITTED_AFTER_TOOL_LAB_'+cid}]
        clinical.write_text(json.dumps(raw))
        (source/cid/'data_quality_annotations.json').write_text('{"value":"UNPAID_QUALITY_MARKER"}')
    return cfg,source


def fixture_token_guard(messages,limit):
    return {'tokenizer':'scripted_engineering_fixture_not_a_real_token_estimate','fits':True,'max_new_tokens':limit}


class CandidateModel:
    def __init__(self):
        self.inputs=[]

    def invoke(self,messages,**kwargs):
        self.inputs.append(dict(messages=deepcopy(messages),options=deepcopy(kwargs)))
        text='\n'.join(str(m.content) for m in messages)
        human=next(m.content for m in messages if isinstance(m,HumanMessage))
        if str(messages[0].content).startswith('[SHARED_INITIAL_PLAN]'):
            second='get_lab_results' if 'get_lab_results' in text else 'get_pathology_report'
            actions=[{'tool':'get_mri_report','purpose':'Inspect the report'},
                     {'tool':second,'purpose':'Inspect the next clinical section'}]
            return AIMessage(content=json.dumps({'actions':actions}))
        if str(messages[0].content).startswith('[ADAPTIVE_ACQUISITION]'):
            if 'NEEDS_GUIDELINE' in text and '"tool": "search_guidelines"' not in text:
                value=dict(action='execute',remaining_plan=[dict(tool='search_guidelines',
                    query='EAU prostate cancer biopsy indications PI-RADS PSA density',purpose='Review the guideline')],reason='POLICY_ONLY_MARKER')
            else:
                value=dict(action='stop',remaining_plan=[],reason='POLICY_ONLY_MARKER')
            return AIMessage(content=json.dumps(value))
        if '[COMMON_DIAGNOSIS]' in text:
            return AIMessage(content='Synthetic assessment based on the permitted observations. Unknown information remains unknown. This is an engineering fixture.')
        if str(messages[0].content).startswith('You are filling out'):
            task=int(re.search(r'Task: (\d)',human)[1])
            called={'get_mri_report'}
            if 'get_lab_results' in human:called.add('get_lab_results')
            value=dict(repeat_test=None,free_text='Synthetic software response with no clinical interpretation; used only for interface validation.')
            if task in (1,2):value.update(confidence='uncertain',variable_weights={k:'not_used' for k in eligible_variables(task,called)})
            if task==1:value['biopsy_decision']='yes'
            elif task==2:value['treatment_recommendation']={'primary':'active_surveillance'}
            else:value.update(event=0,months_to_recurrence=24.0)
            raw=json.dumps(value)
            return AIMessage(content=raw[1:] if messages[-1].content=='{' else raw)
        raise AssertionError('Unknown fixture model stage')


async def validate(root,manifest):
    cfg,source=candidate_config(root,manifest)
    model=CandidateModel()
    token_guard=LlamaContextGuard(cfg)
    rows=[]
    for query in load_cases(source,task=1):
        order=('fixed','adaptive') if query['case_id']=='A' else ('adaptive','fixed')
        result=await run_pair(cfg,source,query,Path(cfg.paths.output_dir),model,token_guard,order=order)
        assert all(x['status']=='done' for x in result),result
        by_arm={x['arm']:x for x in result}
        assert by_arm['fixed']['plan_id']==by_arm['adaptive']['plan_id']
        assert by_arm['fixed']['spent_units']==2 and by_arm['adaptive']['spent_units']==1
        assert by_arm['fixed']['requests']==2
        assert by_arm['adaptive']['requests']==(1 if query['case_id']=='A' else 2)
        first=[]
        for arm in ('fixed','adaptive'):
            case=Path(cfg.paths.output_dir)/arm/'task1'/query['case_id']
            checkpoint=json.loads((case/'candidate_checkpoint.json').read_text())
            observations=checkpoint['observations']
            first.append(decode_payload(observations[0]['data']['content']))
            assert 'UNPAID_QUALITY_MARKER' not in json.dumps(checkpoint)
            assert (case/'information_access_receipt.json').is_file()
        assert first[0]==first[1]
        rows.extend(result)
    plan_inputs=[x for x in model.inputs if x['messages'][0].content.startswith('[SHARED_INITIAL_PLAN]')]
    assert len(plan_inputs)==2  # One per case, never a fresh planner for the second arm.
    assert all('PERMITTED_MRI_' not in str(x['messages']) and 'PERMITTED_AFTER_TOOL_LAB_' not in str(x['messages']) for x in plan_inputs)
    diagnosis_inputs=[x for x in model.inputs if '[COMMON_DIAGNOSIS]' in str(x['messages'])]
    assert len(diagnosis_inputs)==4 and all('POLICY_ONLY_MARKER' not in str(x['messages']) for x in diagnosis_inputs)
    for call in model.inputs:
        text='\n'.join(str(m.content) for m in call['messages'])
        assert 'UNPAID_QUALITY_MARKER' not in text
    before={str(p):sha(p) for p in Path(cfg.paths.output_dir).rglob('*') if p.is_file() and p.name in
            ('prediction.json','trace.json','information_access_receipt.json','candidate_events.jsonl','candidate_checkpoint.json','initial_plan.json')}
    calls=len(model.inputs)
    for query in load_cases(source,task=1):
        result=await run_pair(cfg,source,query,Path(cfg.paths.output_dir),model,token_guard)
        assert all(x['status']=='done' and x['resumed'] for x in result),result
    assert len(model.inputs)==calls and all(sha(Path(p))==value for p,value in before.items())
    report=dict(stage='S3_P6_engineering_not_performance',status='passed',synthetic_case_scenarios=2,arm_instances=4,
        common_planner_generations=2,scripted_model_calls=calls,actual_diagnostic_llm_calls=0,
        actual_knowledge_retrievals=1,initial_plan_reused=True,common_first_observation_identical=True,
        fixed_plan_preserved=True,adaptive_stops_or_retrieves_from_observation=True,policy_text_excluded_from_diagnosis=True,
        completed_resume_preserved=True,token_accounting='actual loaded llama.cpp chat template/tokenizer before each scripted response; no generation requests',rows=rows)
    (root/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


async def validate_task_adapter(root,manifest,task):
    cfg,source=candidate_config(root,manifest,task=task)
    model=CandidateModel();guard=LlamaContextGuard(cfg)
    query=load_cases(source,task=task)[0]
    result=await run_pair(cfg,source,query,Path(cfg.paths.output_dir),model,guard)
    assert all(r['status']=='done' for r in result),result
    assert result[0]['plan_id']==result[1]['plan_id']
    assert result[0]['spent_units']==2 and result[1]['spent_units']==1
    summary=dict(task=task,status='passed',synthetic_case_scenarios=1,arm_instances=2,
                 actual_diagnostic_llm_calls=0,scripted_model_calls=len(model.inputs),rows=result)
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise SystemExit('Use new acceptance output')
    os.environ['CHIMERA_TRACE_CONTENT_LIMIT']='0'
    os.environ['CHIMERA_STRICT_RUNTIME']='1'
    primary=asyncio.run(validate(a.output,a.manifest))
    adapters=[asyncio.run(validate_task_adapter(a.output/'task_adapters'/f'task{t}',a.manifest,t)) for t in (2,3)]
    report={'stage':'S3_P6_candidate_all_task_engineering','status':'passed','primary':primary,'task_adapters':adapters,'actual_diagnostic_llm_calls':0}
    (a.output/'all_task_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False),flush=True)
