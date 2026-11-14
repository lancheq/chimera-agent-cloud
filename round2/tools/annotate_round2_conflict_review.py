#!/usr/bin/env python3
"""Build quality tags/review queue from an existing input-only review ledger."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

CLOSED={'old_screen_quantity_type_error','old_screen_explicit_history_current_conflation',
        'at_or_below_old_screen_tolerance_not_clinical_adjudication'}


def main(pool,review,out):
    if out.exists():raise FileExistsError('Preserve existing annotations')
    read=lambda p:json.loads(p.read_text())
    rows=[json.loads(s) for s in (review/'conflict_review_ledger.jsonl').read_text().splitlines()]
    manifest=read(pool/'data_pool_manifest.json');groups=read(pool/'patient_version_map.json')
    index=defaultdict(list);queue=defaultdict(list);annotations=[]
    for r in rows:index[(r['source'],r['task'],r['case_id'])].append(r)
    for c in manifest['cases']:
        rr=index[(c['source'],c['task'],c['case_id'])]
        unresolved=[r for r in rr if r['review']['status'] not in CLOSED]
        status='outside_this_T1_review_scope' if c['task']!=1 else ('retain_unresolved_sources' if unresolved else ('old_flags_explained_not_clean_certification' if rr else 'not_flagged_by_old_screen_not_certified_clean'))
        annotations.append({'source':c['source'],'task':c['task'],'case_id':c['case_id'],'usage':c['usage'],
            'quality_status':status,'candidate_indices':[r['candidate_index'] for r in rr],
            'model_input_access_granted':False,'remove_from_evaluation':False,'true_value_selected':None})
        for r in unresolved:queue[r['risk_group_id']].append({'source':r['source'],'case_id':r['case_id'],
            'candidate_index':r['candidate_index'],'status':r['review']['status']})
    by_id={g['risk_group_id']:g for g in groups['groups']}
    q=[{'risk_group_id':k,'cross_task':by_id[k]['cross_tasks'],'n_group_records':by_id[k]['n_records'],
        'unresolved_candidates':v,'selection_basis':'source/cross-task/size; no GT or model accuracy'} for k,v in queue.items()]
    q.sort(key=lambda x:(not x['cross_task'],-x['n_group_records'],x['risk_group_id']))
    out.mkdir(parents=True)
    def save(name,x):(out/name).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
    save('data_quality_annotations.json',annotations);save('unresolved_review_queue.json',q)
    save('summary.json',{'old_candidates':len(rows),'explained_screen_flags':sum(r['review']['status'] in CLOSED for r in rows),
        'unresolved_candidate_entries':sum(r['review']['status'] not in CLOSED for r in rows),
        'unresolved_risk_groups':len(q),'records_annotated':len(annotations),'GT_read':False,
        'input_values_changed':0,'new_model_calls':0,'tool_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool',required=True,type=Path);p.add_argument('--review',required=True,type=Path);p.add_argument('--output-dir',required=True,type=Path)
    a=p.parse_args();main(a.pool,a.review,a.output_dir)
