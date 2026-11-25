"""Seal approval, current protected files and exact R01-R16 text."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/b12_regression_20261122'
APPROVAL = ROOT / 'output/b12_semantic_planning_review_20261122/accepted_candidate.json'


def pin(path):
    raw = Path(path).read_bytes()
    return {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}


def relative(path):
    return str(path.relative_to(ROOT))


def freeze():
    receipt = json.loads(APPROVAL.read_bytes())
    assert receipt['accepted'] == 'PASS_ISOLATED_CANDIDATE'
    approved = ROOT / receipt['execution_handoff']
    assert pin(approved)['sha256'] == receipt['execution_handoff_sha256']
    handoff = json.loads(approved.read_bytes())
    assert receipt['candidate'] == handoff['new_source_file']
    assert handoff['protected_fingerprints_before'] == handoff['protected_fingerprints_after']
    pinned = handoff['protected_fingerprints_before'] | handoff['deliverables']
    assert pin(ROOT/receipt['candidate']['path']) == receipt['candidate']['after']
    pinned |= {receipt['candidate']['path']: receipt['candidate']['after']}
    for key in (
        relative(APPROVAL), relative(approved), 'reports/B12_SEMANTIC_PLANNING_REVIEW_20261122.md',
        'discussion/tasks/20261122_B12_REGRESSION.md', 'pyproject.toml',
        'output/b_source_time_feasibility_20261120/synthetic_counterexamples.jsonl',
    ):
        pinned[key] = pin(ROOT/key)
    assert pinned['output/b_source_time_feasibility_20261120/synthetic_counterexamples.jsonl']['sha256'] == (
        'd4ecc0785b874f06687a996d3fb1bf6820396ee2b5df2b11ae6aba0633bf1d89')
    for folder in ('src', 'tests', 'configs', 'templates',
                   'output/s4_control_repair_20261114/source_snapshot/configs',
                   'output/p01_mechanism_acceptance_20261117/fixtures'):
        for path in sorted((ROOT/folder).rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.json', '.yaml', '.yml', '.jinja', '.jinja2', '.txt', '.md') and '__pycache__' not in path.parts:
                pinned.setdefault(relative(path), pin(path))
    assert all(pin(ROOT/p)==v for p,v in pinned.items())
    plan = (ROOT/'discussion/B12_EXECUTION_PLAN_20261122.md').read_text()
    table = dict((key, {'condition': c, 'required': expected})
                 for key, c, expected in re.findall(r'^\| (R\d{2}) ([^|]+?) \| ([^|]+?) \|$', plan, re.M))
    assert list(table) == [f'R{i:02}' for i in range(1,17)]
    head = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    assert head == handoff['base_git_head']
    with (OUT/'frozen_baseline.json').open('x',encoding='utf-8') as f:
        json.dump({'started_beijing':datetime.now().astimezone().isoformat(),'head':head,
                   'candidate':receipt['candidate'],'pinned':pinned, 'plan_expected':table},
                   f,indent=2,ensure_ascii=False)
        f.write('\n')
    print(f'{len(pinned)} frozen paths')
if __name__ == '__main__':
    freeze()
