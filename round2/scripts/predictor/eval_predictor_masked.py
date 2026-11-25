#!/usr/bin/env python3
"""Predictor 合法证据回放测试（2026-11-24，v2）。

三个问题分开回答：

1. **越权**：账本未揭示的段落换成垃圾文本后，特征 row 与预测必须一字不变。
2. **缺失语义**：现状把"没揭示"填成 0.0，而 0.0 在模型眼里等于"明确阴性"。
   NaN 变体改走训练时那条 median-imputer 缺失通道。
3. **性能**：同一批 236 例，全量 / 账本零填充 / 账本 NaN / 去影像向量 四档对比，
   再和纯 LLM C 臂的实际输出并排看。

段落归属用"依赖判定"而不是"键归属"：单段落 dict 喂 clinical_features 时，
未涉及的段落会返回默认值，按 key 归属会把每个特征都算成属于所有段落。
这里改成  emptying 单个段落看哪个特征的值变了 —— 变了才算该段落产出。

数据全部只读；产物写到 output/predictor_replay_20261124/。
"""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

import numpy as np

ARM = Path("/home/hedongze/CHIMERA-armC")
sys.path.insert(0, str(ARM / "src"))

from chimera_agent_baseline import predictor as P  # noqa: E402
from chimera_agent_baseline.predictor import (  # noqa: E402
    CLINICAL_FEATURES, CLINICAL_FILE, Predictor, build_case_features, clinical_features,
)

DATA = Path("/home/hedongze/CHIMERA-agent/data")
MODEL_DIR = Path("/home/hedongze/CHIMERA-agent/model/predictor")
WORK = ARM / "output" / "bench_work"
OUT = Path("/home/hedongze/CHIMERA-agent/output/predictor_replay_20261124")

CASELIST = {1: "t1_full_91_gt_pids.json", 2: "t2_full_72_gt_pids.json", 3: "t3_full_73_gt_pids.json"}
DECISION = {1: "prostate-biopsy-decision.json", 2: "prostate-treatment-decision.json",
            3: "prostate-time-to-recurrence-or-last-follow-up.json"}
ALL_SECTIONS = ("family_history", "psa_trend", "laboratory_results",
                "radiology_report", "pathology_report", "surgical_pathology_report")
SENTINEL_TEXT = "REDACTED: no lesion identified; no lymph nodes were removed; Charlson Comorbidity Index 0"


def case_dir(task, case):
    return DATA / f"task{task}" / "agent_input" / case


def arm_output_dir(task, case):
    hits = list(WORK.glob(f"task{task}/{case}/private/*/temporary_evidence/output/task{task}/{case}"))
    return hits[0] if hits else None


def visible_sections(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT key, source FROM cache WHERE key LIKE 'clinical:%'").fetchall()
        spent, attempts = con.execute("SELECT spent, attempts FROM meta").fetchone()
    finally:
        con.close()
    return ({k.split(":", 1)[1] for k, _ in rows},
            {"spent_units": spent, "admitted_attempts": attempts,
             "sources": dict(Counter(s for _, s in rows))})


# ---------------------------------------------------------------- 标签与预测归一

def truth_of(task, case):
    raw = json.loads((case_dir(task, case) / DECISION[task]).read_text())
    if task == 1:
        return "yes" if raw in (True, "yes") else "no" if raw in (False, "no") else str(raw).strip().lower()
    if task == 2:
        return str(raw).strip().lower()
    if isinstance(raw, dict):
        return (int(raw.get("event", 0)), float(raw.get("months_to_recurrence") or 0.0))
    return None


def normalise_pred(task, dec):
    if task == 1:
        return "yes" if dec.get("decision") in (True, "yes", 1) else "no"
    if task == 2:
        return str(dec.get("decision", "")).strip().lower()
    return (int(dec.get("event", 0)), float(dec.get("months_to_recurrence") or 0.0))


def llm_arm_pred(task, case):
    """C 臂 prediction.json 的提交口径：T1 biopsy_decision / T2 treatment_recommendation.primary / T3 event+months。"""
    d = arm_output_dir(task, case)
    if d is None or not (d / "prediction.json").is_file():
        return None
    raw = json.loads((d / "prediction.json").read_text())
    if task == 1:
        v = raw.get("biopsy_decision")
        return None if v is None else ("yes" if v in (True, "yes") else "no")
    if task == 2:
        v = (raw.get("treatment_recommendation") or {}).get("primary") or raw.get("decision")
        return None if v is None else str(v).strip().lower()
    if "event" in raw:
        return (int(raw.get("event", 0)), float(raw.get("months_to_recurrence") or 0.0))
    return None


# ---------------------------------------------------------------- 特征装配

def empty_like(v):
    return type(v)()


def same_value(a, b):
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 1e-9
    return a == b


def dependencies(task, cd):
    """特征 -> 产出它的段落集合（用 emptying 判定，不用 key 归属）。"""
    dep = defaultdict(set)
    for sec in ALL_SECTIONS:
        if sec not in cd or not isinstance(cd[sec], (str, list, dict)):
            continue
        with_sec = clinical_features({sec: cd[sec]}, task)
        without = clinical_features({sec: empty_like(cd[sec])}, task)
        for f in set(with_sec) | set(without):
            if not same_value(with_sec.get(f), without.get(f)):
                dep[f].add(sec)
    return dict(dep)


def assemble_row(task, cdir, visible, policy, dep):
    """按账本可见集合装配特征 row。

    policy='zero'：未揭示 -> 0.0（= 现状 build_case_features(allowed_sections) 行为）
    policy='nan' ：来源段落未全部揭示 -> NaN（走训练时的 median imputer）
    """
    base, vec = build_case_features(cdir, task, allowed_sections=None)
    cd_file = cdir / CLINICAL_FILE[task]
    cd = json.loads(cd_file.read_text()) if cd_file.is_file() else {}
    row = dict(base)
    clin_vis = clinical_features({k: v for k, v in cd.items() if k in visible}, task)
    for f in CLINICAL_FEATURES[task]:
        if policy == "zero":
            row[f] = clin_vis.get(f, 0.0)
        else:
            src = dep.get(f, set())
            row[f] = clin_vis.get(f, 0.0) if src <= visible else float("nan")

    if task == 2:
        gp, gs = row.get("bx_gl_prim", np.nan), row.get("bx_gl_sec", np.nan)
        if np.isfinite(gp) and np.isfinite(gs):
            row["d_gleason_sum"] = gp + gs
            row["d_grade_group"] = float(int(np.clip(gp - 3, 0, 2) + np.clip(gs - 3, 0, 2)))
            row["d_gleason_primary4"] = 1.0 if gp >= 4 else 0.0
    if task == 3:
        psa = row.get("psa", 0)
        psa = 0 if psa is None else psa
        age = max(row.get("age", 1) or 1, 1)
        psad, pirads = row.get("psa_density", np.nan), row.get("pirads_rad", np.nan)
        sg_p, sg_s = row.get("sg_gleason_primary", np.nan), row.get("sg_gleason_secondary", np.nan)
        bx_p, bx_s = row.get("bx_gleason_primary", np.nan), row.get("bx_gleason_secondary", np.nan)
        row["log_psa"] = float(np.log1p(psa))
        row["psa_age_ratio"] = float(psa / age)
        row["pirads_x_psad"] = pirads * psad if np.isfinite(pirads) and np.isfinite(psad) else np.nan
        row["gleason_sum_sg"] = sg_p + sg_s if np.isfinite(sg_p) and np.isfinite(sg_s) else np.nan
        row["gleason_diff"] = ((sg_p + sg_s) - (bx_p + bx_s)
                               if all(np.isfinite([sg_p, sg_s, bx_p, bx_s])) else np.nan)
    return row, vec


@contextmanager
def forced_features(row, vec):
    original = P.build_case_features
    P.build_case_features = lambda *a, **k: (dict(row), None if vec is None else np.asarray(vec))
    try:
        yield
    finally:
        P.build_case_features = original


def rows_equal(a, b):
    for k in set(a) | set(b):
        if not same_value(a.get(k), b.get(k)):
            return False
    return True


def row_diff(a, b):
    return [k for k in set(a) | set(b) if not same_value(a.get(k), b.get(k))]


def redact_copy(cdir, task, visible):
    tmp = Path(tempfile.mkdtemp(prefix="redact_")) / cdir.name   # 同名，保证 kNN 自排除仍生效
    shutil.copytree(cdir, tmp)
    target = tmp / CLINICAL_FILE[task]
    cd = json.loads(target.read_text())
    for sec, payload in list(cd.items()):
        if sec in visible:
            continue
        if isinstance(payload, str):
            cd[sec] = SENTINEL_TEXT
        elif isinstance(payload, list):
            cd[sec] = [{"component": "REDACTED", "result": "999"}]
        elif isinstance(payload, dict):
            cd[sec] = {"note": SENTINEL_TEXT}
    target.write_text(json.dumps(cd, ensure_ascii=False))
    return tmp


# ---------------------------------------------------------------- 指标

def harrell_c(times, events, scores):
    conc = disc = 0
    for i in range(len(times)):
        for j in range(i + 1, len(times)):
            if events[i] == 1 and events[j] == 1:
                if times[i] == times[j]:
                    continue
                hi = times[i] < times[j]
                if scores[i] > scores[j]:
                    conc += 1 if hi else 0
                elif scores[i] < scores[j]:
                    disc += 1 if hi else 0
    return conc / (conc + disc) if conc + disc else float("nan")


def summarise(task, pairs):
    n = len(pairs)
    if not n:
        return {"cases": 0}
    if task == 3:
        times = [t[1] for _, t, _, _ in pairs]
        events = [t[0] for _, t, _, _ in pairs]
        out = {"cases": n,
               "event_acc": round(sum(1 for _, t, p, _ in pairs if p[0] == t[0]) / n, 4),
               "mae_months": round(float(np.mean([abs(p[1] - t[1]) for _, t, p, _ in pairs])), 2),
               "pred_events": dict(Counter(int(p[0]) for _, _, p, _ in pairs)),
               "truth_events": dict(Counter(int(t[0]) for _, t, _, _ in pairs))}
        risks = [float((r or {}).get("p_event") or np.nan) for _, _, _, r in pairs]
        if np.isfinite(risks).sum() >= 3:
            out["c_index"] = round(harrell_c(times, events, risks), 4)
        return out
    return {"cases": n,
            "accuracy": round(sum(1 for _, t, p, _ in pairs if p == t) / n, 4),
            "pred_dist": dict(Counter(str(p) for _, _, p, _ in pairs)),
            "truth_dist": dict(Counter(str(t) for _, t, _, _ in pairs))}


def agreement(task, a, b):
    return {"n": len(a), "identical": sum(1 for x, y in zip(a, b) if x[2] == y[2]),
            "flipped": sum(1 for x, y in zip(a, b) if x[2] != y[2]),
            "right_only_in_first": sum(1 for x, y in zip(a, b) if x[2] == x[1] and y[2] != y[1]),
            "right_only_in_second": sum(1 for x, y in zip(a, b) if y[2] == y[1] and x[2] != x[1])}


# ---------------------------------------------------------------- 主流程

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    predictor = Predictor(MODEL_DIR)
    results = {"predictor_available_tasks": predictor.available_tasks, "tasks": {}}
    print("可用任务:", predictor.available_tasks)

    for task in (1, 2, 3):
        cases = json.loads((ARM / "scripts" / CASELIST[task]).read_text())
        arms = defaultdict(list)
        per_case = []
        fab = defaultdict(Counter)
        dep_stats = defaultdict(set)
        redact_breaks = []
        row_deltas = []

        for case in cases:
            cdir = case_dir(task, case)
            truth = truth_of(task, case)
            db = arm_output_dir(task, case) / "information_access.sqlite"
            vis, ledger_meta = visible_sections(db)
            cd = json.loads((cdir / CLINICAL_FILE[task]).read_text())
            dep = dependencies(task, cd)
            for f, src in dep.items():
                dep_stats[f] |= src

            row_full, vec_full = build_case_features(cdir, task, allowed_sections=None)
            row_zero, _ = assemble_row(task, cdir, vis, "zero", dep)
            row_nan, _ = assemble_row(task, cdir, vis, "nan", dep)

            # 装配器自校 1：zero 策略必须逐字等于生产代码 allowed_sections 的输出
            prod_zero, _ = build_case_features(cdir, task, allowed_sections=vis)
            assert rows_equal(row_zero, prod_zero), f"zero 装配与生产不一致: task{task}/{case}"
            # 装配器自校 2：全部段落可见时，nan 策略必须退化为全量
            if set(s for s in ALL_SECTIONS if s in cd) <= vis:
                assert rows_equal(row_nan, row_full), f"nan 全可见未退化为全量: task{task}/{case}"

            entry = {"case": case, "truth": truth, "visible": sorted(vis),
                     "spent_units": ledger_meta["spent_units"],
                     "attempts": ledger_meta["admitted_attempts"],
                     "n_features_changed_by_mask": len(row_diff(row_zero, row_full))}
            if row_diff(row_zero, row_full):
                row_deltas.append(entry["n_features_changed_by_mask"])

            for name, row, vec in (("full", row_full, vec_full),
                                   ("ledger_zero", row_zero, vec_full),
                                   ("ledger_nan", row_nan, vec_full)):
                with forced_features(row, vec):
                    dec = predictor.predict(cdir, task)
                pred = normalise_pred(task, dec)
                arms[name].append((case, truth, pred, dec))
                entry[name] = pred

            if task == 3:
                for name, row in (("ledger_zero_novec", row_zero), ("ledger_nan_novec", row_nan)):
                    with forced_features(row, None):
                        dec = predictor.predict(cdir, task)
                    pred = normalise_pred(task, dec)
                    arms[name].append((case, truth, pred, dec))
                    entry[name] = pred

            # 越权：未揭示段落换成垃圾，row 与预测都必须不变
            tmp = redact_copy(cdir, task, vis)
            try:
                row_r, vec_r = assemble_row(task, tmp, vis, "zero", dep)
                row_same = rows_equal(row_r, row_zero)
                with forced_features(row_r, vec_r):
                    dec_r = predictor.predict(tmp, task)
                pred_same = normalise_pred(task, dec_r) == entry["ledger_zero"]
            finally:
                shutil.rmtree(tmp.parent, ignore_errors=True)
            if not (row_same and pred_same):
                redact_breaks.append({"case": case, "row_changed": not row_same, "pred_changed": not pred_same})

            # 零填充伪造审计：未揭示来源 + 现状值 0.0 + 全量真值非 0
            for f in CLINICAL_FEATURES[task]:
                if dep.get(f, set()) <= vis:
                    continue
                z, t = row_zero.get(f), row_full.get(f)
                if isinstance(z, float) and z == 0.0 and isinstance(t, float) and np.isfinite(t) and t != 0.0:
                    fab[f]["fabricated"] += 1
                    fab[f]["true_min"] = min(fab[f].get("true_min", t), t)
                    fab[f]["true_max"] = max(fab[f].get("true_max", t), t)

            per_case.append(entry)

        llm_pairs = [(c, truth_of(task, c), llm_arm_pred(task, c), {}) for c in cases]
        llm_pairs = [p for p in llm_pairs if p[2] is not None]

        block = {
            "cases": len(cases),
            "ledger": {"spent_units": summarise_ledger(per_case)},
            "arms": {name: summarise(task, pairs) for name, pairs in arms.items()},
            "llm_arm_C": summarise(task, llm_pairs) if llm_pairs else {"cases": 0},
            "vs_full": {name: agreement(task, arms["full"], pairs) for name, pairs in arms.items() if name != "full"},
            "redaction_violations": redact_breaks,
            "mask_bites": {"cases_with_changed_features": len(row_deltas), "cases": len(cases),
                           "mean_features_changed": round(float(np.mean(row_deltas)), 2) if row_deltas else 0},
            "fabricated_negatives": {k: dict(v) for k, v in
                                     sorted(fab.items(), key=lambda kv: -kv[1]["fabricated"])[:10]},
            "feature_dependencies": {f: sorted(s) for f, s in sorted(dep_stats.items())},
            "sections_visible_dist": dict(Counter(len(e["visible"]) for e in per_case)),
            "per_case": per_case,
        }
        results["tasks"][task] = block

        print(f"\n=== task{task} n={len(cases)} ===")
        for name, s in block["arms"].items():
            print("  ", name, json.dumps({k: v for k, v in s.items()}, ensure_ascii=False)[:200])
        print("   LLM arm C:", json.dumps(block["llm_arm_C"], ensure_ascii=False)[:200])
        print("   越权违规:", len(redact_breaks), "| 掩码实际改动的病例:", block["mask_bites"])
        print("   伪造阴性 top:", {k: v["fabricated"] for k, v in list(block["fabricated_negatives"].items())[:6]})

    (OUT / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str) + "\n")
    print("\n写入:", OUT / "results.json")
    return 0


def summarise_ledger(per_case):
    spent = [e["spent_units"] for e in per_case if e["spent_units"] is not None]
    return {"n": len(spent), "mean_spent": round(float(np.mean(spent)), 2) if spent else None,
            "max_spent": max(spent) if spent else None,
            "distribution": dict(Counter(spent))}


if __name__ == "__main__":
    raise SystemExit(main())
