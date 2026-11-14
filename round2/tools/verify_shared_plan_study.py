"""Verify a frozen study before any authorized generation; no generation API."""
import argparse
import json
from pathlib import Path
import urllib.request
from omegaconf import OmegaConf
from chimera_agent_baseline.knowledge_access import sha,load_manifest


def verify(path,check_backends=True):
    protocol=json.loads(path.read_text());root=path.parent;pin=json.loads((root/'protocol_digest.json').read_text())
    if sha(path)!=pin['sha256']:raise ValueError('Protocol differs')
    snapshot=Path(protocol['source_snapshot'])
    for name,value in protocol['source_files'].items():
        if sha(snapshot/name)!=value:raise ValueError('Frozen source differs: '+name)
    knowledge=protocol['knowledge_manifest']
    load_manifest(knowledge['path'],knowledge['sha256'],require_alignment=True)
    model=protocol['model']
    if sha(model['model_path'])!=model['model_sha256']:raise ValueError('Model weights differ')
    if sha(protocol['evaluator']['path'])!=protocol['evaluator']['sha256']:raise ValueError('Evaluator differs')
    if sha(protocol['evaluator']['mapping'])!=protocol['evaluator']['mapping_sha256']:raise ValueError('Evaluator mapping differs')
    n=0;members=set()
    for worker in (0,1):
        config=root/f'worker{worker}.yaml';runtime=root/f'runtime_worker{worker}.json'
        if sha(config)!=pin['config_sha256'][str(worker)] or sha(runtime)!=pin['runtime_cohorts'][str(worker)]:
            raise ValueError('Worker config or runtime manifest differs')
        from tools.run_shared_plan_candidate import prepare
        _,cases=prepare(OmegaConf.load(config),runtime)
        for case in cases:
            key=(case.task,case.case_id)
            if key in members:raise ValueError('A case appears in both worker shards')
            members.add(key);n+=1
    if n!=protocol['n_case_records']:raise ValueError('Frozen denominator differs')
    for case in protocol['evaluation_cases']:
        for reference in case['references'].values():
            if sha(reference['path'])!=reference['sha256']:raise ValueError('Reference bytes differ; do not substitute labels')
    if check_backends:
        for backend in model['endpoints']:
            with urllib.request.urlopen(f"http://127.0.0.1:{backend['port']}/props",timeout=15) as response:props=json.load(response)
            for key in ('model_path','model_alias','model_ftype','chat_template','default_generation_settings','build_info'):
                if props.get(key)!=backend['props'].get(key):raise ValueError('Serving backend drift: '+key)
    return dict(status='verified_for_review_not_executed',cases=n,arm_instances=n*2,source_files=len(protocol['source_files']),actual_diagnostic_llm_calls=0)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--protocol',type=Path,required=True)
    p.add_argument('--offline',action='store_true',help='Skip only live endpoint comparison; weights/resources still checked')
    a=p.parse_args();print(json.dumps(verify(a.protocol,not a.offline),ensure_ascii=False),flush=True)
