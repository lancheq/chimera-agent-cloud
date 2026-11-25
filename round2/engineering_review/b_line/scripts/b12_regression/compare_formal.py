"""Compare actual pytest node outcomes, Socket bytes and SDK/ledger summaries."""
import argparse
from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from preregister import OUT, ROOT, pin


def junit(path):
    root = ET.parse(path).getroot()
    nodes = {}
    for case in root.iter('testcase'):
        node = case.attrib['classname'] + '::' + case.attrib['name']
        status = next((x.tag for x in case if x.tag in ('failure','error','skipped')),'passed')
        assert node not in nodes
        nodes[node] = status
    return nodes


def sockets(directory):
    return {str(p.relative_to(directory)):pin(p)['sha256']
            for p in sorted(directory.rglob('*.json'))
            if p.parent.name == 'sockets' and not any(k == 'pytest-current' for k in p.parts)}


def sdk(directory):
    profiles = {}
    for p in sorted(directory.glob('**/events.jsonl')):
        if p.parent.parent.name != 'private':
            continue
        parsed = [json.loads(line) for line in p.read_text().splitlines()]
        calls = [(e['stage'],e['payload_digest']) for e in parsed if e['kind']=='http_request_verified']
        # Use the pytest directory and its sub-branch, never the runtime session ID.
        key = str(p.parents[2].relative_to(directory))
        finals = [e['after'] for e in parsed if e.get('stage')=='final_sockets']
        profiles.setdefault(key, []).append({'requests':calls, 'finals':finals, 'terminal':parsed[-1]['kind']})
    for cases in profiles.values():
        cases.sort(key=lambda x: json.dumps(x, sort_keys=True))
    return profiles


def deltas(a,b):
    return {item:{'baseline':a.get(item),'candidate':b.get(item)}
            for item in sorted(a.keys()|b.keys()) if a.get(item)!=b.get(item)}


def compare(first,second):
    baseline,junit_c = junit(first/'results.xml'),junit(second/'results.xml')
    old = json.loads((first/'mode.json').read_bytes())
    new = json.loads((second/'mode.json').read_bytes())
    b_out,c_out = sockets(first/'cases'),sockets(second/'cases')
    b_sdk,c_sdk = sdk(first/'cases'),sdk(second/'cases')
    paths = {str(p.relative_to(first/'src')):pin(p)
             for p in (first/'src').rglob('*.py')}
    changed_sources = deltas(paths,{str(p.relative_to(second/'src')):pin(p)
                        for p in (second/'src').rglob('*.py')})
    for f in ('inference.py','reasoning_align.py'):
        assert pin(first/f)==pin(second/f)==pin(ROOT/f)
    assert list(changed_sources) == ['chimera_agent_baseline/agent/b12_evidence_state.py']
    good = {}
    for task in (1,2,3):
        matching = [k for k in baseline if k.endswith(f'::test_formal_adaptive_entry[{task}-good]')]
        node, = matching
        paths = sorted(k for k in b_out if k.startswith(f'test_formal_adaptive_entry_{task}_g0/sockets/'))
        if baseline[node] == 'passed':
            assert len(paths) == 2
        good[str(task)] = {'node':node,'baseline':baseline[node],'candidate':junit_c[node],
                           'baseline_sockets':{p:b_out[p] for p in paths},
                           'candidate_sockets':{p:c_out[p] for p in paths if p in c_out}}
    return {
        'method':'frozen shared source copied byte-for-byte, only the candidate module added in second overlay; default-off',
        'baseline':old,'candidate':new,'changed_overlay_sources':changed_sources,
        'baseline_outcomes':dict(Counter(baseline.values())),'candidate_outcomes':dict(Counter(junit_c.values())),
        'test_status_deltas':deltas(baseline,junit_c),
        'socket_count_baseline':len(b_out),'socket_count_candidate':len(c_out),
        'socket_deltas':deltas(b_out,c_out),
        'audited_sdk_cases_baseline':len(b_sdk),'audited_sdk_cases_candidate':len(c_sdk),
        'sdk_and_formal_outcome_deltas':deltas(b_sdk,c_sdk),
        'adaptive_tasks':good,
        'baseline_fixture_result_location':str(first),'candidate_fixture_result_location':str(second),
        'formal_B12_consumer':'NOT_MEASURED',
    }


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('baseline',type=Path)
    p.add_argument('candidate',type=Path)
    args = p.parse_args()
    print(json.dumps(compare(args.baseline.resolve(),args.candidate.resolve()),indent=2,ensure_ascii=False))
