"""Explicit candidate entry. Defaults to validation only, without model generation.

Use a frozen runtime-case manifest; diagnostic target paths are not accepted.
"""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from omegaconf import OmegaConf
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from chimera_agent_baseline.case_loader import load_cases
from chimera_agent_baseline.knowledge_access import load_manifest,sha
from chimera_agent_baseline.models import load_model
from chimera_agent_baseline.model_context import LlamaContextGuard
from chimera_agent_baseline.shared_plan import run_pair,validate_config,atomic_json
from chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK

ROOT=Path(__file__).resolve().parents[1]


class RuntimeCase(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    task:Literal[1,2,3]
    case_id:str
    directory:str
    upfront_filename:Literal['prompt.json','structured-prompt.json']
    upfront_sha256:str
    clinical_sha256:str
    arm_order:list[Literal['fixed','adaptive']]=Field(min_length=2,max_length=2)


def prepare(cfg,manifest):
    validate_config(cfg)
    value=json.loads(manifest.read_text())
    if set(value)!= {'schema_version','study_id','cases'} or value['schema_version']!=1:
        raise ValueError('Unexpected runtime cohort schema; targets/quality/group metadata belong in separate evaluation files')
    cases=[RuntimeCase.model_validate(row) for row in value['cases']]
    if not cases:raise ValueError('Frozen runtime cohort must be nonempty')
    if len({(x.task,x.case_id) for x in cases})!=len(cases):raise ValueError('Duplicate runtime case')
    load_manifest(cfg.agent.information_access.knowledge_manifest,require_alignment=True)
    for case in cases:
        directory=Path(case.directory)
        if not directory.is_absolute() or directory.name!=case.case_id or case.case_id in ('.','..'):
            raise ValueError('Runtime directory/case binding differs')
        if set(case.arm_order)!={'fixed','adaptive'}:raise ValueError('Both arms exactly once required')
        if sha(directory/case.upfront_filename)!=case.upfront_sha256 or sha(directory/CASE_DATA_FILENAMES_BY_TASK[case.task])!=case.clinical_sha256:
            raise ValueError('Runtime input bytes differ from frozen cohort')
    return value,cases


async def execute(cfg,cohort,cases,output,model,guard):
    query_cache={}
    rows=[]
    consecutive_failed=0
    halted=False
    for case in cases:
        key=(case.task,str(Path(case.directory).parent))
        if key not in query_cache:
            query_cache[key]={q['case_id']:q for q in load_cases(Path(case.directory).parent,task=case.task)}
        query=query_cache[key][case.case_id]
        result=await run_pair(cfg,Path(case.directory).parent,query,output,model,guard,order=case.arm_order)
        rows.extend(result)
        consecutive_failed=consecutive_failed+1 if any(r['status']!='done' for r in result) else 0
        fatal=any(r.get('error_type')=='AccessDenied' or any(word in r.get('error','').lower() for word in
                  ('binding differs','common first observation differs','receipt differs','common plan differs','source snapshot differs'))
                  for r in result)
        if fatal or consecutive_failed>=cfg.agent.shared_plan.max_consecutive_failed_pairs:
            halted=True
            break
        atomic_json(output/'candidate_batch_status.json',dict(study_id=cohort['study_id'],status='running',
            expected_arms=len(cases)*2,arms_attempted=len(rows),rows=rows))
    status='halted_for_runtime_failure' if halted else ('completed' if all(r['status']=='done' for r in rows) else 'incomplete')
    atomic_json(output/'candidate_batch_status.json',dict(study_id=cohort['study_id'],status=status,
        expected_arms=len(cases)*2,arms_attempted=len(rows),pending_cases=[x.case_id for x in cases[len(rows)//2:]],rows=rows))
    return status


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=ROOT/'configs/round2_shared_plan_candidate.yaml')
    p.add_argument('--cohort',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--execute',action='store_true',help='Generate only after explicit authorization of the reviewed batch')
    p.add_argument('--retry-failed',action='store_true',help='Resume known outputs/checkpoints, never reset a ledger')
    a=p.parse_args()
    cfg=OmegaConf.merge(OmegaConf.load(ROOT/'configs/config.yaml'),OmegaConf.load(a.config))
    cfg.paths.output_dir=str(a.output.resolve());cfg.agent.retry_failed=a.retry_failed
    cohort,cases=prepare(cfg,a.cohort)
    summary=dict(study_id=cohort['study_id'],status='ready_for_review_not_executed',cases=len(cases),arm_instances=2*len(cases),
        actual_llm_calls=0,component_trained=None,runtime_cohort_sha256=sha(a.cohort),
        cfg=OmegaConf.to_container(cfg,resolve=True),
        model_request_upper_bound=len(cases)*(cfg.agent.shared_plan.control_format_retries+
            2*(cfg.agent.shared_plan.get('diagnosis_attempts',1)+2*cfg.agent.form_fill.max_retries)+cfg.agent.shared_plan.max_policy_turns*cfg.agent.shared_plan.control_format_retries),
        information_budget_per_arm=cfg.agent.information_access.budget_units,
        tool_request_limit_per_arm=cfg.agent.shared_plan.max_actions,
        source='Runtime manifest only; reference labels and offline quality annotations are not accepted')
    a.output.mkdir(parents=True,exist_ok=True)
    if not a.execute:
        atomic_json(a.output/'candidate_dry_run.json',summary)
        print(json.dumps({k:v for k,v in summary.items() if k!='cfg'},ensure_ascii=False),flush=True)
        return
    guard=LlamaContextGuard(cfg)
    model=load_model(cfg)
    atomic_json(a.output/'candidate_execution_config.json',summary|{'status':'authorized_execution','server_context':guard.props['default_generation_settings'].get('n_ctx')})
    status=asyncio.run(execute(cfg,cohort,cases,a.output,model,guard))
    if status!='completed':raise SystemExit(1)


if __name__=='__main__':main()
