#!/usr/bin/env python3
import json, pathlib
ROOT = pathlib.Path('.')
BL = ROOT / 'test/output.final_20260826/task3.prefix_backup'
FX = ROOT / 'test/output.t3_full_20260828/task3'
GT = ROOT / 'data/task3/agent_input'
GF = 'prostate-time-to-recurrence-or-last-follow-up.json'
def lj(p):
    try: return json.loads(pathlib.Path(p).read_text('utf-8','ignore'))
    except: return None
def cl(g,p):
    if g==1 and p==1: return 'TP'
    if g==0 and p==0: return 'TN'
    if g==0 and p==1: return 'FP'
    return 'FN'
def ef(ft):
    ft=(ft or '').lower()
    return {'psa_threshold':'threshold' in ft or 'phoenix' in ft,'margin':'margin' in ft,'gleason':'gleason' in ft or 'grade group' in ft,'baseline':'baseline' in ft or 'pre-treatment' in ft or 'pretreatment' in ft,'rising':'rising' in ft or 'doubling' in ft or 'nadir' in ft}
gtd={};blp={};fxp={}
for d in sorted(GT.iterdir()):
    if not d.is_dir():continue
    g=lj(d/GF)
    if g:
        ev=g.get('event') if isinstance(g,dict) else g
        gtd[d.name]={'event':int(ev) if ev is not None else None,'months':g.get('months_to_recurrence') if isinstance(g,dict) else None}
for d in sorted(BL.iterdir()):
    if d.is_dir():
        p=lj(d/'prediction.json')
        if p:blp[d.name]=p
for d in sorted(FX.iterdir()):
    if d.is_dir():
        p=lj(d/'prediction.json')
        if p:fxp[d.name]=p
common=sorted(set(blp)&set(fxp)&set(gtd))
ffp={};ftp={};ftn={}
for pid in common:
    g=gtd[pid]['event']
    if g is None:continue
    be=int(blp[pid]['event']);fe=int(fxp[pid]['event'])
    bc=cl(g,be);fc=cl(g,fe)
    bft=blp[pid].get('free_text') or '';fft=fxp[pid].get('free_text') or ''
    e={'case_id':pid,'gt_event':g,'gt_months':gtd[pid]['months'],'baseline_pred_event':be,'baseline_pred_months':blp[pid].get('months_to_recurrence'),'fixed_pred_event':fe,'fixed_pred_months':fxp[pid].get('months_to_recurrence'),'psa_ng_ml':blp[pid].get('patient',{}).get('psa_ng_ml'),'baseline_cls':bc,'fixed_cls':fc,'baseline_features':ef(bft),'fixed_features':ef(fft),'baseline_free_text_excerpt':bft[:300],'fixed_free_text_excerpt':fft[:300]}
    if bc=='FP':e['flip_status']='flipped_to_TN' if fc=='TN' else ('remained_FP' if fc=='FP' else f'became_{fc}');ffp[pid]=e
    elif bc=='TP':e['retention_status']='retained_TP' if fc=='TP' else f'degraded_to_{fc}';ftp[pid]=e
    elif bc=='TN':e['stability_status']='stable_TN' if fc=='TN' else f'flipped_to_{fc}';ftn[pid]=e
def fs(cs,key='baseline_features'):
    n=len(cs);c={k:0 for k in['psa_threshold','margin','gleason','baseline','rising']}
    for x in cs.values():
        for k in c:
            if x[key][k]:c[k]+=1
    return {k:{'count':v,'rate':round(v/max(n,1),4)} for k,v in c.items()}
r={'analysis':'Stage 0B former-FP flip matrix attribution','baseline_dir':'test/output.final_20260826/task3.prefix_backup','fixed_dir':'test/output.t3_full_20260828/task3','total_common_cases':len(common),'baseline_confusion':{'TP':len(ftp),'FP':len(ffp),'TN':len(ftn),'FN':0},'fixed_confusion':{'TP':sum(1 for v in ftp.values() if v['fixed_cls']=='TP'),'FP':sum(1 for v in ffp.values() if v['fixed_cls']=='FP')+sum(1 for v in ftn.values() if v['fixed_cls']=='FP'),'TN':sum(1 for v in ffp.values() if v['fixed_cls']=='TN')+sum(1 for v in ftn.values() if v['fixed_cls']=='TN'),'FN':sum(1 for v in ftp.values() if v['fixed_cls']=='FN')},'summary':{'former_fp':{'total':len(ffp),'flipped_to_TN':sum(1 for v in ffp.values() if v['flip_status']=='flipped_to_TN'),'remained_FP':sum(1 for v in ffp.values() if v['flip_status']=='remained_FP')},'former_tp':{'total':len(ftp),'retained_TP':sum(1 for v in ftp.values() if v['retention_status']=='retained_TP'),'degraded_to_FN':sum(1 for v in ftp.values() if v['retention_status']=='degraded_to_FN')},'former_tn':{'total':len(ftn),'stable_TN':sum(1 for v in ftn.values() if v['stability_status']=='stable_TN'),'flipped_to_FP':sum(1 for v in ftn.values() if v['stability_status']=='flipped_to_FP')}},'former_fp_flip_matrix':{pid:v for pid,v in sorted(ffp.items())},'former_tp_retention':{pid:v for pid,v in sorted(ftp.items())},'former_tn_stability':{pid:v for pid,v in sorted(ftn.items())},'free_text_features':{'former_fp_baseline':fs(ffp),'former_fp_fixed':fs(ffp,'fixed_features'),'former_tp_baseline':fs(ftp),'former_tp_fixed':fs(ftp,'fixed_features'),'former_tn_baseline':fs(ftn),'former_tn_fixed':fs(ftn,'fixed_features')}}
out=pathlib.Path('scripts/overnight_stage0_former_fp_matrix.json')
out.write_text(json.dumps(r,indent=2,ensure_ascii=False),'utf-8')
print(f'Written {out.stat().st_size} bytes')
print(f'Baseline: TP={len(ftp)}, FP={len(ffp)}, TN={len(ftn)}')
print(f'Fixed: TP={sum(1 for v in ftp.values() if v["fixed_cls"]=="TP")}, FP={sum(1 for v in ffp.values() if v["fixed_cls"]=="FP")+sum(1 for v in ftn.values() if v["fixed_cls"]=="FP")}, TN={sum(1 for v in ffp.values() if v["fixed_cls"]=="TN")+sum(1 for v in ftn.values() if v["fixed_cls"]=="TN")}, FN={sum(1 for v in ftp.values() if v["fixed_cls"]=="FN")}')
print(f'Former-FP flip: {sum(1 for v in ffp.values() if v["flip_status"]=="flipped_to_TN")} to TN, {sum(1 for v in ffp.values() if v["flip_status"]=="remained_FP")} still FP')
