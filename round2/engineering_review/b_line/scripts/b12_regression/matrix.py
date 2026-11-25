"""Execute the sealed R01-R16 probes and retain expected/actual by layer."""
import argparse
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import sys

from preregister import OUT, ROOT, pin
from chimera_agent_baseline.agent.b12_evidence_state import (
    BindingError, Scope, SnapshotAuthority, derive_state, first_legacy_mismatch, legacy_assertions, restore_state,
)
from chimera_agent_baseline.information_access import AccessDenied, InformationAccess, canonical

SOURCE = ROOT / 'output/b12_source_conflict_20261121'
SHORT = ROOT / 'output/b12_short_output_20261121'
SEMANTIC = ROOT / 'output/b12_semantic_20261122'
LIVE = ROOT / 'output/b12_short_output_live_20261121'
sys.path[:0] = [str(SOURCE), str(SHORT), str(SEMANTIC)]
from synthetic_cases import Claim, Fixture, replay
from replay_minimal import frozen_rows
from protocol import FORMAT, REASONS, format_result

BASE = Claim('psa', observed='2026-01-02', reported='2026-01-06', available='2026-01-07',
             collection_attested=True, identity='sample-a')
CUTOFF = '2027-01-01'
LATE = 'B12_R11_LATE_OBSERVATION_SENTINEL'
FROZEN_RAW = LIVE/'B12_structure/raw_response.json'
FIXTURES = {}


def sha(data):
    return sha256(data).hexdigest()


def check_frozen():
    baseline = json.loads((OUT/'frozen_baseline.json').read_bytes())
    still = {p: pin(ROOT/p) for p in baseline['pinned']}
    assert still == baseline['pinned']
    return still


def load(data, authority, rel):
    state = derive_state(data, authority, rel, enabled=True)
    serialized = state.serialize()
    assert serialized == restore_state(serialized, data, authority, rel, enabled=True).serialize()
    doc = json.loads(data)
    for reference in state.references:
        raw = doc['references'][reference.reference_id]
        assert (reference.value, type(reference.value)) == (raw['raw_value'], type(raw['raw_value']))
        assert reference.path == tuple(raw['original_field']) and reference.group == raw['group']
        assert raw['receipt'] == json.loads(reference.receipt_json)
        assert [reference.annotation.observed, reference.annotation.reported, reference.annotation.available] == [
            raw['assertion'][k] for k in ('observed', 'reported', 'available')]
    references = {r.reference_id for r in state.references}
    assert not references.intersection(dict(state.rejected))
    assert len({r.group for r in state.references}) == state.evidence_group_count
    legacy_round_trip = 'NOT_APPLICABLE'
    if not state.rejected and references and state.value_comparability != 'INCOMPARABLE':
        aliases = {r.reference_id:f'S{i}' for i,r in enumerate(state.references,1)}
        encoded = json.dumps(legacy_assertions(state,aliases) | {'decision_reason':REASONS['limited']})
        stub = {'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':encoded}}],
                'usage':{'completion_tokens':1}}
        parsed = format_result(stub,1024)
        assert parsed['format'] == 'accepted', parsed
        assert first_legacy_mismatch(state,parsed['parsed'],aliases) is None
        legacy_round_trip = 'OLD_STRICT_SCHEMA_ACCEPTED'
    return state, {'snapshot_sha256': authority.sha256, 'private_state_sha256': sha(serialized),
                   'b2': state.b2_status, 'b2_reason': state.b2_reason, 'round_trip': 'EXACT',
                   'observed_fields_are_preserved': True, 'reference_ids': sorted(references),
                   'legacy_round_trip': legacy_round_trip}


def describe(state):
    return {'identity':state.observation_identity, 'comparability':state.value_comparability,
            'conflict':state.conflict_status, 'observations':state.independent_observation_count,
            'groups':state.evidence_group_count, 'order':state.left_relative_to_right,
            'truth':state.true_value_status, 'trend':state.temporal_trend,
            'values':[r.value for r in state.references]}


def fixture_bytes(f):
    raw = canonical(f.result('B12-supplemental')).encode()
    return raw, SnapshotAuthority(Scope(**f.scope), f.cutoff_text, sha(raw))


def fixture(name):
    f = Fixture('REG-' + name, CUTOFF)
    f.cutoff_text = CUTOFF
    return f


def add(f, ref, kind, value, claim):
    if kind == 'card':
        f.card(ref, value, claim)
    elif kind == 'lab':
        f.lab(ref, value, claim)
    else:
        f.extract(ref, kind, value, claim)


def pair(name, upstream_expected, left=None, right=None, values=(5,6),
         left_source='card', right_source='lab'):
    f = fixture(name)
    add(f, 'left', left_source, values[0], replace(BASE, **(left or {})))
    add(f, 'right', right_source, values[1], replace(BASE, **(right or {})))
    f.expect('pair', 'left', 'right', upstream_expected)
    state, evidence = load(*fixture_bytes(f), 'pair')
    return describe(state), evidence


def frozen(name, fixture, relation):
    state, evidence = load(*FIXTURES[fixture], relation)
    return describe(state), evidence | {'fixture': fixture, 'relation': relation}


def error(callback, *args, **kwargs):
    try:
        callback(*args, **kwargs)
    except BindingError as exc:
        return str(exc)
    return 'FALSE_ACCEPT'


def rejected_frozen(name, relation):
    state, evidence = load(*FIXTURES['B12-S3'], relation)
    assert state.rejected == ((relation, dict(state.rejected)[relation]),)
    return {
        'reason':dict(state.rejected)[relation], 'retained_values':[r.value for r in state.references],
        'conflict':state.conflict_status, 'observation_count':state.independent_observation_count,
    }, evidence | {'rejected_reference_projected': False}


def bad_value(name,fault):
    if fault in ('nan','date'):
        raw, authority = FIXTURES['B12-S1']
        snapshot = json.loads(raw)
        row = snapshot['references']['lab-original-receipt']
        if fault == 'nan':
            row['raw_value'] = float('nan')
        else:
            row['assertion']['observed'] = '2026-02-30'
        mutation = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=True).encode()
        reason = error(derive_state,mutation,replace(authority,sha256=sha(mutation)),'after_repeat',enabled=True)
        return {'reason':reason,'value_exported':reason=='FALSE_ACCEPT'}, {'controlled_mutant_sha256': sha(mutation)}
    f = fixture(name)
    f.card('left', 5, BASE)
    f.card('right', None if fault=='missing' else True, BASE)
    f.expect('pair','left','right','UNKNOWN')
    state, evidence = load(*fixture_bytes(f),'pair')
    return {'reason':dict(state.rejected)['right'],
            'value_exported':any(r.reference_id=='right' for r in state.references)}, evidence


def isolated_cutoff(name,fault):
    f = fixture(name)
    f.card('left',5,BASE)
    value = 88119165
    date = '2027-02-01' if fault=='late' else None
    f.lab('withheld',value,replace(BASE,available=date))
    f.expect('pair','left','withheld','UNKNOWN')
    state, evidence = load(*fixture_bytes(f),'pair')
    residual = canonical({'synthetic_private_diagnosis_input':[r.value for r in state.references]})
    return {'reasons':[v for _,v in state.rejected], 'references':len(state.references),
            'late_in_state':str(value).encode() in state.serialize(),
            'legacy_exported':error(legacy_assertions,state,{})=='FALSE_ACCEPT',
            'late_in_request_fixture':str(value) in residual}, evidence | {'private_request_sha256':sha(residual.encode())}


def raw_audit(ledger):
    return [json.loads(line) for line in ledger.read_text().splitlines()]


def formal_cutoff(name,work):
    from pytest import MonkeyPatch
    import test_b_evidence_reconciliation_build as formal
    work.mkdir()
    old_fixture = formal.fixture
    lab = deepcopy(formal.LAB)
    lab['laboratory_results'].append({'name':'PSA','val':LATE,'unit':'fixture-unit'})

    def dated(task):
        base = old_fixture(task)
        a,b = base.annotations
        b = replace(b,document_digest=formal.digest(lab))
        late = replace(b,path=('laboratory_results',1,'val'),available='2020-08-02')
        return replace(base, annotations=(a,b,late))

    with MonkeyPatch.context() as env:
        env.setattr(formal,'LAB',lab)
        env.setattr(formal,'fixture',dated)
        payloads, _ = formal.run_entry(work,env,1,'stdio_on')
    summary_path = work/'fixture_summary.json'
    summary = json.loads(summary_path.read_bytes())
    ledger = Path(summary['ledger'])
    events = raw_audit(ledger)
    b = [e for e in events if e.get('stage')=='b_evidence_agent_input']
    status = [r['status'] for e in b for r in e.get('sources',())
              if r.get('reference',{}).get('path') == ['laboratory_results',1,'val']]
    audit_sends = [e for e in events if e['kind']=='http_request_verified']
    return {'sdk_sends':len(payloads),'late_in_b_projection':any(LATE in canonical(e['after']) for e in b),
            'late_sdk_request_indexes':[i for i,p in enumerate(payloads) if LATE in canonical(p)],
            'ledger_status':status[-1], 'two_sockets':len(summary['files'])==2}, {
                'fixture':'root inference.py / true synthetic MCP stdio / SDK MockTransport',
                'audit_log':str(ledger.relative_to(work.parent)),
                'audit_sha256':pin(ledger)['sha256'],
                'http_payload_digests':[e['payload_digest'] for e in audit_sends],
                'http_request_stages':[e['stage'] for e in audit_sends],
                'socket_files':summary['files'],
            }


def t3_guard(name,work):
    work.mkdir()
    guard = InformationAccess(ledger_path=work/'t3.sqlite',session_id='T3-synthetic-session',case_id='synthetic-3',
                              task=3,snapshot_id='registered-fixture',budget_units=1,max_attempts=2)
    fetches = []
    reason = 'FALSE_ACCEPT'
    try:
        guard.execute('get_psa_trend','synthetic-3',lambda: fetches.append(1))
    except AccessDenied as exc:
        reason = str(exc)
    ledger = guard.snapshot()
    return {'denial':reason,'backend_reads':len(fetches),'spent_units':ledger['spent_units'],
            'admitted_attempts':ledger['admitted_attempts']}, {'guard_events':ledger['events']}


def assertion_states(name):
    from test_b_evidence_reconciliation_build import EvidenceSession, Source, UPFRONT, LAB, binding, digest, fixture
    authority = fixture(lab=False)
    raw = UPFRONT | {'pirads_int':False, 'vol':'planned fixture', 'psad':None}
    template = replace(authority.annotations[0],document_digest=digest(raw))
    states = [('pirads_int','negated'),('vol','planned'),('psad','missing'),('psa','uncertain')]
    annotations = tuple(replace(template,path=(key,),state=value) for key,value in states)
    sess = EvidenceSession(replace(authority,annotations=annotations),'fixture-audit',binding(),raw)
    sess.sources.append(Source('get_lab_results',LAB,{}))
    projection, audit = sess._project()
    return {'types':[v['state'] for v in projection['observations']], 'relations':len(projection['relations']),
            'unbound_history_numbers':sum(v['reference']['source']=='get_lab_results'
                                         for v in projection['observations'])}, {'statuses':[item['status'] for item in audit]}


def corrupt(name,fault):
    raw, authority = FIXTURES['B12-S1']
    if fault == 'model':
        model = json.loads(FROZEN_RAW.read_bytes())['choices'][0]['message']['content'].encode()
        return {'reason':error(derive_state,model,authority,'after_repeat',enabled=True)},{'input_sha256':sha(model)}
    d = json.loads(raw)
    ref = d['references']['lab-original-receipt']
    if fault == 'core':
        d['relations']['after_repeat']['core_comparison']['status'] = 'same_observation_same_value'
    elif fault == 'status':
        d['relations']['after_repeat']['status'] = 'UNKNOWN'
    elif fault == 'alias_assertion':
        ref['assertion']['method'] = 'contradiction'
    elif fault == 'scope':
        ref['scope']['snapshot_id'] = 'wrong'
    elif fault == 'receipt':
        ref['receipt']['response_digest'] = 'wrong'
    elif fault == 'path':
        ref['original_field'][1] = 9
    else:
        raise AssertionError(fault)
    bad = canonical(d).encode()
    return {'reason':error(derive_state,bad,replace(authority,sha256=sha(bad)),'after_repeat',enabled=True)}, {
        'controlled_mutant_sha256':sha(bad),'authority_kind':'private synthetic negative fixture'}


def response(name,variant):
    raw, authority = FIXTURES['B12-S1']
    relation = 'after_repeat'
    aliases = {'card':'S1','lab-original-receipt':'S2','lab-repeat-receipt':'S3'}
    if variant == 'elevate':
        f = fixture(name)
        f.card('left',5,replace(BASE,identity=None))
        f.lab('right',6,replace(BASE,identity=None))
        f.expect('pair','left','right','UNKNOWN')
        raw, authority = fixture_bytes(f)
        relation,aliases = 'pair',{'left':'S1','right':'S2'}
    state, evidence = load(raw,authority,relation)
    expected = legacy_assertions(state,aliases)
    captured = json.loads(FROZEN_RAW.read_bytes())
    frozen_content = captured['choices'][0]['message']['content']
    body = deepcopy(captured)
    msg = body['choices'][0]['message']
    if variant != 'historic':
        expected_draft = deepcopy(expected)
        expected_draft['decision_reason'] = json.loads(frozen_content)['decision_reason']
        if variant == 'elevate':
            expected_draft['conflict_status'] = 'same_observation_conflict_unresolved'
        elif variant == 'default':
            expected_draft['measurements'][0]['value'] = 0
        elif variant == 'alias':
            expected_draft['measurements'][1]['source_ids'].append('S2')
        elif variant == 'length':
            body['choices'][0]['finish_reason'] = 'length'
        elif variant == 'extended_schema':
            expected_draft.update(observation_identity='SAME_CONFIRMED', value_comparability='COMPARABLE',
                                  true_value_status='UNRESOLVED', explanation='Both values remain unresolved.')
        msg['content'] = json.dumps(expected_draft)
        if variant == 'duplicate_key':
            msg['content'] = msg['content'][:-1] + ', "conflict_status": "UNKNOWN"}'
    content = msg['content']
    wire = canonical(body)
    original = state.serialize()
    parsed = format_result(body,1024)
    if parsed['format'] == 'accepted':
        fields = parsed['parsed']
        before = deepcopy(fields)
        actual = {'format':'accepted','first_semantic_mismatch':first_legacy_mismatch(state,fields,aliases)}
        assert before == fields
        if variant in ('historic','elevate'):
            actual.update(original_format_claim=fields['conflict_status'], trusted_claim=expected['conflict_status'])
    else:
        actual = {'format':parsed['format'],'format_reason':parsed['reason'],'semantic':'NOT_RUN'}
    actual['raw_unchanged'] = canonical(body)==wire and msg['content']==content and state.serialize()==original
    evidence |= {'raw_response_content':content,'raw_content_sha256':sha(content.encode()),
                 'raw_decision_reason':json.loads(frozen_content)['decision_reason'],
                 'standalone_explanation':'NOT_MEASURED', 'reason_enum_only': True}
    return actual, evidence


def round_trip(name):
    raw, authority = FIXTURES['B12-S1']
    state,evidence = load(raw,authority,'after_repeat')
    serialized = state.serialize()
    return {'exact_round_trip':restore_state(serialized,raw,authority,'after_repeat',enabled=True)==state,
            'reason':error(restore_state,serialized.replace(b'CONFIRMED_UNRESOLVED',b'UNKNOWN',1),
                           raw,authority,'after_repeat',enabled=True),
            'off':derive_state(b'invalid',None,'not evaluated') is None},evidence


def legacy_gap(name):
    state,evidence = load(*FIXTURES['B12-S2'],'different_method')
    reason = error(legacy_assertions,state,{r.reference_id:r.reference_id for r in state.references})
    conflict_enum = FORMAT['json_schema']['schema']['properties']['conflict_status']['enum']
    return {'reason':reason,'internal_status':state.value_comparability,
            'legacy_schema_support':'INCOMPARABLE' in conflict_enum},evidence | {'legacy_conflict_enum': conflict_enum}


def prior(name):
    p = ROOT/'output/b_source_time_feasibility_20261120/synthetic_counterexamples.jsonl'
    original = [json.loads(line) for line in p.read_bytes().splitlines()]
    synthesized = replay(original)
    fixed = [json.loads(line) for line in (SOURCE/'synthetic_replay.jsonl').read_bytes().splitlines()]
    return {'old_counterexamples':len(original),'supplemental':len(synthesized)-len(original),
            'equal_frozen_jsonl':synthesized==fixed}, {'old_counterexample_sha256':pin(p)['sha256']}


PROBES = {f.__name__:f for f in (pair,frozen,rejected_frozen,bad_value,isolated_cutoff,formal_cutoff,t3_guard,
                                  assertion_states,corrupt,response,round_trip,legacy_gap,prior)}


def main(target):
    assert len(os.sched_getaffinity(0)) == 1
    os.umask(0o077)
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    class SerialPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            loop = super().new_event_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            return loop
    policy = SerialPolicy()
    policy.set_child_watcher(asyncio.SafeChildWatcher())
    asyncio.set_event_loop_policy(policy)

    pinned = check_frozen()
    FIXTURES.update(frozen_rows())
    matrix = json.loads((OUT/'expected.json').read_bytes())
    assert {x['rid'] for x in matrix} == {f'R{i:02}' for i in range(1,17)}
    target.mkdir(parents=True,exist_ok=True)
    failed = 0
    with (target/'expected_actual.jsonl').open('x',encoding='utf-8') as f:
        for item in matrix:
            name, probe = item['case'], item['probe']
            extra = {'work':target/name} if probe in ('t3_guard','formal_cutoff') else {}
            try:
                actual, evidence = PROBES[probe](name,**item['inputs'],**extra)
            except Exception as exc:
                actual, evidence = {'exception_type':type(exc).__name__,'message':str(exc)}, {}
            expected = item['expected']
            first = next((k for k,v in expected.items() if k not in actual or actual[k]!=v),None)
            if first is None:
                first = next((k for k in actual if k not in expected),None)
            failed += first is not None
            record = item | {'actual':actual,'evidence':evidence,'status':'PASS' if first is None else 'FAIL',
                             'first_mismatch':first}
            record['log_sha256'] = sha(canonical(record).encode())
            f.write(canonical(record)+'\n')
            print(item['rid'],name,record['status'],first or '',flush=True)
    assert pinned == check_frozen()
    use = resource.getrusage(resource.RUSAGE_SELF)
    (target/'matrix_status.json').write_text(canonical({'rows':len(matrix),'failed':failed,
        'cpu':list(os.sched_getaffinity(0)), 'workers':1,'max_rss_kib':use.ru_maxrss,
        'model_generation':0,'gpu':0,'clinical_tools':0,'information_units_real':0})+'\n')
    return 1 if failed else 0

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('target',type=Path)
    raise SystemExit(main(parser.parse_args().target.resolve()))
