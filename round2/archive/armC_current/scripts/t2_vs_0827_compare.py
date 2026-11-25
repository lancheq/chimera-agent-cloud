#!/usr/bin/env python3
"""T2 全量 v2 vs 0827 final 对比（同代码口径，均有 _enforce_treatment_floor）。

用法: .venv/bin/python scripts/t2_vs_0827_compare.py > scripts/t2_vs_0827_compare.txt
"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GT = f"{ROOT}/data/task2/agent_input"
OLD = f"{ROOT}/test/output.final_20260826/task2"
NEW = f"{ROOT}/test/output.t2_full_v2_20260830/task2"
LABELS = ["active_surveillance", "active_treatment", "continued_surveillance", "watchful_waiting"]

pids = json.load(open(f"{ROOT}/scripts/t2_full_72_gt_pids.json"))


def gt_dec(c):
    d = json.load(open(f"{GT}/{c}/prostate-treatment-decision.json"))
    return d.strip().strip('"') if isinstance(d, str) else d.get("decision", d)


def load(dirp, c):
    p = f"{dirp}/{c}/prediction.json"
    if not os.path.exists(p):
        return "MISSING"
    tr = json.load(open(p)).get("treatment_recommendation") or {}
    return tr.get("primary") if isinstance(tr, dict) else "MISSING"


def cm(yt, yp):
    conf = {l: {"tp": 0, "fp": 0, "fn": 0, "gt": 0, "pred": 0} for l in LABELS}
    for t, p in zip(yt, yp):
        if t in conf:
            conf[t]["gt"] += 1
        if p in conf:
            conf[p]["pred"] += 1
        if t in conf and p in conf:
            if t == p:
                conf[t]["tp"] += 1
            else:
                conf[t]["fn"] += 1
                conf[p]["fp"] += 1
    return conf


def report(name, yt, yp):
    n = len(yp)
    acc = sum(1 for t, p in zip(yt, yp) if t == p) / n
    conf = cm(yt, yp)
    f1s = {}
    for l in LABELS:
        c = conf[l]
        pr = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else 0
        rc = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else 0
        f1s[l] = 2 * pr * rc / (pr + rc) if (pr + rc) else 0
    macro = sum(f1s.values()) / len(LABELS)
    print(f"== {name} ==")
    print(f"  acc: {acc*100:.1f}%  ({sum(1 for t,p in zip(yt,yp) if t==p)}/{n})")
    for l in LABELS:
        c = conf[l]
        print(f"  {l}: tp={c['tp']} gt={c['gt']} pred={c['pred']} F1={f1s[l]:.3f}")
    print(f"  macroF1: {macro:.3f}")
    print(f"  混淆({name}): " + " ".join(f"{l}:{conf[l]['tp']}/{conf[l]['fp']}/{conf[l]['fn']}" for l in LABELS))
    return macro, acc, conf


yt = [gt_dec(c) for c in pids]
yo = [load(OLD, c) for c in pids]
yn = [load(NEW, c) for c in pids]

print(f"pids={len(pids)}  OLD MISSING={sum(1 for x in yo if x=='MISSING')}  NEW MISSING={sum(1 for x in yn if x=='MISSING')}")
print("口径注记: 同代码（含 _enforce_treatment_floor），双关闭（predictor/self_refine/atomic 均关），per-task prompt + P5 free_text 修复")
print()
m1, a1, _ = report("0827 final (output.final_20260826)", yt, yo)
print()
m2, a2, _ = report("0830 T2 full v2 (output.t2_full_v2_20260830)", yt, yn)
print()
print(f"Δacc: {a2-a1:+.1%}   ΔmacroF1: {m2-m1:+.3f}")
flips = [(c, o, n, gt_dec(c)) for c, o, n in zip(pids, yo, yn) if o != n]
fx = sum(1 for _, o, n, g in flips if n == g and o != g)
hx = sum(1 for _, o, n, g in flips if o == g and n != g)
print(f"翻转 {len(flips)} case: 修对 {fx} / 修错 {hx}")
for c, o, n, g in flips:
    print(f"  {c}: {o} -> {n}  GT={g}  [{'修对' if n==g else '修错'}]")
