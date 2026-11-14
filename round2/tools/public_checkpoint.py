#!/usr/bin/env python3
"""Prepare reviewed public checkpoint bundles without touching the worktree index.

Publishing uses the connected GitHub tools; this tool holds no GitHub credential.
Only a fixed text allowlist is exported. Reports are public derivatives, never
raw patient records. A failed scan leaves the remote unchanged.
"""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PATTERNS=('src/**/*.py','tests/test_*.py','configs/round2*.yaml','configs/round2*.json',
          'configs/tool_access_cost_v1.json','configs/config.yaml','templates/**/*.j2',
          'docs/ROUND2*.md','docs/EXECUTION_CHAIN.md','reports/ROUND2*.md','reports/T1*.md',
          'tools/*shared_plan*.py','tools/*guarded*.py','tools/*knowledge*.py',
          'tools/*round2*.py','tools/*control*.py','tools/*information_access*.py',
          'tools/public_checkpoint.py','scripts/train_predictor.py')
EXACT=('AGENTS.md','pyproject.toml','requirements.txt','Dockerfile_Baseline','inference.py','reasoning_align.py')
SENSITIVE_ID=re.compile(r'PT-(?:pseudo_)?[A-Za-z0-9_-]+|study-case-\d+|BX_\d+|T[123]-\d+|`[a-f0-9]{12}`')
SECRET=re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{20,}|\bsk-[A-Za-z0-9_-]{20,}')

def scrub_report(text):
    lines=[]
    for line in text.splitlines():
        if SENSITIVE_ID.search(line) or re.search(r'"case_id"\s*:',line):
            if not lines or lines[-1]!='[公开版省略逐例记录；汇总与机制结论保留。]':
                lines.append('[公开版省略逐例记录；汇总与机制结论保留。]')
        else:lines.append(line)
    return '\n'.join(lines)+'\n'

def collect(root=ROOT):
    paths=set()
    for pattern in PATTERNS:paths.update(root.glob(pattern))
    paths.update(root/p for p in EXACT if (root/p).is_file())
    result={}; manifest={}
    for p in sorted(paths):
        rel=p.relative_to(root).as_posix()
        if not p.resolve().is_relative_to(root.resolve()):raise ValueError('Export escapes source root')
        if p.is_symlink() or any(x.startswith('._') or '.bak' in x or x in ('__pycache__','src_afix') for x in p.parts):continue
        raw=p.read_bytes()
        if len(raw)>400_000 or b'\0' in raw:raise ValueError('Not permitted text: '+rel)
        text=raw.decode('utf-8')
        if SECRET.search(text):raise ValueError('Credential pattern: '+rel)
        if rel.startswith(('docs/','reports/')):
            text=scrub_report(text)
        elif re.search(r'PT-(?:pseudo_)?[a-f0-9]{12}',text):
            # Do not silently edit executable source containing patient-specific
            # fixtures. Exclude and disclose rather than produce altered code.
            manifest[rel]={'excluded':'patient_identifier_pattern'};continue
        target='round2/'+rel
        result[target]=text
        manifest[rel]={'source_sha256':hashlib.sha256(raw).hexdigest(),
            'public_sha256':hashlib.sha256(text.encode()).hexdigest(),'public_path':target,
            'public_derivative':text.encode()!=raw}
    return result,manifest

def prepare(step,summary,root=ROOT):
    state=root/'output/public_sync';state.mkdir(parents=True,exist_ok=True)
    with (state/'prepare.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        stamp=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,70}',step):raise ValueError('Invalid step identifier')
        key=stamp+'-'+step; dest=state/key;dest.mkdir()
        files,manifest=collect(root)
        for p in sorted((root/'discussion').rglob('*.md')):
            if p.is_symlink():raise ValueError('Discussion symlink forbidden')
            text=p.read_text()
            if SECRET.search(text):raise ValueError('Credential in discussion')
            files[p.relative_to(root).as_posix()]=scrub_report(text)
        for rel,entry in manifest.items():
            if 'source_sha256' in entry and hashlib.sha256((root/rel).read_bytes()).hexdigest()!=entry['source_sha256']:
                raise RuntimeError('Source changed during snapshot: '+rel)
        files['round2/README.md']='# 第二轮工作快照\n\n本目录是当前第二轮代码、配置、测试和公开过程报告的阶段快照。仓库根目录保留上轮发布代码，不能混称同一版本。\n\n[Chat讨论入口](../discussion/README.md) · [当前状态](../discussion/CURRENT.md) · [过程报告](reports/) · [总计划](docs/ROUND2_MASTER_PLAN_20261113.md)\n\n过程报告为公开衍生版，逐例记录省略。原始证据、模型和数据留在服务器。代码必须结合相应冻结批次理解，工程检查不等于医学提分。\n'
        checkpoint='discussion/checkpoints/'+key
        record={'schema_version':1,'checkpoint':key,'created_beijing':stamp,
            'source_git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
            'summary':summary,'files':manifest,'note':'Public text derivative; raw outputs, clinical data, model weights and credentials are not included.'}
        files[checkpoint+'/SUMMARY.md']='# '+step+'\n\n'+summary+'\n\n代码和公开报告与本提交对应；逐文件指纹见 manifest.json。服务器原始证据不在公开仓库。\n'
        files[checkpoint+'/manifest.json']=json.dumps(record,ensure_ascii=False,indent=2)+'\n'
        files['discussion/LATEST.md']='# 最新存档\n\n'+key+'\n\n'+summary+'\n\n[存档摘要](checkpoints/'+key+'/SUMMARY.md) · [指纹清单](checkpoints/'+key+'/manifest.json)\n'
        # Separate bare repository/index; preserve staging and all local edits.
        bare=state/'archive.git'
        if not bare.exists():subprocess.run(['git','init','--bare',str(bare)],check=True,capture_output=True)
        env={**os.environ,'GIT_INDEX_FILE':str((dest/'index').resolve()),'GIT_AUTHOR_NAME':'CHIMERA checkpoint','GIT_AUTHOR_EMAIL':'checkpoint@local','GIT_COMMITTER_NAME':'CHIMERA checkpoint','GIT_COMMITTER_EMAIL':'checkpoint@local'}
        def git(*args,input=None):return subprocess.check_output(['git','--git-dir='+str(bare),*args],input=input,env=env).decode().strip()
        for path,text in sorted(files.items()):
            sha=git('hash-object','-w','--stdin',input=text.encode());git('update-index','--add','--cacheinfo','100644,'+sha+','+path)
        tree=git('write-tree')
        prior=subprocess.run(['git','--git-dir='+str(bare),'rev-parse','--verify','refs/heads/checkpoints'],capture_output=True,text=True)
        args=['commit-tree',tree]
        if prior.returncode==0:args+=['-p',prior.stdout.strip()]
        commit=git(*args,input=(step+': '+summary+'\n').encode());git('update-ref','refs/heads/checkpoints',commit);git('tag',key,commit)
        bundle={'repository':'lancheq/chimera-agent-cloud','branch':'main','checkpoint':key,
            'local_archive_commit':commit,'files':files,'summary':summary,'status':'prepared_not_uploaded'}
        (dest/'bundle.json').write_text(json.dumps(bundle,ensure_ascii=False))
        (state/'pending.json').write_text(json.dumps({'bundle_path':str(dest/'bundle.json'),'checkpoint':key,'status':'prepared_not_uploaded'},ensure_ascii=False,indent=2)+'\n')
        return {'bundle_path':str(dest/'bundle.json'),'checkpoint':key,'local_archive_commit':commit,'file_count':len(files),'excluded':[k for k,v in manifest.items() if 'excluded' in v]}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--step',required=True);p.add_argument('--summary',required=True)
    a=p.parse_args();print(json.dumps(prepare(a.step,a.summary),ensure_ascii=False,indent=2))
