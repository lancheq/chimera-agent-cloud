"""C02 security/semantic boundaries on synthetic records only; no model calls."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import pytest

from chimera_agent_baseline.evidence_package import (EvidencePackager, EvidencePackageError,
    legal_context, file_sha, load_overlay, VERSION)
from chimera_agent_baseline.information_access import InformationAccess, AccessDenied, canonical
from chimera_agent_baseline.guarded_runtime import verify_messages
from langchain_core.messages import ToolMessage

ROOT = Path(os.environ.get('CHIMERA_C02_PROJECT_ROOT', Path(__file__).resolve().parents[1]))
RESOURCE = Path(os.environ.get('CHIMERA_C02_RESOURCE_DIR', ROOT/'output/evidence_package_candidate_20261117/resources_v2'))


@pytest.fixture
def setup(tmp_path):
    resource = tmp_path/'resources';shutil.copytree(RESOURCE, resource)
    overlay_path=resource/'overlay.json';overlay=json.loads(overlay_path.read_text());pin=file_sha(overlay_path)
    data=tmp_path/'task1';case=data/'A';case.mkdir(parents=True)
    prompt=case/'prompt.json';prompt.write_text(json.dumps({'case_id':'A','biopsy_context':'initial',
        'dre':'negative','family_history_prostate_cancer':False}))
    config={'ledger_path':str(tmp_path/'ledger.sqlite'),'session_id':'synthetic-c02','case_id':'A','task':1,
        'snapshot_id':'synthetic-source','initial_source_sha256':file_sha(prompt),'budget_units':1,'max_attempts':8,
        'knowledge_snapshot_id':overlay['knowledge_snapshot_id'],'evidence_package_id':pin}
    access=InformationAccess(**config)
    options={'overlay_path':str(overlay_path),'overlay_sha256':pin,'max_consumer_bytes':24000}
    package=EvidencePackager(options,access,data)
    valid=next(x for x in overlay['hits'] if x['handling']=='conditional_reference')
    # Retrieve the exact carrier shape already bound by the overlay.
    qa=[json.loads(l) for l in (ROOT/'output/s2_knowledge_snapshot_20261113_v4/corpus/guideline_qa.jsonl').read_text().splitlines()]
    q=next(x for x in qa if x['id']=='qa-T1-s16-048');g=q['grounding'][0]
    text=f"[{g['source']} {g['section_id']}] {g['section_title']}\n{q['support_quote']}"
    hit={'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
         'knowledge_snapshot_id':overlay['knowledge_snapshot_id'],'type':'qa'}
    assert hit['text_sha256']==valid['text_sha256']
    return dict(package=package,access=access,data=data,prompt=prompt,overlay=overlay,path=overlay_path,
                hit=hit,config=config,options=options)


def reveal(x, report=None):
    x['access'].execute('get_mri_report','A',lambda:{'case_id':'A','radiology_report':
        report if report is not None else 'IMPRESSION:\nPI-RADS 2.\nPSA density: 0.08 ng/mL/mL.'})


def reprompt(x, raw):
    x['prompt'].write_text(json.dumps(raw));x['config']['initial_source_sha256']=file_sha(x['prompt'])
    x['config']['ledger_path']=str(Path(x['config']['ledger_path']).with_name('other.sqlite'))
    x['access']=InformationAccess(**x['config']);x['package']=EvidencePackager(x['options'],x['access'],x['data'])


def test_positive_exact_source_with_complete_legally_observed_conditions_reaches_consumer(setup):
    x=setup;reveal(x)
    payload=x['access'].execute('search_guidelines','A',lambda:x['package'].package('q',[x['hit']]),query='q')
    item=payload['results'][0]
    assert item['evidence_package']['condition_status_at_retrieval']=='matched'
    assert item['evidence_package']['strength']=='Weak'
    assert not item['evidence_package']['automatic_action_authorized']
    message=ToolMessage(content=json.dumps(payload),name='search_guidelines',tool_call_id='q')
    verify_messages(x['access'],[message])
    events=[json.loads(l) for l in (x['access'].path.parent/'evidence_package_audit.jsonl').read_text().splitlines()]
    assert events[-1]['event']=='verified_consumer_payload'
    assert events[0]['consumer_payload_digest']==events[-1]['consumer_payload_digest']
    assert x['access'].snapshot()['spent_units']==1


def test_unobtained_mri_and_missing_family_history_never_become_negative(setup):
    x=setup;reprompt(x,{'case_id':'A','biopsy_context':'initial','dre':'negative'})
    payload=x['package'].package('q',[x['hit']]);meta=payload['results'][0]['evidence_package']
    checks={c['field']:c['status'] for c in meta['conditions']}
    assert checks['pi_rads']==checks['psa_density']==checks['family_history_prostate_cancer']=='unknown'
    assert meta['condition_status_at_retrieval']=='unknown' and not meta['automatic_action_authorized']


@pytest.mark.parametrize('context',['initial','prior_negative','known_cancer',None])
def test_management_populations_not_mixed(setup,context):
    x=setup;raw={'case_id':'A','dre':'negative','family_history_prostate_cancer':False}
    if context is not None:raw['biopsy_context']=context
    reprompt(x,raw);reveal(x);payload=x['package'].package('q',[x['hit']])
    if context=='known_cancer':assert payload['results']==[]
    else:
        assert payload['results']
        assert payload['results'][0]['evidence_package']['condition_status_at_retrieval']==('unknown' if context is None else 'matched')


def test_conflicting_observed_and_upfront_facts_stay_conflict(setup):
    x=setup;reprompt(x,{'case_id':'A','biopsy_context':'initial','pi_rads':3});reveal(x)
    fact=legal_context(x['access'],x['data'])['facts']['pi_rads']
    assert fact['status']=='conflict' and fact['value'] is None
    assert x['package'].package('q',[x['hit']])['results'][0]['evidence_package']['condition_status_at_retrieval']=='unknown'


@pytest.mark.parametrize('report',[
 'No PI-RADS 3 or higher lesion is identified. No explicit score.',
 'Prior score mentioned in prose: PI-RADS 2.',
 'PI-RADS 2.\nPI-RADS 4.'])
def test_negation_prose_or_conflicting_scores_cannot_become_unambiguous_fact(setup,report):
    reveal(setup,report)
    assert legal_context(setup['access'],setup['data'])['facts']['pi_rads']['status']!='known'


def test_declared_numeric_density_without_units_is_unknown(setup):
    reveal(setup,'PI-RADS 2.\nPSA density: 0.08')
    assert legal_context(setup['access'],setup['data'])['facts']['psa_density']['status']=='unknown'


def test_unmet_condition_is_not_reported_matched_and_full_else_branch_kept(setup):
    reveal(setup,{'pi_rads':2,'psa_density':0.35})
    item=setup['package'].package('q',[setup['hit']])['results'][0]
    assert item['evidence_package']['condition_status_at_retrieval']=='unmet'
    assert 'otherwise consider' in item['text'] and not item['evidence_package']['automatic_action_authorized']


def test_fake_visibility_and_cross_case_never_authorize(setup):
    with pytest.raises(AccessDenied,match='trusted_information_access'):legal_context({'visible':True},setup['data'])
    with pytest.raises(TypeError):setup['package'].package('q',[setup['hit']],visibility={'pi_rads':2})
    reprompt(setup,{'case_id':'B','biopsy_context':'initial'})
    with pytest.raises(AccessDenied,match='cross_case'):setup['package'].package('q',[setup['hit']])


def test_changed_upfront_rejected_without_reading_hidden_casefile(setup):
    setup['prompt'].write_text('{"case_id":"A","pi_rads":2}')
    with pytest.raises(AccessDenied,match='upfront_source_identity'):setup['package'].package('q',[setup['hit']])


@pytest.mark.parametrize('kind',['wrong_text','wrong_kb','malformed'])
def test_corrupt_hits_fail_closed(setup,kind):
    hit=deepcopy(setup['hit'])
    if kind=='wrong_text':hit['text']='substituted recommendation'
    elif kind=='wrong_kb':hit['knowledge_snapshot_id']='other-resource'
    else:hit={'text':None}
    with pytest.raises(EvidencePackageError):setup['package'].package('q',[hit])


def test_organization_name_does_not_promote_unknown_source_and_empty_is_explicit(setup):
    text='AUA official-looking recommendation with no verified binding'
    hit={**setup['hit'],'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest()}
    for hits in ([hit],[]):
        payload=setup['package'].package('q',hits)
        assert not payload['results'] and payload['note']['status']=='no_verified_reference'


def test_duplicate_sources_not_counted_twice_and_original_rank_preserved(setup):
    hit=deepcopy(setup['hit']);payload=setup['package'].package('q',[hit,hit])
    assert len(payload['results'])==1 and payload['results'][0]['evidence_package']['original_rank']==0
    log=json.loads((setup['access'].path.parent/'evidence_package_audit.jsonl').read_text().splitlines()[-1])
    assert len(log['raw_hits'])==2 and log['dispositions'][1]['handling']=='duplicate_source'


@pytest.mark.parametrize('change',['identity','version','source_version','source_file','unbound_text'])
def test_changed_overlay_or_sources_rejected(setup,change):
    path=setup['path'];overlay=deepcopy(setup['overlay']);expected=file_sha(path)
    if change=='identity':expected='0'*64
    elif change=='version':overlay['version']='unsupported'
    elif change=='source_version':overlay['sources']['eau_2025']['version']=''
    elif change=='unbound_text':overlay['sources']['eau_2025']['verification_text']='other.txt'
    else:
        source=path.parent/overlay['sources']['eau_2025']['files'][0]['path'];source.write_bytes(b'changed')
    if change not in ('identity','source_file'):
        path.write_text(json.dumps(overlay));expected=file_sha(path)
    with pytest.raises(EvidencePackageError):load_overlay(path,expected)


def test_bounded_failure_has_no_raw_source_fallback_or_free_retry(setup):
    setup['options']['max_consumer_bytes']=512
    p=EvidencePackager(setup['options'],setup['access'],setup['data'])
    with pytest.raises(AccessDenied,match='fetch_failed'):
        setup['access'].execute('search_guidelines','A',lambda:p.package('q',[setup['hit']]),query='q')
    state=setup['access'].snapshot()
    assert state['admitted_attempts']==1 and state['spent_units']==0 and state['pending_requests']==0
    with pytest.raises(AccessDenied,match='unobserved_knowledge'):setup['access'].require_knowledge('q')


def test_overlay_identity_is_part_of_ledger_binding(setup):
    with pytest.raises(ValueError,match='binding differs'):
        InformationAccess(**{**setup['config'],'evidence_package_id':'other-version'})


def test_all_69_audit_rows_preserved_and_p60_reason_corrected(setup):
    rules={r['rule_id']:r for r in setup['overlay']['rules']}
    assert len(rules)==69
    assert 'crescentic' in rules['P60']['audit_note'] and 'Withdraw' in rules['P60']['audit_note']
    assert rules['P60']['status']=='mismatch' and rules['P61']['status']=='incomplete'
    assert all(r['runtime_handling']=='audit_mapping_not_official_action_authority' for r in rules.values())
    assert 'Conditional systematic/C' in rules['A28']['recommendation_grade']
    original=json.loads((ROOT/'output/evidence_applicability_audit_20261117/rule_ledger.json').read_text())
    assert {(r['rule_id'],r['status']) for r in original['rules']}=={(r['rule_id'],r['status']) for r in rules.values()}


def test_ambiguous_alternative_mri_scores_are_unknown(setup):
    reveal(setup,'PI-RADS 2 or 4.\nPSA density: 0.08 ng/mL/mL.')
    assert legal_context(setup['access'],setup['data'])['facts']['pi_rads']['status']=='unknown'


def test_cached_delivery_still_honors_reduced_length_limit(setup):
    payload=setup['package'].package('q',[setup['hit']])
    setup['options']['max_consumer_bytes']=512
    restricted=EvidencePackager(setup['options'],setup['access'],setup['data'])
    with pytest.raises(EvidencePackageError,match='consumer_context_limit'):restricted.validate_delivery(payload)


@pytest.mark.parametrize('kind',['directory','unverified_task_adaptation'])
def test_identified_nonrecommendation_hits_never_reach_consumer(setup,kind):
    import sqlite3
    db=sqlite3.connect('file:'+str(ROOT/'output/s2_knowledge_snapshot_20261113_v4/corpus/guidelines_db/chroma.sqlite3')+'?mode=ro',uri=True)
    hashes={x['text_sha256'] for x in setup['overlay']['hits'] if x['handling']==kind}
    hits=[]
    for (text,) in db.execute("SELECT string_value FROM embedding_metadata WHERE key='chroma:document'"):
        h=hashlib.sha256(text.encode()).hexdigest()
        if h in hashes:hits.append({**setup['hit'],'text':text,'text_sha256':h})
    db.close();assert len(hits)==2
    payload=setup['package'].package('q',hits)
    assert payload['results']==[] and payload['note']['raw_hit_count']==2
    log=json.loads((setup['access'].path.parent/'evidence_package_audit.jsonl').read_text().splitlines()[-1])
    assert len(log['raw_hits'])==2 and all(z['handling']==kind for z in log['dispositions'])


@pytest.mark.parametrize('report,field',[
 ('PI-RADS 2 was the prior score; current PI-RADS 4.','pi_rads'),
 ('PI-RADS 2 could not be confirmed.','pi_rads'),
 ('PSA density: 0.08 ng/mL/mL previously; now 0.25 ng/mL/mL.','psa_density'),
 ('PSA density: 0.08 ng/mL/mL could not be confirmed.','psa_density')])
def test_historical_or_unconfirmed_numeric_prefix_is_not_current_fact(setup,report,field):
    reveal(setup,report)
    assert legal_context(setup['access'],setup['data'])['facts'][field]['status']=='unknown'


def test_untrusted_tokenizer_endpoint_is_rejected_without_network(setup,monkeypatch):
    setup['options']['tokenizer']={'base_url':'https://untrusted.invalid','model_id':'x','max_evidence_tokens':1}
    package=EvidencePackager(setup['options'],setup['access'],setup['data'])
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:pytest.fail('No remote request allowed'))
    payload=setup['package'].package('q',[setup['hit']])
    with pytest.raises(EvidencePackageError,match='invalid_local_tokenizer_binding'):package.validate_delivery(payload)


def test_tokenizer_error_is_explicit_no_character_count_fallback(setup,monkeypatch):
    setup['options']['tokenizer']={'base_url':'http://127.0.0.1:8765','model_id':'synthetic-model','max_evidence_tokens':8000}
    package=EvidencePackager(setup['options'],setup['access'],setup['data'])
    def broken(*args,**kwargs):raise OSError('endpoint down')
    monkeypatch.setattr('urllib.request.urlopen',broken)
    payload=setup['package'].package('q',[setup['hit']])
    with pytest.raises(EvidencePackageError,match='tokenizer_unavailable'):package.validate_delivery(payload)


@pytest.mark.parametrize('report,field',[
 ('PREVIOUS MRI FINDINGS:\nPI-RADS 2.\nCURRENT MRI: not yet performed.','pi_rads'),
 ('HISTORICAL MEASUREMENT:\nPSA density: 0.08 ng/mL/mL.\nCurrent density has not been measured.','psa_density'),
 ('PRIOR REPORT:\nIMPRESSION:\nPI-RADS 2.\nPSA density: 0.08 ng/mL/mL.','pi_rads'),
 ('IMPRESSION:\nPI-RADS 2.\nPSA density: 0.08 ng/mL/mL.\nThese measurements were not confirmed.','psa_density'),
 ({'pi_rads':2,'psa_density':0.08,'context':'previous report'},'pi_rads')])
def test_surrounding_history_or_uncertainty_makes_narrative_format_unsupported(setup,report,field):
    reveal(setup,report)
    assert legal_context(setup['access'],setup['data'])['facts'][field]['status']=='unknown'


RULE_IDS = ['A'+str(n) for n in [11,12,13,14,18,19,20,21,27,28,29,30,31,32,33,34,35,39,40,41,42,43,47,48,49,50,51]] + ['P'+str(n) for n in [9,10,11,12,13,14,16,17,18,24,25,26,27,28,29,35,36,37,38,39,40,41,47,48,49,50,51,57,58,59,60,61,65,66,72,73,74,78,79,80,81,82]]


@pytest.mark.parametrize('rule_id',RULE_IDS)
def test_every_secondary_rule_row_binds_exact_archived_line_without_authority_promotion(setup,rule_id):
    rule=next(x for x in setup['overlay']['rules'] if x['rule_id']==rule_id)
    path=setup['path'].parent/rule['summary_source']['path']
    assert file_sha(path)==rule['summary_source']['sha256']
    assert path.read_text().splitlines()[rule['line']-1]==rule['text']
    assert rule['primary_locator'] and rule['necessary_conditions'] and rule['exceptions']
    assert rule['source_level']=='project_secondary_summary'
    assert rule['primary_source_binding_status']=='manual_audit_locator_only_not_runtime_authorized'
