#!/usr/bin/env python3
"""S2 双臂评估 + 对比（零推理）"""
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from chimera_agent_baseline.agent.self_refine import _parse_critique_json  # noqa: E402

A = PROJECT_ROOT / 'test' / 'output.s2_A'
B = PROJECT_ROOT / 'test' / 'output.s2_B'
GT_FILES = {1: 'prostate-biopsy-decision.json', 2: 'prostate-treatment-decision.json', 3: 'prostate-time-to-recurrence-or-last-follow-up.json'}
DATA = PROJECT_ROOT / 'data'

def load(p): return json.load(open(p))

def pred_decision(task, pred):
    if task == 1: return pred.get('biopsy_decision')
    if task == 2: return pred.get('treatment_recommendation', {}).get('primary')
    return (pred.get('event'), pred.get('months_to_recurrence'))

def gt_decision(task, case):
    d = load(DATA / f'task{task}' / 'agent_input' / case / GT_FILES[task])
    if task in (1, 2): return d
    return (d.get('event'), d.get('months_to_recurrence'))

def arm_stats(root):
    stats = defaultdict(lambda: [0, 0]); dec = {}
    for task in (1, 2, 3):
        td = root / f'task{task}'
        if not td.is_dir(): continue
        for cd in sorted(p for p in td.iterdir() if p.is_dir()):
            pf = cd / 'prediction.json'
            if not pf.exists(): continue
            pred = load(pf)
            try: g = gt_decision(task, cd.name)
            except FileNotFoundError: continue
            p = pred_decision(task, pred)
            stats[task][1] += 1
            if p == g: stats[task][0] += 1
            dec[f'{task}:{cd.name}'] = p
    return stats, dec

def main():
    a_stats, a_dec = arm_stats(A); b_stats, b_dec = arm_stats(B)
    print('=== S2 双臂评估（30 case，A=self_refine off / B=on）===')
    print('| task | A acc | B acc |')
    for task in (1, 2, 3):
        at, an = a_stats[task]; bt, bn = b_stats[task]
        print(f'| task{task} | {at}/{an} | {bt}/{bn} |')
    a_tot = sum(v[1] for v in a_stats.values()); b_tot = sum(v[1] for v in b_stats.values())
    a_c = sum(v[0] for v in a_stats.values()); b_c = sum(v[0] for v in b_stats.values())
    print(f'| 合计 | {a_c}/{a_tot} ({a_c/a_tot:.1%}) | {b_c}/{b_tot} ({b_c/b_tot:.1%}) |')
    common = set(a_dec) & set(b_dec)
    flips = [k for k in common if a_dec[k] != b_dec[k]]
    print(f'\n决策变更率: {len(flips)}/{len(common)} = {len(flips)/len(common):.1%}')
    print('  变更 case:', sorted(flips))
    crit = sorted(B.glob('trace/*/critique_iter1.json'))
    ok = trig = nw = 0
    for f in crit:
        raw = load(f).get('critique_raw_full', '')
        clean, issues = _parse_critique_json(raw)
        if clean and clean != raw: ok += 1
        if issues: trig += 1
        if any(m in raw.lower()[:200] for m in
               ("here's a thinking process", 'here is a thinking process', 'analyze user input',
                "let's analyze the provided", 'the user wants me to audit', 'i need to check for:',
                "i'll analyze", 'let me review')): nw += 1
    n = len(crit)
    print(f'\nB 臂 critique: {n} 个，尾部 JSON 解析成功 {ok}/{n} ({ok/n:.1%})')
    print(f'  thinking 污染: {nw}/{n}（目标 <20%）')
    print(f'  refine 触发: {trig}/{n} ({trig/n:.1%})')
    a_acc = a_c/a_tot if a_tot else 0; b_acc = b_c/b_tot if b_tot else 0; d = b_acc - a_acc
    print(f'\n=== 判定 ===\nA acc={a_acc:.3f} B acc={b_acc:.3f} Δ={d:+.3f}')
    if d > 0.02: print('B 正贡献 → 建议启用 refine，待 L3 全量验证')
    elif d < -0.02: print('B 负贡献 → 确认关闭 refine')
    else: print('B 持平 → 参考 critique 解析/触发率；无显著收益则维持关闭')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

