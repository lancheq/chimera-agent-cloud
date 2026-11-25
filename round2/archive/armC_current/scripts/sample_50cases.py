#!/usr/bin/env python3
"""Stratified sample 50 GT cases: T1:20 + T2:15 + T3:15"""
import json, os, re, random
from collections import defaultdict

PROJECT_ROOT = '/root/shared-nvme/chimera-agent-baseline'
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')

GT_FILES = {
    1: {'decision': 'prostate-biopsy-decision.json',
        'clinical_data': 'prostate-biopsy-decision-clinical-data.json'},
    2: {'decision': 'prostate-treatment-decision.json',
        'clinical_data': 'prostate-treatment-decision-clinical-data.json'},
    3: {'decision': 'prostate-time-to-recurrence-or-last-follow-up.json',
        'clinical_data': 'prostate-time-to-recurrence-or-last-follow-up-clinical-data.json'},
}

random.seed(42)

def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_psa(cd):
    pt = cd.get('psa_trend', [])
    if pt:
        return float(pt[-1].get('val', 0))
    for lab in reversed(cd.get('laboratory_results', [])):
        if lab.get('name', '').upper() == 'PSA':
            try: return float(str(lab.get('val', '')).replace('ng/mL', '').strip())
            except: pass
    return None

def extract_pirads(cd):
    m = re.search(r'PI-RADS\s*(\d)', cd.get('radiology_report', ''), re.IGNORECASE)
    return int(m.group(1)) if m else None

def extract_psad(cd):
    rep = cd.get('radiology_report', '')
    m = re.search(r'PSA density\s*([\d.]+)', rep, re.IGNORECASE)
    if m:
        try: return float(m.group(1).rstrip('.'))
        except: pass
    m = re.search(r'PSAD\s*([\d.]+)', rep, re.IGNORECASE)
    if m:
        try: return float(m.group(1).rstrip('.'))
        except: pass
    return None

def has_prior_diag(cd):
    for note in (cd.get('previous_notes') or []):
        text = (note.get('text', '') if isinstance(note, dict) else note).lower()
        if any(kw in text for kw in ['positive biopsy','biopsy results','gleason','isup','prostate cancer','cspca','clinically significant','prior biopsy','previous biopsy','histological finding']):
            return True
    if cd.get('pathology_report') or cd.get('surgical_pathology_report'):
        return True
    return False

def is_grey_zone(psa, pirads, psad):
    if psa is not None and 3 <= psa <= 10:
        if pirads is not None and pirads == 3: return True
        if psad is not None and 0.10 <= psad <= 0.20: return True
    return False

def categorize(task, cid, dec, cd):
    psa, pirads, psad = extract_psa(cd), extract_pirads(cd), extract_psad(cd)
    diag = has_prior_diag(cd)
    gz = is_grey_zone(psa, pirads, psad)
    if task == 1:
        d = dec if isinstance(dec, str) else dec.get('biopsy_decision', 'no')
        cat = 'grey_zone' if gz else ('positive' if d == 'yes' else 'negative')
    elif task == 2:
        d = dec if isinstance(dec, str) else dec.get('treatment_recommendation', {}).get('primary', '')
        cat = 'grey_zone' if gz else ('positive' if d == 'active_treatment' else ('negative' if d == 'watchful_waiting' else 'intermediate'))
    else:
        ev = dec.get('event', 0)
        mo = dec.get('months_to_recurrence', 0)
        cat = 'grey_zone' if (ev == 1 and 12 <= mo <= 36) else ('positive' if ev == 1 else 'negative')
        d = {'event': ev, 'months': mo}
    sub = f'{cat}+diagnosed' if diag else cat
    return {'case_id': cid, 'task': task, 'category': cat, 'sub_category': sub, 'diagnosed': diag, 'decision': d, 'psa': psa, 'pirads': pirads, 'psad': psad}

def get_gt_cases(task):
    gd = os.path.join(DATA_DIR, f'task{task}', 'agent_input')
    if not os.path.isdir(gd): return []
    return [c for c in sorted(os.listdir(gd)) if os.path.exists(os.path.join(gd, c, GT_FILES[task]['decision']))]

def sample_task(info, n, tn):
    by_sub = defaultdict(list)
    for c in info: by_sub[c['sub_category']].append(c)
    if tn == 1:
        targets = {'positive':6,'negative':5,'grey_zone':3,'positive+diagnosed':3,'negative+diagnosed':2,'grey_zone+diagnosed':1}
    elif tn == 2:
        targets = {'positive+diagnosed':5,'intermediate+diagnosed':5,'negative+diagnosed':2,'grey_zone+diagnosed':3}
    else:
        targets = {'positive+diagnosed':6,'negative+diagnosed':6,'grey_zone+diagnosed':3}
    sampled, remaining = [], []
    for sc, tn2 in targets.items():
        pool = by_sub.get(sc, [])
        random.shuffle(pool)
        take = min(tn2, len(pool))
        sampled.extend(pool[:take])
        remaining.extend(pool[take:])
    if len(sampled) < n:
        random.shuffle(remaining)
        sampled.extend(remaining[:n - len(sampled)])
    elif len(sampled) > n:
        random.shuffle(sampled)
        sampled = sampled[:n]
    return sampled

def main():
    all_s = {}
    for task in [1, 2, 3]:
        gt = get_gt_cases(task)
        info = []
        for cid in gt:
            gd = os.path.join(DATA_DIR, f'task{task}', 'agent_input', cid)
            dec = load_json(os.path.join(gd, GT_FILES[task]['decision']))
            cdp = os.path.join(gd, GT_FILES[task]['clinical_data'])
            cd = load_json(cdp) if os.path.exists(cdp) else {}
            info.append(categorize(task, cid, dec, cd))
        n = {1: 20, 2: 15, 3: 15}[task]
        s = sample_task(info, n, task)
        all_s[f'task{task}'] = s
        cc = defaultdict(int)
        dc = {'diag': 0, 'nodiag': 0}
        for c in s:
            cc[c['category']] += 1
            dc['diag' if c['diagnosed'] else 'nodiag'] += 1
        print(f'\n=== Task {task}: {len(s)}/{n} ===')
        print(f'  Categories: {dict(cc)}')
        print(f'  Diagnosed: {dc}')
        for c in s:
            print(f"  {c['case_id']:40s} cat={c['category']:15s} diag={c['diagnosed']}  dec={str(c.get('decision',''))[:40]}")
    pids = []
    for t in [1,2,3]:
        for c in all_s[f'task{t}']: pids.append(c['case_id'])
    pids_str = '[' + ','.join(pids) + ']'
    print(f'\n=== Total: {len(pids)} cases ===')
    print(f'T1={len(all_s["task1"])}, T2={len(all_s["task2"])}, T3={len(all_s["task3"])}')
    print(f'\nHydra pids arg:')
    print(pids_str)
    out = {'description': 'Stratified 50-case sample', 'total': len(pids), 'pids': pids,
           'by_task': {f'task{t}': [c['case_id'] for c in all_s[f'task{t}']] for t in [1,2,3]},
           'details': {f'task{t}': all_s[f'task{t}'] for t in [1,2,3]}}
    with open(os.path.join(PROJECT_ROOT, 'scripts', 'lightweight_50cases.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\nSaved to scripts/lightweight_50cases.json')

if __name__ == '__main__':
    main()
