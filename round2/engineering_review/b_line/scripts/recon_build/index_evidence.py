"""Index the finished synthetic run without reading clinical experiments."""
from collections import Counter
from datetime import datetime
from difflib import unified_diff
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from chimera_agent_baseline.information_access import digest
from chimera_agent_baseline.runtime_audit import content_digest

ROOT = Path(__file__).resolve().parent.relative_to(Path.cwd())
REPORT = Path('reports/ROUND2_B_EVIDENCE_RECONCILIATION_BUILD_20261118.md')
CARD = 'discussion/tasks/20261118_B_EVIDENCE_RECONCILIATION_BUILD.md'
MARKER = '[Evidence timing and unresolved comparisons; references only]'
FINAL = ROOT / 'final_cases'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def write(name, payload):
    (ROOT / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args])


def check_chain(path):
    events = []
    previous = None
    for i, raw in enumerate(path.read_text().splitlines()):
        event = json.loads(raw)
        assert event['seq'] == i and event['previous'] == previous
        assert content_digest({k: v for k, v in event.items() if k != 'digest'}) == event['digest']
        previous = event['digest']
        events.append(event)
    return events


def run_record(path):
    summary = read(path)
    entry = {'fixture': str(path), 'config': str(path.with_name('config.yaml')),
             **{k: v for k, v in summary.items() if k != 'consumer_read'}}
    proof_files = [path, path.with_name('config.yaml')]
    if not summary.get('ledger'):
        return entry, proof_files, []
    ledger = Path(summary['ledger']).relative_to(Path.cwd())
    entry['ledger'] = str(ledger)
    proof_files.append(ledger)
    events = check_chain(ledger)
    attempts = [e for e in events if e['kind'] == 'wire_attempt']
    verified = {e['attempt']: e for e in events if e['kind'] == 'http_request_verified'}
    assert len(verified) == len(attempts)
    prepared = [e for e in events if e['stage'] == 'b_evidence_agent_input']
    delivery = []
    for e in prepared:
        target = [a for a in attempts if a['seq'] > e['seq'] and any(
            m.get('role') == 'system' and isinstance(m.get('content'), str)
            and m['content'].startswith(MARKER) and digest(m['content']) == e.get('block_digest')
            for m in a['payload']['messages'])]
        assert len(target) < 2
        linked = target[0] if target else None
        delivery.append(dict(stage_event=e['digest'], status=e['status'],
            wire_attempt=linked['digest'] if linked else None,
            serialized_request=verified[linked['attempt']]['digest'] if linked else None,
            body_sha256=verified[linked['attempt']]['body_sha256'] if linked else None,
            mock_consumer_read=bool(linked) and any(e['after'] in x for x in summary.get('consumer_read', []))))
    entry['entry_to_agent'] = delivery
    entry['model_requests'] = len(attempts)
    entry['exit'] = events[-1]['kind']
    entry['decision_boundaries'] = {event['stage']:event['digest'] for event in events
        if event['kind']=='consumption' and event['stage'] in ('formfill_input','assembled_output',
                                                              'prediction_file','reasoning_align','final_sockets')}
    snapshot = next(ledger.parent.rglob('information_access_snapshot.json'), None)
    if snapshot is not None:
        proof_files.append(snapshot)
        entry['tool_snapshot'] = str(snapshot)
        snap = read(snapshot)
        assert (snap['spent_units'],snap['admitted_attempts'])==(summary['information_units'],summary['admitted_tool_attempts'])
    if summary.get('files'):
        for name, details in summary['files'].items():
            socket = path.parent/'sockets'/details['file']
            assert sha(socket)==details['sha256']
            assert details['content_digest']==content_digest(read(socket))
            proof_files.append(socket)
        prediction=next(ledger.parent.rglob('prediction.json'))
        proof_files.append(prediction)
        entry['prediction'] = str(prediction)
    if summary['mode']=='stdio_blocked':
        db = next(ledger.parent.rglob('information_access.sqlite'))
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True) as conn:
            entry['information_units'],entry['admitted_tool_attempts']=conn.execute('SELECT spent,attempts FROM meta').fetchone()
        assert entry['information_units']==entry['admitted_tool_attempts']==1
        proof_files.append(db)
    return entry, proof_files, [e['payload'] for e in attempts]


baseline = read(ROOT/'baseline.json')
assert all(sha(p)==d for p,d in baseline['inputs'].items())
assert git('rev-parse','HEAD').decode().strip()==baseline['head']
assert git('diff','--cached','--binary')==(ROOT/'before/staged.patch').read_bytes()
assert not git('diff','--check')
status=git('status','--porcelain=v1','--untracked-files=all').decode()
(ROOT/'after_status.txt').write_text(status)
new_source=['src/chimera_agent_baseline/agent/evidence_reconciliation.py','tests/test_b_evidence_reconciliation_build.py']
status_changes=set(status.splitlines()) ^ set((ROOT/'before/status.txt').read_text().splitlines())
assert status_changes <= {'?? '+p for p in [*new_source,str(REPORT)]},status_changes

sources=[]
patch=[]
for name in [*baseline['before'],*new_source]:
    before=ROOT/'before'/name
    old=before.read_text().splitlines(keepends=True) if before.exists() else []
    new=Path(name).read_text().splitlines(keepends=True)
    if old:
        assert sha(before)==baseline['before'][name]
    patch.extend(unified_diff(old,new,fromfile='a/'+name if old else '/dev/null',tofile='b/'+name))
    sources.append(dict(path=name,before=str(before) if old else None,
                        before_sha256=sha(before) if old else None,after_sha256=sha(name)))
(ROOT/'source_diff.patch').write_text(''.join(patch))

manifests=[('output/p01_g1a_common_runtime_20261117/manifest/artifacts.json',None),
           ('output/p01_g1r_reflection_20261118/manifest.json','artifacts'),
           ('output/p01_g1t_sdk_request_boundary_20261118/manifest.json','artifact_sha256')]
preserved={}
for path, key in manifests:
    items=read(path)
    rows=items[key] if key else {str(Path(path).parents[1]/p):v['sha256'] for p,v in items.items()}
    assert all(sha(p)==d for p,d in rows.items()),path
    preserved[path]=len(rows)
c02='output/evidence_package_candidate_20261117/frozen_manifest.json'
preserved[c02]={'sha256':sha(c02),'scope':'recorded identity, C02 code and config checked separately'}

cases={}
files=[]
payloads={}
for p in sorted(FINAL.glob('**/fixture_summary.json')):
    if p.parent.is_symlink():
        continue
    e, raw, wire=run_record(p)
    key=(e['task'],e['mode'])
    assert key not in cases,key
    cases[key]=e
    files.extend(raw)
    payloads[key]=wire
assert len(cases)==12,cases.keys()
comparisons=[]
for task in (1,2,3):
    base=cases[task,'off']
    b=cases[task,'on']
    for field in ('model_requests','information_units','admitted_tool_attempts'):
        assert base[field]==b[field]
    deltas=[y-x for x,y in zip(base['synthetic_context_sizes'],b['synthetic_context_sizes'],strict=True)]
    assert deltas==([465,946,0] if task!=3 else [0,0]),deltas
    comparisons.append(dict(task=task,baseline={k:base[k] for k in ('model_requests','information_units',
        'admitted_tool_attempts','synthetic_context_sizes')},candidate_context_bytes=b['synthetic_context_sizes'],
        delta_bytes=deltas,byte_counter_is_fixture=True))
for mode in ('unknown','fallback'):
    assert payloads[1,mode]==payloads[1,'off'],mode
    assert cases[1,mode]['files']==cases[1,'off']['files']

tests=Counter(t.attrib['classname'] for t in ElementTree.parse(ROOT/'final.junit.xml').iter('testcase') if len(t)==0)
assert tests['tests.test_b_evidence_reconciliation_build']==37,tests
resources=(ROOT/'final.resources.txt').read_text()
assert 'Exit status: 0' in resources
write('entry_evidence.json',dict(scope='synthetic only',entries=list(cases.values()),on_off=comparisons))
now=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
resource_ledger=dict(pytest_workers=1,gpu_jobs=0,real_generations=0,clinical_tool_calls=0,
    new_clinical_inputs=0,model_service_operations=0,accepted_synthetic_sdk_calls=sum(s['model_requests'] for s in cases.values()),
    authorized_synthetic_tool_attempts=sum(s.get('admitted_tool_attempts',0) for s in cases.values()),
    real_tokenizer_calls=0,wall_and_rss=str(ROOT/'final.resources.txt'),background_jobs=0)
artifacts=[REPORT,*(ROOT/n for n in ('baseline.json','before/status.txt','before/staged.patch','after_status.txt',
    'source_diff.patch','entry_evidence.json','final.log','final.junit.xml','final.resources.txt','stdio_probe.log',
    'stdio_retry.log','transport.log','index_evidence.py')),*files]
artifacts.extend(Path(s['before']) for s in sources if s['before'])
handoff=dict(task_id='B-EVIDENCE-RECONCILIATION-BUILD',status='DELIVERED_AWAIT_PLANNING_REVIEW',
    task_card=CARD,task_card_sha256=baseline['inputs'][CARD],execution_model='gpt-6.2-sol/high',
    started_beijing=baseline['started_beijing'],finished_beijing=now,
    synthetic_status=dict(upfront_actual_entry='PASS',tool_text_transport_fixture='PASS',
        real_stdio_tool_followup='BLOCKED_G1T_TEXT_BLOCK_LIST',task3='EXPLICIT_SKIP',
        production_default='UNCHANGED',real_clinical_coverage='UNKNOWN',real_clinical_consumer='NOT_RUN'),
    configs=[c['config'] for c in cases.values()],diff=str(ROOT/'source_diff.patch'),source_changes=sources,
    preservation=dict(earlier_artifacts=preserved,unchanged_inputs=baseline['inputs'],git_head=baseline['head'],
                      staged_unchanged=True),
    resources=resource_ledger,
    tests=dict(exit_code=0,passed=sum(tests.values()),by_file=dict(tests),
        exact_command=(ROOT/'final.resources.txt').read_text().splitlines()[0].strip(),environment=dict(
            PYTHONPATH='src:.:tests',PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
            OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',CUDA_VISIBLE_DEVICES='',
            JE_ARROW_MALLOC_CONF='background_thread:false',ARROW_DEFAULT_MEMORY_POOL='system',LANGCHAIN_OPENAI_TCP_KEEPALIVE='0'),
        junit=str(ROOT/'final.junit.xml'),log=str(ROOT/'final.log')),
    entry_evidence=str(ROOT/'entry_evidence.json'),artifact_sha256={str(p):sha(p) for p in artifacts},
    no_followup_started=True,no_background_process=True)
write('handoff.json',handoff)
written=read(ROOT/'handoff.json')
assert all(sha(p)==d for p,d in written['artifact_sha256'].items())
for link in re.findall(r'\]\(([^)]+)\)',REPORT.read_text()):
    assert (REPORT.parent/link).is_file(),link
print('Indexed',len(cases),'entry runs,',sum(tests.values()),'passes and',len(artifacts),'artifact hashes.')
