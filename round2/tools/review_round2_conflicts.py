#!/usr/bin/env python3
"""P3 input-only source/time/unit review of the frozen historical candidates.

Does not adjudicate clinical truth, choose a preferred value, or repair inputs.
Only unambiguous quantity-type parsing errors may close an old screen flag.
"""
from __future__ import annotations
import argparse
import calendar
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from tools.conflict_audit import _psa_sources

ROOT=Path(__file__).resolve().parents[1]
POOL=ROOT/'output/round2_data_pool_20261113_v2'
CANDIDATES=ROOT/'output/round2_prerequisites_20261113/conflict_candidates.jsonl'
OLD_PSA=re.compile(r'\bPSA\b[^0-9]{0,12}(\d+(?:\.\d+)?)',re.I)


def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def date_interval(raw):
    if not isinstance(raw,str):return None
    raw=' '.join(raw.strip().split())
    for fmt,precision in [('%Y-%m-%d','day'),('%d %b %Y','day'),('%d %B %Y','day'),
                          ('%b %Y','month'),('%B %Y','month'),('%Y-%m','month'),('%Y','year')]:
        try:d=datetime.strptime(raw,fmt).date()
        except ValueError:continue
        end=d if precision=='day' else (date(d.year,d.month,calendar.monthrange(d.year,d.month)[1]) if precision=='month' else date(d.year,12,31))
        return {'raw':raw,'precision':precision,'start':d.isoformat(),'end':end.isoformat()}
    return None


def unit_kind(raw):
    s=str(raw).lower().replace('μ','u').replace('µ','u').replace(' ','')
    if re.search(r'(?<![a-z])(?:ng/ml|ug/l)(?!/ml)',s):return 'ng_per_ml_equivalent'
    return 'unrecorded_or_unrecognized'


def semantic_kind(match):
    if match and re.match(r'PSA\s+density\b',match.group(),re.I):return 'psa_density_not_serum_psa'
    return 'serum_psa_candidate'


def observation(path,raw,value,when=None,document_when=None,match=None):
    return {'field_path':path,'raw':raw,'numeric_value':value,'quantity':semantic_kind(match),
        'measurement_date':date_interval(when),'document_date':date_interval(document_when),
        'unit':unit_kind(str(raw)[match.end():match.end()+16]) if match else unit_kind(raw),
        'quote':match.group() if match else json.dumps(raw,ensure_ascii=False),
        'measurement_time_not_inferred_from_document_date':True}


def psa_observations(card,ehr):
    # Preserve historical extraction to explain each old flag; annotate rather
    # than silently replace it with a newer, differently scoped detector.
    values=_psa_sources(card,ehr);items={}
    for name,value in values.items():
        if name=='card':x=observation('prompt.psa',card.get('psa'),value,document_when=card.get('enc_date'))
        elif name=='psa_trend[-1]':
            a=ehr['psa_trend'][-1];x=observation('clinical.psa_trend[-1]',a,value,a.get('date'))
        elif name=='lab':
            i,a=next((i,a) for i,a in enumerate(ehr.get('laboratory_results') or []) if isinstance(a,dict) and 'psa' in str(a.get('name','')).lower())
            x=observation(f'clinical.laboratory_results[{i}]',a,value,a.get('date'))
            literal=re.match(r'\s*(?:[<>≤≥]=?\s*)?([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)',str(a.get('val','')))
            if literal:
                parsed=float(literal.group(1))
                if math.isfinite(parsed) and parsed!=value:
                    x.update(legacy_numeric_value=value,numeric_value=parsed,numeric_parse_corrected_in_audit_only=True)
            x['unit']=unit_kind(a.get('val'));x['lab_name']=a.get('name')
            if str(a.get('name','')).strip().lower() not in ('psa','total psa','prostate specific antigen','prostate-specific antigen'):
                x['quantity']='not_confirmed_total_psa'
            if re.match(r'\s*[<>≤≥]',str(a.get('val',''))):x['censored']=True
        else:
            raw=str(card.get('notes') or '') if name=='card.notes' else str(ehr.get('radiology_report') or '')
            m=OLD_PSA.search(raw);x=observation('prompt.notes' if name=='card.notes' else 'clinical.radiology_report',raw,value,match=m)
            if m:
                # Only an explicit date attached to this value plus an explicit
                # "now" comparator establishes the historical/current relation.
                linked=re.match(r'\s*(ng/mL|[uµμ]g/L)\s+in\s+([A-Za-z]+\s+\d{4}),\s*now\s+(\d+(?:\.\d+)?)\s*(ng/mL|[uµμ]g/L)',raw[m.end():],re.I)
                if linked and date_interval(linked.group(2)) and unit_kind(linked.group(1))==unit_kind(linked.group(4)):
                    x.update(measurement_date=date_interval(linked.group(2)),
                        temporal_role='explicit_historical_current_comparison',
                        current_comparator_value=float(linked.group(3)),
                        quote=raw[m.start():m.end()+linked.end()])
            # Do not assign an MRI examination date to the serum sample.
        items[name]=x
    return items


def relative_diff(a,b):
    try:
        a,b=Decimal(str(a)),Decimal(str(b));return abs(a-b)/max(abs(a),abs(b),Decimal('0.000000001'))
    except (InvalidOperation,ValueError):return None


def review(candidate,card,ehr):
    kind=candidate['type'];result={'status':'requires_source_alignment','selection_of_true_value':None,
        'input_edit':False,'observations':{},'rule_version':'p3_source_review_v1','evidence_basis':'input_only_no_GT'}
    if kind=='psa_multi':
        obs=psa_observations(card,ehr);result['observations']=obs
        wrong=[k for k,x in obs.items() if x['quantity']!='serum_psa_candidate']
        valid={k:x for k,x in obs.items() if k not in wrong}
        pairs=[(a,b) for a,b in itertools.combinations(valid,2) if relative_diff(valid[a]['numeric_value'],valid[b]['numeric_value'])>Decimal('.05')]
        result['wrong_quantity_sources']=wrong;result['remaining_screen_disagreements']=[]
        for a,b in pairs:
            x,y=valid[a],valid[b];d1,d2=x['measurement_date'],y['measurement_date']
            relation='measurement_date_unknown_or_partial_overlap'
            if d1 and d2:
                if d1['end']<d2['start'] or d2['end']<d1['start']:relation='disjoint_measurement_dates'
                elif d1['precision']==d2['precision']=='day' and d1['start']==d2['start']:relation='same_day'
            result['remaining_screen_disagreements'].append({'sources':[a,b],'date_relation':relation,
                'units_both_explicit':x['unit']==y['unit']=='ng_per_ml_equivalent',
                'censored_value_present':bool(x.get('censored') or y.get('censored'))})
        if wrong and not pairs:
            result['status']='old_screen_quantity_type_error'
        elif not pairs:
            result['status']='at_or_below_old_screen_tolerance_not_clinical_adjudication'
        elif all(any(valid[k].get('temporal_role')=='explicit_historical_current_comparison' and
                     Decimal(str(valid[other]['numeric_value']))==Decimal(str(valid[k]['current_comparator_value']))
                     for k,other in ((a,b),(b,a))) for a,b in pairs):
            result['status']='old_screen_explicit_history_current_conflation'
        elif all(x['date_relation']=='disjoint_measurement_dates' for x in result['remaining_screen_disagreements']):
            result['status']='different_measurement_times_keep_longitudinal_values'
        else:result['status']='value_disagreement_time_or_source_unresolved'
        result['reason']='5% is the old candidate-screen threshold, not a clinical equivalence criterion. MRI dates are not specimen dates; missing units remain unknown.'
    elif kind=='psad_self':
        x,y,z=card.get('psa'),card.get('vol'),card.get('psad')
        result['observations']={k:{'field_path':'prompt.'+k,'raw':card.get(k),'measurement_date':None,'units_not_inferred':True} for k in ('psa','vol','psad')}
        result['calculated_psa_over_volume']=x/y if all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (x,y)) and y else None
        result['status']='arithmetic_residual_measurement_alignment_or_rounding_unresolved'
        result['reason']='An arithmetic residual is reproducible, but source measurement timing/units and original reporting precision are not jointly certified. No numeric correction.'
    elif kind=='psad_vs_mri':
        result['observations']={'prompt.psad':card.get('psad'),'clinical.radiology_report':ehr.get('radiology_report')}
        result['status']='density_sources_disagree_alignment_unresolved'
        result['reason']='Keep both original statements; a document date alone does not establish identical underlying measurements.'
    elif kind=='bx_mention':
        texts=[('prompt.notes',str(card.get('notes') or '')),('clinical.radiology_report',str(ehr.get('radiology_report') or ''))]
        texts += [(f'clinical.previous_notes[{i}].text',str(n.get('text') or '')) for i,n in enumerate(ehr.get('previous_notes') or []) if isinstance(n,dict)]
        hits=[]
        for field,text in texts:
            for m in re.finditer(r'[^.!?\n]*(?:biopsy|biopsies)[^.!?\n]*[.!?]?',text,re.I):
                quote=m.group().strip()
                affirmative=bool(re.search(r'previously\s+documented\s+positive\s+biopsy|following\s+the\s+first\s+biopsy\s+results|biopsy.{0,35}(?:confirmed|yielded|returned)',quote,re.I))
                negated=bool(re.search(r'\b(?:no|never|not)\b',quote,re.I))
                hits.append({'field_path':field,'quote':quote,'affirmative_history_pattern':affirmative and not negated,'clinical_adjudication':False})
        result['observations']={'prompt.bx':card.get('bx'),'biopsy_mentions':hits}
        result['status']='prior_biopsy_history_vs_empty_card_requires_review' if any(h['affirmative_history_pattern'] for h in hits) else 'biopsy_mention_only_not_proof_of_prior_procedure'
        result['reason']='Mention, negation and future plans are not equivalent to a completed procedure. No inferred biopsy result.'
    else:result['reason']='Unknown candidate type; retained for manual review.'
    return result


def run(output):
    if output.exists():raise FileExistsError('Preserve previous review; choose a new output directory')
    manifest=read(POOL/'data_pool_manifest.json');groups=read(POOL/'patient_version_map.json')
    snapshots=read(POOL/'input_hashes.json')
    assert all(sha(Path(k))==h for k,h in snapshots.items()),'Source input changed before review'
    index={(c['source'],c['case_id']):c for c in manifest['cases'] if c['task']==1}
    group_index={(x['source'],x['task'],x['case_id']):x['risk_group_id'] for x in groups['mappings']}
    candidates=[json.loads(x) for x in CANDIDATES.read_text().splitlines() if x.strip()]
    ledger=[];cache={}
    for i,c in enumerate(candidates):
        key=(c['source'],c['case_id']);record=index[key];directory=Path(record['directory'])
        if key not in cache:
            p=directory/'structured-prompt.json';e=directory/'prostate-biopsy-decision-clinical-data.json'
            cache[key]=(read(p),read(e),{'prompt':{'path':str(p),'sha256':sha(p)},'clinical':{'path':str(e),'sha256':sha(e)}})
        card,ehr,source_files=cache[key]
        ledger.append({'candidate_index':i,'source':c['source'],'case_id':c['case_id'],'task':1,
            'risk_group_id':group_index[(c['source'],1,c['case_id'])],'usage':record['usage'],
            'old_candidate':c,'source_files':source_files,'review':review(c,card,ehr)})
    assert len(ledger)==len(candidates)
    assert all(sha(Path(k))==h for k,h in snapshots.items()),'Source input changed during review'
    counts={source:Counter(r['review']['status'] for r in ledger if r['source']==source) for source in sorted({r['source'] for r in ledger})}
    summary={'stage':'S1_P3_input_only_candidate_review','reviewed_candidates':len(ledger),'case_version_records':len(cache),
        'risk_groups_with_candidates':len({r['risk_group_id'] for r in ledger}),'by_source':counts,
        'original_inputs_hash_checked':len(snapshots),'original_inputs_unchanged':True,'GT_contents_opened':0,
        'new_model_calls':0,'input_values_changed':0,'production_changed':False,
        'scope':'Only frozen T1 candidate list; not a full T1/T2/T3 contradiction audit or clinician adjudication.',
        'next_step':'Retain unresolved evidence and version boundaries; define shared S2 tool access/cost contract before model experiments.'}
    output.mkdir(parents=True)
    (output/'conflict_review_ledger.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in ledger))
    dump(output/'summary.json',summary)
    dump(output/'source_hashes.json',{'candidates':{'path':str(CANDIDATES),'sha256':sha(CANDIDATES)},'pool_input_hashes':{'path':str(POOL/'input_hashes.json'),'sha256':sha(POOL/'input_hashes.json')},'tool_sha256':sha(Path(__file__))})
    lines=['# P3 来源／日期／量纲审查','','不读取GT，不选真值，不改输入。以下是旧候选的解释分类，不是已清洗病例或真实冲突率。','',
        '| 来源 | 状态 | 条目数 |','|---|---|---:|']
    for source,statuses in counts.items():
        for status,n in statuses.items():lines.append(f'| {source} | {status} | {n} |')
    lines+=['',json.dumps(summary,ensure_ascii=False,indent=2),'',
        '量纲误读只关闭该旧筛查标记，不证明整例无其他冲突。不同日期只证明不能当作同次测量直接冲突，不证明数值全部正确。保留所有原值、来源和未知信息。',
        '不自动删除病例或依GT选侧；没有生成已清洗训练／评测输入。后续两组须使用相同原始版本和同一冲突处理约定。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n');return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',required=True,type=Path);a=p.parse_args()
    print(json.dumps(run(a.output_dir),ensure_ascii=False,indent=2))
