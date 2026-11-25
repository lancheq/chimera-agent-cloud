"""Collect new outputs and verify frozen inputs before writing the regression handoff."""
from collections import Counter
from datetime import datetime
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from preregister import OUT, ROOT, pin
from review_saved import rows


def read(path):
    return json.loads(path.read_bytes())


def main(review_status, review_turn):
    base = read(OUT/'frozen_baseline.json')
    before = base['pinned'] | read(OUT/'supplemental_pins.json')
    after = {p:pin(ROOT/p) for p in before}
    assert before == after
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==base['head']
    recorded = rows(OUT/'expected_actual.jsonl')
    expectations = [{k:r[k] for k in ('rid','case','probe','inputs','expected')} for r in recorded]
    assert expectations == read(OUT/'expected.json')
    counts = {k:dict(Counter(r['status'] for r in recorded if r['rid']==k)) for k in base['plan_expected']}
    matrix_status = read(OUT/'matrix_status.json')
    failures = [r for r in recorded if r['status']!='PASS']
    assert len(failures)==matrix_status['failed']
    formal = read(OUT/'baseline_delta.json')
    results = {name:formal[name+'_outcomes'] for name in ('baseline','candidate')}
    suite_times = {name:sum(float(s.attrib.get('time',0))
                            for s in ET.parse(OUT/f'formal_final_{name}/results.xml').getroot().iter('testsuite'))
                   for name in ('baseline','candidate')}

    report = ROOT/'reports/B12_REGRESSION_MATRIX.md'
    files = [report, *(OUT/p for p in (
        'expected.json','expected_actual.jsonl','baseline_delta.json','matrix_status.json','matrix.log',
        'frozen_baseline.json','supplemental_pins.json','formal_support.sha256','preregister.py',
        'cases.py','matrix.py','formal.py','compare_formal.py','env.sh','check.sh','review_saved.py',
        'collect_handoff.py','formal_baseline.log','formal_candidate.log','formal_verified_baseline.log',
        'formal_verified_candidate.log','formal_final_baseline.log','formal_final_candidate.log',
        'formal_final_baseline/results.xml','formal_final_candidate/results.xml',
        'formal_final_baseline/mode.json','formal_final_candidate/mode.json',
    ))]
    for item in recorded:
        ledger = item['evidence'].get('audit_log')
        if ledger:
            expected = item['evidence']['audit_sha256']
            path = OUT/ledger
            assert pin(path)['sha256']==expected
            files.append(path)
    gate = {
        'advance':'HOLD',
        'legacy_unexpressible':['known INCOMPARABLE','independent truth adjudication','standalone explanation'],
        'formal_B12_consumer':'NOT_MEASURED',
        'live_started':False,
    }
    report_ids = ('known_incomparable_to_legacy','independent_truth_and_explanation_schema')
    h = {
        'task_id':'B12-REGRESSION','status':'HOLD','matrix_status':'PARTIAL' if failures else 'PASS_EXECUTED',
        'gate':gate,'candidate':base['candidate'],'base_git_head':base['head'],
        'started_beijing':base['started_beijing'],'finished_beijing':datetime.now().astimezone().isoformat(),
        'frozen_before':before,'frozen_after':after,
        'deliverables':{str(p.relative_to(ROOT)):pin(p) for p in files},
        'matrix_probes':len(recorded),'matrix_passed':len(recorded)-len(failures),'row_counts':counts,
        'failures':[r for r in failures],
        'legacy_gap_probes':[r for r in recorded if r['case'] in report_ids],
        'execution_exit_codes':[formal['baseline']['exit'],formal['candidate']['exit'],int(bool(failures))],
        'formal_outcomes':results,'formal_suite_wall_seconds':suite_times,
        'environment':{'python':sys.version,'pytest':version('pytest'),'jsonschema':version('jsonschema')},
        'resources':matrix_status | {'background_processes':0,'stdio':'synthetic only; serial helper processes'},
        'check_command':f'bash {OUT}/check.sh --check',
        'planning_handoff':{'thread':'01a1e862-66be-7991-b3bf-cc6fcb387119',
                            'observed_status':review_status,'observed_turn':review_turn},
    }
    with (OUT/'handoff.json').open('x',encoding='utf-8') as f:
        json.dump(h,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.write('\n')
    print(h['status'],h['matrix_passed'],h['matrix_probes'],len(before),results,suite_times)

if __name__=='__main__':
    main(sys.argv[1],sys.argv[2])
