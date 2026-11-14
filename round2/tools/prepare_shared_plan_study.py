"""Freeze S4/P7 for review. No generation, target decoding, or training."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from shutil import copy2,copytree
import statistics
import urllib.request
from omegaconf import OmegaConf
from chimera_agent_baseline.knowledge_access import sha,load_manifest
from chimera_agent_baseline.information_access import digest

ROOT=Path(__file__).resolve().parents[1]
SOURCE='phase2_train_release_20261104'


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def select_cases(pool,groups,expected=91):
    candidates=[r for r in pool['cases'] if r['source']==SOURCE and r['task']==1]
    selected=[r for r in candidates if r['decision_file_present'] and r.get('usage')=='internal_development_exposed']
    if len(selected)!=expected:raise ValueError('Predeclared labeled source count differs; do not silently change the cohort')
    membership={r['record_index']:r['risk_group_id'] for r in groups['mappings']}
    ids=[membership[r['record_index']] for r in selected]
    if len(set(ids))!=len(ids):raise ValueError('Duplicate conservative risk groups require an explicit grouped protocol')
    selected.sort(key=lambda r:hashlib.sha256(('20261113|'+membership[r['record_index']]).encode()).hexdigest())
    return selected,membership,len(candidates)-len(selected)


def serving_identity():
    endpoints=[]
    for port in (8765,8766):
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/props',timeout=15) as response:props=json.load(response)
        endpoints.append(dict(port=port,props=props))
    fields=('model_path','model_alias','model_ftype','chat_template','default_generation_settings','build_info')
    for field in fields:
        if endpoints[0]['props'].get(field)!=endpoints[1]['props'].get(field):
            raise ValueError('Serving endpoints differ on '+field)
    model=Path(endpoints[0]['props']['model_path'])
    if not model.is_file():raise ValueError('Actual serving model file not accessible')
    return dict(model_path=str(model),model_sha256=sha(model),model_bytes=model.stat().st_size,endpoints=endpoints,
                context_limit=endpoints[0]['props']['default_generation_settings']['n_ctx'])


def snapshot_code(destination):
    files=[]
    files.extend(p for p in (ROOT/'src').rglob('*.py') if not p.name.startswith('._'))
    for name in ('tools','tests'):files.extend(p for p in (ROOT/name).glob('*.py') if not p.name.startswith('._'))
    for name,extensions in [('configs',{'.yaml','.json'}),('templates',{'.j2'})]:
        files.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and p.suffix in extensions and not p.name.startswith('._'))
    files.extend([ROOT/'AGENTS.md',ROOT/'pyproject.toml'])
    files.extend(p for p in (ROOT/'resources').glob('*.json') if not p.name.startswith('._'))
    hashes={}
    for source in files:
        if not source.is_file():continue
        relative=source.relative_to(ROOT);target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);copy2(source,target)
        hashes[str(relative)]=sha(target)
    write(destination/'SOURCE_FILES_SHA256.json',hashes)
    return hashes


def historical_throughput():
    result={}
    for port,name in [(8765,'server_gpu0.log'),(8766,'server_gpu1.log')]:
        path=ROOT/'output/round2_night_20261110'/name
        lines=[line for line in path.read_text(errors='replace').splitlines() if 'eval time =' in line and 'prompt eval' not in line][-200:]
        speeds=sorted(float(m[1]) for line in lines if (m:=re.search(r'([\d.]+) tokens per second',line)))
        result[str(port)]=dict(source=str(path),last_n=len(speeds),p10=speeds[len(speeds)//10],median=statistics.median(speeds),p90=speeds[len(speeds)*9//10])
    return result


def prepare(output,pool_path,groups_path):
    if output.exists():raise ValueError('Freeze into a new directory; preserve earlier protocols')
    pool=json.loads(pool_path.read_text());groups=json.loads(groups_path.read_text())
    records,membership,unlabeled=select_cases(pool,groups)
    model=serving_identity()
    knowledge=ROOT/'output/s2_knowledge_snapshot_20261113_v4/manifest.json'
    load_manifest(knowledge,require_alignment=True)
    pin=json.loads((ROOT/'configs/round2_evaluator.json').read_text())
    evaluator=ROOT/'output/round2_20261110/official_evaluator/evaluate.py'
    mapping=evaluator.parent/'section_variable_mapping.json'
    if sha(evaluator)!=pin['evaluate_sha256'] or sha(mapping)!=pin['mapping_sha256']:raise ValueError('Official evaluator pin differs')
    assets={r['path']:r for r in pool['assets']}
    runtime=[[],[]];evaluation=[]
    for index,record in enumerate(records):
        source=Path(record['directory'])
        upfront=next(p for p in (source/'prompt.json',source/'structured-prompt.json') if p.is_file())
        clinical=source/'prostate-biopsy-decision-clinical-data.json'
        for path in (upfront,clinical):
            if sha(path)!=assets[str(path)]['sha256']:raise ValueError('Registered input bytes differ')
        worker=index%2
        order=['fixed','adaptive'] if (index//2)%2==0 else ['adaptive','fixed']
        row=dict(task=1,case_id=record['case_id'],directory=str(source),upfront_filename=upfront.name,
                 upfront_sha256=sha(upfront),clinical_sha256=sha(clinical),arm_order=order)
        runtime[worker].append(row)
        references={}
        for kind,key in [('decision','decision_asset_index'),('reasoning','reasoning_asset_index')]:
            asset=pool['assets'][record[key]];path=Path(asset['path']);raw_sha=sha(path)
            declared=asset.get('declared_transfer_sha256')
            if declared and raw_sha!=declared:raise ValueError('Reference bytes differ from transfer provenance')
            references[kind]=dict(path=str(path),sha256=raw_sha,content_decoded=False)
        evaluation.append(dict(case_id=record['case_id'],task=1,source=SOURCE,record_index=record['record_index'],
            risk_group_id=membership[record['record_index']],worker=worker,arm_order=order,references=references,
            role='legacy_exposed_internal_paired_evaluation',patient_identity_confirmed=False))
    output.mkdir(parents=True)
    snapshot=output/'source_snapshot';hashes=snapshot_code(snapshot)
    copies=output/'official_evaluator';copies.mkdir();copy2(evaluator,copies/evaluator.name);copy2(mapping,copies/mapping.name)
    base=OmegaConf.merge(OmegaConf.load(ROOT/'configs/config.yaml'),OmegaConf.load(ROOT/'configs/round2_shared_plan_candidate.yaml'))
    base.agent.information_access.run_id='s4-shared-plan-internal-t1-20261113-v1'
    result_root=ROOT/'output/s4_shared_plan_performance_20261113_v1'
    commands=[];dry_commands=[]
    for worker,rows in enumerate(runtime):
        cfg=OmegaConf.create(OmegaConf.to_container(base,resolve=True));cfg.model.base_url=f'http://127.0.0.1:{8765+worker}/v1'
        cfg.paths.output_dir=str(result_root/f'worker{worker}')
        OmegaConf.save(cfg,output/f'worker{worker}.yaml')
        cohort=output/f'runtime_worker{worker}.json'
        write(cohort,dict(schema_version=1,study_id='s4-shared-plan-internal-t1-v1',cases=rows))
        common=[str(snapshot/'tools/run_shared_plan_candidate.py'),'--config',str(output/f'worker{worker}.yaml'),'--cohort',str(cohort)]
        commands.append(['/home/hedongze/venvs/chimera_access/bin/python',*common,'--output',str(result_root/f'worker{worker}'),'--execute'])
        dry_commands.append(['/home/hedongze/venvs/chimera_access/bin/python',*common,'--output',str(output/f'dry_run_worker{worker}')])
    protocol=dict(schema_version=1,study_id='s4-shared-plan-internal-t1-v1',stage='S4_P7_prepared_not_executed',
        created_beijing=datetime.now().astimezone().isoformat(),unique_primary_change='Permission to revise acquisition and stopping after the common first observation',
        source=SOURCE,cohort_role='legacy_exposed_internal_not_independent_holdout',n_case_records=len(records),n_conservative_risk_groups=len(records),
        n_arm_instances=len(records)*2,unlabeled_t1_records_not_scored=unlabeled,patient_identity_confirmed=False,
        selection_rule='All 91 Phase2 T1 records with registered decision file presence; sorted/assigned by frozen conservative group hash; no outcome values or error-based selection',
        input_quality_policy='Keep original contradictory/unknown inputs identically in both arms; no GT-selected removal or correction',
        model=model,knowledge_manifest=dict(path=str(knowledge),sha256=sha(knowledge)),
        evaluator=dict(path=str(copies/'evaluate.py'),sha256=pin['evaluate_sha256'],mapping=str(copies/'section_variable_mapping.json'),mapping_sha256=pin['mapping_sha256']),
        source_pool_sha256=sha(pool_path),risk_mapping_sha256=sha(groups_path),source_snapshot=str(snapshot),source_files=hashes,
        per_arm=dict(information_units=3,max_admitted_attempts=6,max_action_requests=6,adaptive_policy_turns=5,control_format_retries=2),
        generation=dict(seed=20261113,model_weights_frozen=True,component_trained=None,transport_retries=0),
        stage_definition=dict(primary='parsed structured judgment from the common LLM formatter',verification='schema and information permission verification only; clinical fact_check/self_refine excluded',postprocessing='disabled in both arms'),
        metrics=dict(primary='Paired adaptive minus fixed correct fraction over all 91 frozen cases; terminal failures count incorrect',
            secondary=['new clinical-section proxy units','official cost_aware_tool_score from frozen evaluator','tool attempts/cache/denial rates','model calls/input-output tokens/latency by stage','case/arm failures'],
            separation='Official tool precision, information proxy units and compute cost reported separately; no currency/real patient testing price asserted',
            statistics=dict(paired_bootstrap_draws=10000,bootstrap_seed=20261113,confidence=0.95,unit='conservative risk group',mcnemar='exact two-sided discordant-pair test',
                assumption='Unconfirmed patient linkage limits independence; intervals do not certify new-patient generalization')),
        failure_policy=dict(permission_or_binding_drift='immediate halt',consecutive_failed_pairs=3,accuracy_early_stopping=False,
            pending_on_halt='not scored as terminal failure; final analysis requires completion or explicit registered disposition',
            ambiguous_tool_outcome='preserve ledger, refuse automatic replay, reconcile before further generation',
            recovery='same source snapshot, evidence, parameters, seed and ledger; successful arms skipped and hashes verified; do not silently increase budgets'),
        decision_rule=dict(advance='Evidence of a positive primary accuracy difference with a 95% interval excluding zero and no unresolved permission/runtime breach; report cost tradeoff independently',
            cost_only='Lower information cost with non-positive/inconclusive accuracy is exploratory tradeoff evidence, not proven accuracy improvement or noninferiority',
            stop='Negative or inconclusive primary result still closes this finite candidate; analyze before any S5 or new prompt/model variant',
            clinical_generalization='No independent holdout available; positive internal evidence remains limited to this exposed cohort'),
        evaluation_cases=evaluation,worker_counts=[len(x) for x in runtime],results_root=str(result_root),
        execution_commands=commands,dry_run_commands=dry_commands,
        environment=dict(cwd=str(snapshot),PYTHONPATH=str(snapshot/'src')+':'+str(snapshot)+':'+str(snapshot/'tests'),OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='4',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',CHIMERA_RERANKER='on'),
        scheduling=dict(finite_case_pairs=91,model_request_worst_case=91*26,generated_token_worst_case=91*69632,
            historical_decode_tokens_per_second=historical_throughput(),engineering_overhead_reference=str(ROOT/'output/s3_shared_plan_acceptance_20261113_v2/summary.json'),
            expected_duration='Approximately 3–6 hours with two existing equivalent endpoints; saturated retries/retrieval may exceed 10 hours',
            estimate_limit='No new clinical LLM throughput was measured; use historical decoder and actual scripted/MCP overhead, refresh ETA at authorized deployment; no hard cutoff'),
        permission='Prepared for review only. No clinical performance generation authorized by this protocol file. --execute is not run by prepare.',
        target_access=dict(reference_bytes_hashed=True,reference_contents_decoded=False,agent_runtime_metadata_has_no_target_or_quality_fields=True),
        actual_diagnostic_llm_calls=0)
    write(output/'protocol.json',protocol)
    write(output/'protocol_digest.json',dict(sha256=sha(output/'protocol.json'),runtime_cohorts={str(i):sha(output/f'runtime_worker{i}.json') for i in (0,1)},config_sha256={str(i):sha(output/f'worker{i}.yaml') for i in (0,1)}))
    return dict(status='prepared_not_executed',case_pairs=len(records),worker_counts=protocol['worker_counts'],actual_diagnostic_llm_calls=0,protocol=str(output/'protocol.json'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool',type=Path,default=ROOT/'output/round2_data_pool_20261113_v2/data_pool_manifest.json')
    p.add_argument('--groups',type=Path,default=ROOT/'output/round2_data_pool_20261113_v2/patient_version_map.json')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.output.resolve(),a.pool,a.groups),ensure_ascii=False),flush=True)
