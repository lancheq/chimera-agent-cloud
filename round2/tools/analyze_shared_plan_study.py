"""Private S4 analysis after terminal collection. Never calls a model.

References are opened only here; the agent runtime accepts a different schema.
"""
import argparse
from collections import Counter
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import sys

from chimera_agent_baseline.information_access import digest
from chimera_agent_baseline.knowledge_access import sha
from chimera_agent_baseline.shared_plan import Journal


def exact_mcnemar(wins,losses):
    n=wins+losses
    return 1.0 if n==0 else min(1.0,2*sum(math.comb(n,i) for i in range(min(wins,losses)+1))/(2**n))


def paired_interval(differences,groups,draws=10000,seed=20261113):
    if not differences:return None
    grouped={}
    for value,group in zip(differences,groups):grouped.setdefault(group,[]).append(value)
    clusters=list(grouped.values());rng=random.Random(seed);samples=[]
    for _ in range(draws):
        sample=[v for _ in clusters for v in rng.choice(clusters)]
        samples.append(sum(sample)/len(sample))
    samples.sort()
    return [samples[int(.025*(draws-1))],samples[int(.975*(draws-1))]]


def official_evaluator(protocol):
    info=protocol['evaluator']
    if sha(info['path'])!=info['sha256'] or sha(info['mapping'])!=info['mapping_sha256']:
        raise ValueError('Frozen official evaluator differs')
    os.environ['USE_RATIONALE_JUDGE']='0'
    os.environ['SECTION_MAPPING_FILE']=info['mapping']
    spec=importlib.util.spec_from_file_location('shared_plan_official_evaluator',info['path'])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module


def validate_receipt(case,case_id,expected=None):
    receipt=json.loads((case/'information_access_receipt.json').read_text())
    if receipt['binding']['case_id']!=case_id or receipt['binding']['task']!=1:raise ValueError('Wrong case output binding')
    if expected:
        binding=receipt['binding']
        if binding['snapshot_id']!=expected['clinical_sha256'] or binding['knowledge_snapshot_id']!=expected['knowledge_sha256']:
            raise ValueError('Output uses a different input/resource version')
        if not binding['session_id'].startswith(expected['run_id']+':') or binding['budget_units']!=3 or binding['max_attempts']!=6:
            raise ValueError('Output uses a different run/budget')
        plan=json.loads((case/'initial_plan.json').read_text())
        if plan['binding']['model']!=expected['model'] or plan['binding']['generation']!=expected['generation'] or plan['binding']['options']!=expected['options']:
            raise ValueError('Output model/generation options differ from protocol')
        for name,value in plan['binding']['code'].items():
            if expected['source_files'].get('src/chimera_agent_baseline/'+name)!=value:
                raise ValueError('Output runtime source differs from protocol')
    for name,key in [('prediction.json','prediction_sha256'),('trace.json','trace_sha256')]:
        if sha(case/name)!=receipt[key]:raise ValueError('Output receipt differs')
    for name,value in receipt.get('candidate_artifacts',{}).items():
        if sha(case/name)!=value:raise ValueError('Candidate artifact differs')
    snapshot=json.loads((case/'information_access_snapshot.json').read_text())
    if digest(snapshot)!=receipt['ledger_snapshot_digest']:raise ValueError('Saved ledger snapshot differs')
    db=sqlite3.connect((case/'information_access.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    try:
        binding,spent,attempts=db.execute('SELECT binding,spent,attempts FROM meta').fetchone()
        if json.loads(binding)!=receipt['binding'] or spent!=snapshot['spent_units'] or attempts!=snapshot['admitted_attempts']:
            raise ValueError('Actual ledger differs from receipt')
        if db.execute('SELECT COUNT(*) FROM pending').fetchone()[0]:raise ValueError('Unsettled information requests')
        events=[json.loads(x[0]) for x in db.execute('SELECT event FROM events ORDER BY seq')]
        if events!=snapshot['events']:raise ValueError('Ledger events differ from receipt')
    finally:db.close()
    Journal(case/'candidate_events.jsonl')
    return snapshot


def compute_cost(events):
    requests=[x for x in events if x['kind']=='model_request']
    returns=[x for x in events if x['kind']=='model_return']
    usage=[x['usage'] for x in returns if isinstance(x.get('usage'),dict)]
    return dict(client_call_attempts=len(requests),successful_responses=len(returns),usage_known_responses=len(usage),
        input_tokens=sum(u['input_tokens'] for u in usage) if len(usage)==len(returns)==len(requests) else None,
        output_tokens=sum(u['output_tokens'] for u in usage) if len(usage)==len(returns)==len(requests) else None,
        response_elapsed_seconds=sum(x['elapsed_seconds'] for x in returns),
        by_stage=dict(Counter(x['stage'] for x in requests)))


def analyze(protocol_path,results_root,output):
    if output.exists():raise ValueError('Analysis output must be new')
    protocol=json.loads(protocol_path.read_text());pin=json.loads((protocol_path.parent/'protocol_digest.json').read_text())
    if sha(protocol_path)!=pin['sha256']:raise ValueError('Protocol differs from preregistration digest')
    configs={};runtime_cases={}
    from omegaconf import OmegaConf
    for worker in (0,1):
        config_path=protocol_path.parent/f'worker{worker}.yaml'
        runtime_path=protocol_path.parent/f'runtime_worker{worker}.json'
        if sha(config_path)!=pin['config_sha256'][str(worker)] or sha(runtime_path)!=pin['runtime_cohorts'][str(worker)]:
            raise ValueError('Frozen config/runtime cohort differs')
        configs[worker]=OmegaConf.to_container(OmegaConf.load(config_path),resolve=True)
        runtime_cases[worker]={r['case_id']:r for r in json.loads(runtime_path.read_text())['cases']}
        status=json.loads((results_root/f'worker{worker}/candidate_batch_status.json').read_text())
        if status['status'] not in ('completed','incomplete') or status.get('pending_cases'):
            raise ValueError('Collection not terminal for all cases; pending cases are not terminal failures')
        if status['arms_attempted']!=status['expected_arms']:raise ValueError('Unattempted frozen cases remain')
    evaluator=official_evaluator(protocol)
    rows=[];common_compute=[]
    for reference in protocol['evaluation_cases']:
        cid=reference['case_id'];worker=results_root/f"worker{reference['worker']}"
        decision=reference['references']['decision'];reasoning=reference['references']['reasoning']
        for ref in (decision,reasoning):
            if sha(ref['path'])!=ref['sha256']:raise ValueError('Reference bytes changed after freeze')
        label=json.loads(Path(decision['path']).read_text())
        if not isinstance(label,str) or label not in ('yes','no'):raise ValueError('Unexpected T1 target format; do not coerce or default')
        gt_reasoning=json.loads(Path(reasoning['path']).read_text())
        row=dict(case_id=cid,risk_group_id=reference['risk_group_id'],label=label)
        for arm in ('fixed','adaptive'):
            case=worker/arm/'task1'/cid
            result=dict(complete=False,parsed_correct=0,verified_correct=0,final_correct=0,information_units=None,
                official_tool_score=None,compute=None,error=None)
            if (case/'.failed').exists() or not (case/'prediction.json').exists():
                result['error']=(case/'.failed').read_text() if (case/'.failed').exists() else 'missing candidate output'
                if (case/'information_access_snapshot.json').exists():
                    result['information_units']=json.loads((case/'information_access_snapshot.json').read_text())['spent_units']
            else:
                cfg=configs[reference['worker']];runtime=runtime_cases[reference['worker']][cid]
                expected=dict(clinical_sha256=runtime['clinical_sha256'],knowledge_sha256=protocol['knowledge_manifest']['sha256'],
                    run_id=cfg['agent']['information_access']['run_id'],model={k:cfg['model'].get(k) for k in ('provider','model_id','base_url','extra_body','max_retries')},
                    generation=cfg['generation'],options={k:v for k,v in cfg['agent']['shared_plan'].items() if k!='arm'},source_files=protocol['source_files'])
                snapshot=validate_receipt(case,cid,expected)
                pred=json.loads((case/'prediction.json').read_text());trace=json.loads((case/'trace.json').read_text())
                stages={x['stage']:x for x in trace['processing_stages']}
                parsed=stages['parsed_judgment']['after']['biopsy_decision']
                verified=stages['verified_judgment']['after']['biopsy_decision']
                if stages['postprocessing']['status']!='disabled':raise ValueError('Unexpected downstream treatment in the controlled study')
                score,_=evaluator.cost_aware_tool_score(gt_reasoning,pred)
                result.update(complete=True,parsed_correct=int(parsed==label),verified_correct=int(verified==label),
                    final_correct=int(pred['biopsy_decision']==label),information_units=snapshot['spent_units'],
                    official_tool_score=score,compute=compute_cost(Journal(case/'candidate_events.jsonl').events),
                    admitted_attempts=snapshot['admitted_attempts'])
            row[arm]=result
        rows.append(row)
        planner=worker/'common_plans/task1'/cid/'model_calls.jsonl'
        if planner.exists():common_compute.append(dict(case_id=cid,**compute_cost(Journal(planner).events)))
    n=len(rows)
    if n!=protocol['n_case_records']:raise ValueError('Frozen analysis denominator differs')
    delta=[r['adaptive']['parsed_correct']-r['fixed']['parsed_correct'] for r in rows]
    groups=[r['risk_group_id'] for r in rows]
    wins=sum(x==1 for x in delta);losses=sum(x==-1 for x in delta)
    ci=paired_interval(delta,groups,draws=protocol['metrics']['statistics']['paired_bootstrap_draws'],seed=protocol['metrics']['statistics']['bootstrap_seed'])
    joint=[r for r in rows if r['fixed']['complete'] and r['adaptive']['complete']]
    cost_delta=[r['adaptive']['information_units']-r['fixed']['information_units'] for r in joint]
    accuracy={arm:sum(r[arm]['parsed_correct'] for r in rows)/n for arm in ('fixed','adaptive')}
    summary=dict(stage='S4_P7_terminal_analysis',cohort_role=protocol['cohort_role'],denominator=n,
        primary=dict(stage='common_formatter_parsed_judgment',accuracy=accuracy,adaptive_minus_fixed=sum(delta)/n,
            paired_95_ci=ci,paired_wins=wins,paired_losses=losses,mcnemar_exact_two_sided=exact_mcnemar(wins,losses)),
        completed_arms={a:sum(r[a]['complete'] for r in rows) for a in ('fixed','adaptive')},
        secondary=dict(jointly_completed_pairs=len(joint),conditional_information_unit_difference=sum(cost_delta)/len(cost_delta) if cost_delta else None,
            official_tool_score={a:sum(r[a]['official_tool_score'] for r in joint)/len(joint) if joint else None for a in ('fixed','adaptive')},
            note='Cost conditional on joint completion; failures do not count as zero-cost efficient diagnoses'),
        shared_planning_compute=common_compute,rows=rows,analysis_model_calls=0,
        decision='positive_internal_accuracy_evidence' if ci[0]>0 and len(joint)==n else 'negative_or_inconclusive_requires_analysis_before_next_mechanism',
        limits=['Historical exposed records, not an independent patient holdout','Risk groups do not certify patient independence','No monetary testing costs or full GEval/platform score asserted'])
    output.mkdir(parents=True);(output/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return {k:v for k,v in summary.items() if k not in ('rows','shared_planning_compute')}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--protocol',type=Path,required=True);p.add_argument('--results-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(analyze(a.protocol,a.results_root,a.output),ensure_ascii=False),flush=True)
