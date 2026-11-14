"""Explicit execution gate and finite batch failure handling."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
from omegaconf import OmegaConf
from pydantic import ValidationError
from tools import run_shared_plan_candidate as entry
from tools.validate_guarded_runner import fixture_config

ROOT=Path(__file__).resolve().parents[1]


def config():
    return OmegaConf.merge(OmegaConf.load(ROOT/'configs/config.yaml'),OmegaConf.load(ROOT/'configs/round2_shared_plan_candidate.yaml'))


def test_default_entry_does_not_load_or_call_model(monkeypatch,tmp_path):
    cfg=config()
    fake_cases=[SimpleNamespace(case_id='A')]
    monkeypatch.setattr(entry,'prepare',lambda *_:({'study_id':'fixture'},fake_cases))
    monkeypatch.setattr(entry,'load_model',lambda *_:pytest.fail('Dry run must not load a model'))
    monkeypatch.setattr(entry,'LlamaContextGuard',lambda *_:pytest.fail('Dry run must not contact serving endpoint'))
    monkeypatch.setattr(entry,'sha',lambda *_:'fixture-sha')
    monkeypatch.setattr(sys,'argv',['candidate','--cohort',str(tmp_path/'runtime.json'),'--output',str(tmp_path/'out')])
    entry.main()
    result=json.loads((tmp_path/'out/candidate_dry_run.json').read_text())
    assert result['status']=='ready_for_review_not_executed' and result['actual_llm_calls']==0


def test_runtime_schema_refuses_target_or_quality_metadata():
    row=dict(task=1,case_id='A',directory='/fixture/A',upfront_filename='prompt.json',
             upfront_sha256='x',clinical_sha256='y',arm_order=['fixed','adaptive'])
    for field in ('ground_truth','reference_file','quality_flags','risk_group_id'):
        with pytest.raises(ValidationError):entry.RuntimeCase.model_validate(row|{field:'not an agent input'})


def test_three_failed_pairs_stop_finite_batch_without_scoring_pending_cases(monkeypatch,tmp_path):
    cfg=config();cases=[SimpleNamespace(task=1,case_id=str(i),directory='/fixture/'+str(i),arm_order=['fixed','adaptive']) for i in range(5)]
    monkeypatch.setattr(entry,'load_cases',lambda *_ ,**__: [dict(case_id=str(i),task=1,context='fixture') for i in range(5)])
    attempted=[]
    async def fail_pair(cfg,source,query,*args,**kwargs):
        attempted.append(query['case_id'])
        return [dict(status='failed',case_id=query['case_id'],arm=a,error_type='APIConnectionError',error='outage') for a in ('fixed','adaptive')]
    monkeypatch.setattr(entry,'run_pair',fail_pair)
    result=asyncio.run(entry.execute(cfg,{'study_id':'fixture'},cases,tmp_path,object(),object()))
    assert result=='halted_for_runtime_failure' and attempted==['0','1','2']
    status=json.loads((tmp_path/'candidate_batch_status.json').read_text())
    assert status['pending_cases']==['3','4'] and status['arms_attempted']==6 and status['expected_arms']==10


def test_permission_failure_stops_batch_immediately(monkeypatch,tmp_path):
    cfg=config();cases=[SimpleNamespace(task=1,case_id=str(i),directory='/fixture/'+str(i),arm_order=['fixed','adaptive']) for i in range(2)]
    monkeypatch.setattr(entry,'load_cases',lambda *_,**__: [dict(case_id=str(i),task=1,context='fixture') for i in range(2)])
    async def fail_pair(*args,**kwargs):
        return [dict(status='failed',case_id='0',arm=a,error_type='AccessDenied',error='source mismatch') for a in ('fixed','adaptive')]
    monkeypatch.setattr(entry,'run_pair',fail_pair)
    status=asyncio.run(entry.execute(cfg,{'study_id':'fixture'},cases,tmp_path,object(),object()))
    assert status=='halted_for_runtime_failure'
    result=json.loads((tmp_path/'candidate_batch_status.json').read_text())
    assert result['pending_cases']==['1']


def test_candidate_disables_sdk_transport_retries_without_generation():
    from chimera_agent_baseline.models import load_model
    model=load_model(config())
    assert model.max_retries==0
