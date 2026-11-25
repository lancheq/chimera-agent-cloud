#!/usr/bin/env python3
"""Step 5 L1 离线审计：T3 零推理策略模拟
A. FN 深挖（former-TP 降级）  B. margin-FP vs TP 可分性
C. 基率匹配重切模拟          D. predictor 融合（LOOCV 诚实口径 = 项目 HistGB 回退）
 LLM 推理，只读已落盘数据 + sklearn 离线计算。
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import numpy as np
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_predictor as tp

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "test/output.t3_full_20260828/task3"
GT_DIR = ROOT / "data/task3/agent_input"
GF = "prostate-time-to-recurrence-or-last-follow-up.json"

def load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None

def ev_kw(ft):
    ft = (ft or "").lower()
    out = []
    if "margin" in ft: out.append("margin")
    if "gleason" in ft or "grade group" in ft: out.append("gleason")
    if "rising" in ft or "doubling" in ft or "nadir" in ft: out.append("rising")
    if "baseline" in ft or "pre-treatment" in ft or "pretreatment" in ft: out.append("baseline")
    return out

cases = []
for cdir in sorted(OUT.iterdir()):
    if not cdir.is_dir():
        continue
    pid = cdir.name
    pred = load(cdir / "prediction.json")
    if pred is None or (cdir / ".failed").exists():
        continue
    g = load(GT_DIR / pid / GF)
    gt_ev = g.get("event") if isinstance(g, dict) else g
    gt_mon = g.get("months_to_recurrence") if isinstance(g, dict) else None
    cases.append(dict(pid=pid, pred=pred, gt_ev=int(gt_ev), gt_mon=gt_mon))
cases.sort(key=lambda c: c["pid"])
n = len(cases)
print(f"loaded {n} cases")

def cmat_f(cs, f):
    tp_=fp=tn=fn=0
    for c in cs:
        p=f(c); g=c["gt_ev"]
        if g==1 and p==1: tp_+=1
        elif g==0 and p==0: tn+=1
        elif g==0 and p==1: fp+=1
        else: fn+=1
    return dict(TP=tp_,FP=fp,TN=tn,FN=fn,acc=(tp_+tn)/len(cs),fp_rate=fp/max(fp+tn,1))

def dist(cs):
    cnt = Counter()
    for c in cs:
        for k in ev_kw(c["pred"].get("free_text") or ""):
            cnt[k]+=1
    return dict(cnt)

def psa_list(cs):
    return [c["pred"].get("patient",{}).get("psa_ng_ml") for c in cs
            if isinstance(c["pred"].get("patient",{}).get("psa_ng_ml"),(int,float))]

# ==================================================================
# A. FN 深挖
# ==================================================================
print("\n" + "="*70)
print("A. FN 深挖（former-TP 降级 case）")
print("="*70)
fn_cases = [c for c in cases if c["gt_ev"]==1 and c["pred"].get("event")==0]
tp_cases = [c for c in cases if c["gt_ev"]==1 and c["pred"].get("event")==1]
print(f"FN 数={len(fn_cases)}  TP 数={len(tp_cases)}")
print("\n--- 3 个 FN 明细 ---")
for c in fn_cases:
    p = c["pred"]
    ft = p.get("free_text") or ""
    print(f"\n[{c['pid']}] pred_months={p.get('months_to_recurrence')}  GT_months={c['gt_mon']}  "
          f"psa={p.get('patient',{}).get('psa_ng_ml')}  证据={ev_kw(ft)}")
    print(f"  free_text: {ft}")
print("\n--- 证据分布对比 ---")
print(f"TP 证据分布: {dist(tp_cases)}  (n={len(tp_cases)})")
print(f"FN 证据分布: {dist(fn_cases)}  (n={len(fn_cases)})")
tp_psa=psa_list(tp_cases); fn_psa=psa_list(fn_cases)
if tp_psa: print(f"TP PSA: 中位={sorted(tp_psa)[len(tp_psa)//2]} 范围=[{min(tp_psa)},{max(tp_psa)}]")
if fn_psa: print(f"FN PSA: 中位={sorted(fn_psa)[len(fn_psa)//2]} 范围=[{min(fn_psa)},{max(fn_psa)}]")

# ==================================================================
# B. margin-FP vs TP 可分性
# ==================================================================
print("\n" + "="*70)
print("B. margin-FP vs 保持TP 特征可分性（规则校准 v2 可行性）")
print("="*70)
fp_cases = [c for c in cases if c["gt_ev"]==0 and c["pred"].get("event")==1]
margin_fp = [c for c in fp_cases if "margin" in ev_kw(c["pred"].get("free_text") or "")]
print(f"FP 总数={len(fp_cases)}  margin-FP={len(margin_fp)}  TP={len(tp_cases)}")
print("\n引用率对比（margin-FP vs TP）:")
for grp_name, grp in [("margin-FP", margin_fp), ("TP", tp_cases)]:
    d = dist(grp)
    print(f"  {grp_name:<10} n={len(grp):<3} "
          f"margin={d.get('margin',0)/max(len(grp),1):.0%} "
          f"gleason={d.get('gleason',0)/max(len(grp),1):.0%} "
          f"rising={d.get('rising',0)/max(len(grp),1):.0%} "
          f"baseline={d.get('baseline',0)/max(len(grp),1):.0%}")
fp_psa=psa_list(margin_fp); tp_psa_b=psa_list(tp_cases)
if fp_psa: print(f"  margin-FP PSA: 中位={sorted(fp_psa)[len(fp_psa)//2]} 范围=[{min(fp_psa)},{max(fp_psa)}]")
if tp_psa_b: print(f"  TP      PSA: 中位={sorted(tp_psa_b)[len(tp_psa_b)//2]} 范围=[{min(tp_psa_b)},{max(tp_psa_b)}]")
single_margin_fp = [c for c in margin_fp if set(ev_kw(c["pred"].get("free_text") or ""))=={"margin"}]
single_margin_tp = [c for c in tp_cases if set(ev_kw(c["pred"].get("free_text") or ""))=={"margin"}]
print(f"\n规则v2拟议(仅margin单一证据→降级): 命中 margin-FP={len(single_margin_fp)}  误伤 TP={len(single_margin_tp)}")

# ==================================================================
# C. 基率匹配重切模拟（风险分 = -months）
# ==================================================================
print("\n" + "="*70)
print("C. 基率匹配重切模拟（风险分=-months，top-k 判 event=1）")
print("="*70)
gt_ev_cnt = sum(1 for c in cases if c["gt_ev"]==1)
print(f"GT 基率: event=1 共 {gt_ev_cnt}/{n} = {gt_ev_cnt/n:.1%}")
scored = sorted(cases, key=lambda c: (c["pred"].get("months_to_recurrence") or 999, c["pid"]))
print(f"\n{'k':<6}{'TP':<5}{'FP':<5}{'TN':<5}{'FN':<5}{'EventAcc':<10}{'FP率':<8}")
for k in [16,19,22,25]:
    for i, c in enumerate(scored):
        c["recut_ev"] = 1 if i < k else 0
    m = cmat_f(scored, lambda c: c["recut_ev"])
    tag = "  <- 基率(主结果)" if k==gt_ev_cnt else ""
    print(f"{k:<6}{m['TP']:<5}{m['FP']:<5}{m['TN']:<5}{m['FN']:<5}{m['acc']*100:<10.1f}{m['fp_rate']*100:<8.1f}{tag}")

# ==================================================================
# D. predictor 融合（LOOCV = HistGB 回退，同项目口径；附全量自预测上界）
# ==================================================================
print("\n" + "="*70)
print("D. predictor 融合模拟（LOOCV 诚实口径）")
print("="*70)
rows, labels, vecs, times, case_ids, is_gt = tp.load_task_data(3)
pid2idx = {pid: i for i, pid in enumerate(case_ids)}
events = np.array(labels); times_all = np.array(times)
n_all = len(rows)
is_gt_arr = np.array(is_gt)
gt_idx = np.where(is_gt_arr)[0]

for r in rows:
    r["log_psa"] = np.log1p(r.get("psa", 0) or 0)
    r["psa_age_ratio"] = (r.get("psa", 0) or 0) / max(r.get("age", 1) or 1, 1)
num_names = tp.NUMERIC_FEATURES[3] + tp.CLINICAL_FEATURES[3] + ["dre_tstage", "active_treatment", "log_psa", "psa_age_ratio"]
raw_num = np.array([[r.get(x, np.nan) for x in num_names] for r in rows], dtype=float)
has_vecs = any(v is not None for v in vecs)

from sklearn.ensemble import HistGradientBoostingClassifier

def hgb_fit_predict(train_idx, test_idx):
    X_tr, X_te = tp._preprocess_fold_t3(raw_num, train_idx, test_idx, vecs, has_vecs)
    clf = HistGradientBoostingClassifier(max_depth=2, learning_rate=0.05, max_iter=150, random_state=42)
    clf.fit(X_tr, events[train_idx])
    proba = clf.predict_proba(X_te)[:, 1]
    thr = float(np.median(clf.predict_proba(X_tr)[:, 1]))
    return proba, (proba > thr).astype(int)

# LOOCV 诚实口径（每 case 留一，训练 fold 外拟合）
risk_loocv = np.zeros(n_all); ev_loocv = np.zeros(n_all, dtype=int)
for ti in gt_idx:
    mask = np.ones(n_all, dtype=bool); mask[ti] = False
    tr_idx = np.where(mask)[0]
    pr, ev = hgb_fit_predict(tr_idx, np.array([ti]))
    risk_loocv[ti] = pr[0]; ev_loocv[ti] = ev[0]

# 全量自预测（训练集上界，有泄漏）
pr_full, ev_full = hgb_fit_predict(np.arange(n_all), np.arange(n_all))

for c in cases:
    i = pid2idx[c["pid"]]
    c["pr_loocv_ev"] = int(ev_loocv[i])
    c["pr_loocv_risk"] = float(risk_loocv[i])
    c["pr_full_ev"] = int(ev_full[i])

ci_loocv = tp._c_index(times_all[gt_idx], risk_loocv[gt_idx], events[gt_idx])
ci_full = tp._c_index(times_all, pr_full, events)
print(f"predictor C-index: LOOCV={ci_loocv:.3f}  全量自预测(上界)={ci_full:.3f}")
print(f"predictor event acc: LOOCV={np.mean(ev_loocv[gt_idx]==events[gt_idx])*100:.1f}%  全量自预测={np.mean(ev_full==events)*100:.1f}%")

def ev_llm(c): return c["pred"].get("event")
def ev_and(c): return 1 if ev_llm(c)==1 and c["pr_loocv_ev"]==1 else 0
def ev_or(c):  return 1 if ev_llm(c)==1 or c["pr_loocv_ev"]==1 else 0
def ev_pred(c): return c["pr_loocv_ev"]
def ev_pred_full(c): return c["pr_full_ev"]

print(f"\n{'策略':<24}{'TP':<5}{'FP':<5}{'TN':<5}{'FN':<5}{'EventAcc':<10}{'FP率':<8}")
rows_tbl = [
    ("纯LLM(现状)", ev_llm),
    ("LLM∧predictor(LOOCV)", ev_and),
    ("LLM∨predictor(LOOCV)", ev_or),
    ("predictor直替(LOOCV)", ev_pred),
    ("predictor直替(全量自预测,泄漏上界)", ev_pred_full),
]
for name, f in rows_tbl:
    m = cmat_f(cases, f)
    print(f"{name:<24}{m['TP']:<5}{m['FP']:<5}{m['TN']:<5}{m['FN']:<5}{m['acc']*100:<10.1f}{m['fp_rate']*100:<8.1f}")

print("\n=== 完成 ===")
