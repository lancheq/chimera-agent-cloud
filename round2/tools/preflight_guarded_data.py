"""Bind official input records to guarded sessions without LLMs or targets."""
import argparse
import builtins
from collections import Counter
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import time
from omegaconf import OmegaConf
from chimera_agent_baseline.case_loader import load_cases
from chimera_agent_baseline.guarded_runtime import prepare_case
from chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK

ROOT=Path(__file__).resolve().parents[1]


@contextmanager
def input_read_boundary(roots):
    original_open, original_io_open=builtins.open,io.open
    allowed={'prompt.json','structured-prompt.json',*CASE_DATA_FILENAMES_BY_TASK.values()}
    events={'source_reads':0,'forbidden_attempts':[]}
    def checked(open_fn,path,*args,**kwargs):
        if isinstance(path,(str,Path)):
            resolved=Path(path).resolve()
            if any(resolved.is_relative_to(root) for root in roots):
                if resolved.name not in allowed:
                    events['forbidden_attempts'].append(str(resolved))
                    raise PermissionError('Reference/sidecar input read refused by preflight boundary')
                events['source_reads']+=1
        return open_fn(path,*args,**kwargs)
    builtins.open=lambda p,*a,**k:checked(original_open,p,*a,**k)
    io.open=lambda p,*a,**k:checked(original_io_open,p,*a,**k)
    try:yield events
    finally:builtins.open,io.open=original_open,original_io_open


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool',type=Path,required=True)
    p.add_argument('--source',default='phase2_train_release_20261104')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise SystemExit('Use new preflight output; preserve prior ledgers')
    pool=json.loads(a.pool.read_text())
    records=[r for r in pool['cases'] if r['source']==a.source]
    registered_bytes={item['path']:item['sha256'] for item in pool['assets']
                      if item.get('role')=='input' and item.get('sha256')}
    if not records:raise ValueError('Registered source not found')
    a.output.mkdir(parents=True)
    task_roots={t:Path(next(r['directory'] for r in records if r['task']==t)).parent.resolve() for t in sorted({r['task'] for r in records})}
    cfg=OmegaConf.load(ROOT/'configs/config.yaml')
    cfg.agent.predictor.enabled=False;cfg.agent.predictor.decision_override=False;cfg.agent.predictor.decision_override_tasks=[]
    cfg.agent.max_model_turns=5
    cfg.agent.information_access=dict(enabled=True,run_id='official-input-adapter-preflight-v1',budget_units=0,max_attempts=0)
    cfg.paths.output_dir=str(a.output)
    results=[];started=time.monotonic()
    with input_read_boundary(list(task_roots.values())) as boundary:
        queries={t:{q['case_id']:q for q in load_cases(path,task=t)} for t,path in task_roots.items()}
        for record in records:
            t,cid=record['task'],record['case_id']
            source=Path(record['directory'])
            upfront=next(p for p in (source/'prompt.json',source/'structured-prompt.json') if p.is_file())
            clinical=source/CASE_DATA_FILENAMES_BY_TASK[t]
            before={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in [('prompt',upfront),('clinical',clinical)]}
            row=dict(record_index=record['record_index'],task=t,case_id=cid,source=a.source,input_hashes=before)
            try:
                if any(before[k]!=registered_bytes.get(str(path)) for k,path in [('prompt',upfront),('clinical',clinical)]):
                    raise ValueError('Registered source input bytes differ')
                _,access,bound=prepare_case(cfg,task_roots[t],t,queries[t][cid],a.output/f'task{t}'/cid)
                assert f'Task: {t}' in bound['context']
                assert access.snapshot()['spent_units']==access.snapshot()['admitted_attempts']==0
                after={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in [('prompt',upfront),('clinical',clinical)]}
                if before!=after:raise ValueError('Input modified during adapter preflight')
                row.update(status='passed',input_unchanged=True,bound_task=t,initial_sections=access.snapshot()['visible_sections'])
            except Exception as exc:
                row.update(status='failed',error=type(exc).__name__+': '+str(exc))
            results.append(row)
    summary=dict(stage='S2_official_input_adapter_not_performance',source=a.source,
        counts_by_task=dict(Counter(str(r['task']) for r in results)),total=len(results),
        passed=sum(r['status']=='passed' for r in results),failures=[r for r in results if r['status']=='failed'],
        elapsed_seconds=time.monotonic()-started,read_boundary=boundary,actual_diagnostic_llm_calls=0,
        targets_opened=0,training=0,independent_patient_claim=False)
    (a.output/'records.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    (a.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='failures'} | {'failure_count':len(summary['failures'])},ensure_ascii=False),flush=True)
    if summary['failures'] or boundary['forbidden_attempts']:raise SystemExit(1)


if __name__=='__main__':main()
