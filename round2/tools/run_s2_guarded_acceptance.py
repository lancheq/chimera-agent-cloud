"""Finite S2 repair/acceptance chain. Never starts an LLM performance batch."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--rebuild-pid',type=int,required=True)
p.add_argument('--rebuild',type=Path,required=True)
p.add_argument('--parent-manifest',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--manifest-output',type=Path,required=True)
a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=True)
status_path=a.output/'batch_status.json'
started=datetime.now().astimezone().isoformat()
proc_path=Path(f'/proc/{a.rebuild_pid}/stat')
identity=proc_path.read_text().split()[21] if proc_path.exists() else None
status=dict(stage='S2_P5_engineering_only',status='waiting_for_vector_rebuild',started_beijing=started,
    pid=os.getpid(),rebuild_pid=a.rebuild_pid,actual_diagnostic_llm_calls=0,steps=[],
    source_root=str(Path.cwd()),note='Finite prerequisites only; stop on first failure. No clock deadline and no S3/clinical batch launch.')

def save():
    status['updated_at']=datetime.now().astimezone().isoformat()
    temp=status_path.with_suffix('.tmp')
    temp.write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n');temp.replace(status_path)

def step(name,args):
    status['status']='running';status['current_step']=name;save()
    now=time.monotonic()
    with (a.output/(name+'.log')).open('w') as log:
        result=subprocess.run([sys.executable,*args],stdout=log,stderr=subprocess.STDOUT,check=False)
    status['steps'].append(dict(name=name,exit_code=result.returncode,elapsed_seconds=time.monotonic()-now))
    save()
    if result.returncode:
        raise RuntimeError(name+' failed; subsequent steps not started')

save()
try:
    while proc_path.exists() and proc_path.read_text().split()[21]==identity:
        # A zombie has closed its DB and will never make progress.
        if proc_path.read_text().split()[2]=='Z':break
        time.sleep(5)
    if not (a.rebuild/'rebuild_summary.json').is_file():
        raise RuntimeError('Vector rebuild exited without a completion record')
    step('01_full_vector_write_check',['tools/check_rebuilt_knowledge_vectors.py','--parent-manifest',str(a.parent_manifest),'--rebuild',str(a.rebuild)])
    step('02_freeze_resources',['tools/freeze_knowledge_access.py','--resources',str(a.rebuild/'resources'),'--output',str(a.manifest_output),
        '--encoder','/home/hedongze/CHIMERA-agent/resources/embedding_model','--reranker','/home/hedongze/CHIMERA-agent/model/reranker_model'])
    manifest=a.manifest_output/'manifest.json'
    step('03_vector_compatibility_authorization',['tools/validate_knowledge_geometry.py','--manifest',str(manifest),
        '--output',str(a.manifest_output/'vector_compatibility.json'),'--authorize'])
    step('04_actual_knowledge_runner',['tools/validate_guarded_knowledge.py','--manifest',str(manifest),'--output',str(a.output/'knowledge_runner')])
    step('05_all_task_clinical_runner',['tools/validate_guarded_runner.py','--output',str(a.output/'clinical_runner')])
    step('06_regression',['-m','pytest','-q','tests/test_guarded_runner.py','tests/test_information_access.py','tests/test_refactor_runtime.py',
        'tests/test_round2_policy.py','tests/test_run_completion.py','tests/test_mcp_environment.py','tests/test_knowledge_access.py'])
    step('07_official_input_adapter',['tools/preflight_guarded_data.py','--pool',
        str(a.parent_manifest.parents[2]/'output/round2_data_pool_20261113_v2/data_pool_manifest.json'),
        '--output',str(a.output/'official_input_adapter')])
    status['status']='completed';status['current_step']=None
except BaseException as exc:
    status['status']='failed';status['error']=type(exc).__name__+': '+str(exc)
    save()
    raise
else:
    status['completed_at']=datetime.now().astimezone().isoformat();save()
    print(json.dumps(status,ensure_ascii=False),flush=True)
