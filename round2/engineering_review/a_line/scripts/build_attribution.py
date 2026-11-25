#!/usr/bin/env python3
"""A 线逐例归因（修正版）：只把必须经工具取得的栏目当作"证据可得性"证据。

修正点：dre / bx / fh / comorbidity / age / psa 初值均在上游 initial record 内，
不因未调用工具判为"无据主张"。只有 pirads / vol（影像）、psad / cspca（化验）
必须经工具取得。
"""
import json
import glob
import os
from collections import Counter

ROOT = "/home/hedongze/CHIMERA-agent"
BASE = f"{ROOT}/output/s4_shared_plan_performance_20261115_v2r1"
OUT = f"{ROOT}/output/a_attribution_20261122"
REVIEW = f"{ROOT}/output/s4_mechanism_review_20261115"

CLINICAL = {
    "get_psa_trend": "psa_trend",
    "get_mri_report": "radiology_report",
    "get_lab_results": "laboratory_results",
    "get_previous_notes": "previous_notes",
}
# 仅这些变量必须经工具取得
TOOL_DEPENDENT = {
    "pirads": "radiology_report",
    "vol": "radiology_report",
    "psad": "laboratory_results",
    "cspca": "laboratory_results",
}
UPFRONT_ONLY = {"age", "dre", "bx", "fh", "comorbidity", "psa"}

ledger = {r["case_id"]: r for r in json.load(open(f"{OUT}/ledger.json"))}
early = {r["case_id"]: r for r in json.load(open(f"{REVIEW}/early_stop_private.json"))}


def cdir(cid, arm):
    return glob.glob(f"{BASE}/worker*/{arm}/task1/{cid}")[0]


def view(cid, arm):
    ev = [json.loads(l) for l in open(os.path.join(cdir(cid, arm), "candidate_events.jsonl")) if l.strip()]
    last = None
    seq = []
    for e in ev:
        if e["kind"] == "tool_request":
            seq.append(CLINICAL.get(e.get("tool"), "knowledge"))
        elif e["kind"] == "tool_return":
            last = e.get("budget_after") or last
    vis = list((last or {}).get("visible_sections") or [])
    stop = next((e for e in ev if e["kind"] == "acquisition_stop"), {})
    p = json.load(open(os.path.join(cdir(cid, arm), "prediction.json")))
    return {
        "action_sequence": seq,
        "visible_sections": vis,
        "information_units": (last or {}).get("spent_units"),
        "admitted_attempts": (last or {}).get("admitted_attempts"),
        "decision": p.get("biopsy_decision"),
        "confidence": p.get("confidence"),
        "variable_weights": p.get("variable_weights") or {},
        "free_text": p.get("free_text") or "",
        "stop_explanation_chars": len(stop.get("explanation") or ""),
    }


targets = ([c for c, r in ledger.items() if r["group"] in ("only_adaptive", "only_fixed")]
           + [c for c, r in ledger.items() if r["group"] == "both_wrong"])

rows = []
for cid in targets:
    rec = ledger[cid]
    f, a = view(cid, "fixed"), view(cid, "adaptive")
    unsup = {}
    for arm, v in (("fixed", f), ("adaptive", a)):
        unsup[arm] = sorted([k for k, w in v["variable_weights"].items()
                             if w in ("decisive", "important")
                             and TOOL_DEPENDENT.get(k) and TOOL_DEPENDENT[k] not in v["visible_sections"]])
    es = early.get(cid)
    rows.append({
        "case_id": cid,
        "group": rec["group"],
        "label": rec["label"],
        "fixed_decision": f["decision"],
        "adaptive_decision": a["decision"],
        "fixed_visible": f["visible_sections"],
        "adaptive_visible": a["visible_sections"],
        "fixed_sequence": f["action_sequence"],
        "adaptive_sequence": a["action_sequence"],
        "fixed_units": f["information_units"],
        "adaptive_units": a["information_units"],
        "decisive_variables": {arm: sorted([k for k, w in v["variable_weights"].items()
                                            if w in ("decisive", "important")])
                               for arm, v in (("fixed", f), ("adaptive", a))},
        "unsupported_tool_dependent": unsup,
        "unseen_clinical_adaptive": sorted(set(CLINICAL.values()) - set(a["visible_sections"])),
        "early_stop": None if not es else {
            "pending_categories": es["pending_categories"],
            "feasible_new_patient_units": sum(p.get("new_patient_units", 0)
                                              for p in es["pending"] if p.get("individually_feasible")),
        },
        "fixed_free_text": f["free_text"],
        "adaptive_free_text": a["free_text"],
    })

json.dump(rows, open(f"{OUT}/attribution_30.json", "w"), ensure_ascii=False, indent=1)

# 混淆矩阵（全 91）
cm = {}
for arm in ("fixed", "adaptive"):
    d = {}
    for rec in ledger.values():
        cid = rec["case_id"]
        pred = view(cid, arm)["decision"]
        pos, dec = rec["label"] == "yes", pred == "yes"
        d[("TP" if pos and dec else "FN" if pos else "FP" if dec else "TN")] = \
            d.get(("TP" if pos and dec else "FN" if pos else "FP" if dec else "TN"), 0) + 1
    cm[arm] = {k: d.get(k, 0) for k in ("TP", "FN", "FP", "TN")}

bucket = Counter()
for r in rows:
    for arm in ("fixed", "adaptive"):
        if (r["label"] == "yes") == (r[f"{arm}_decision"] == "yes"):
            continue
        if r["unsupported_tool_dependent"][arm]:
            bucket[f"{arm}:被判 decisive 的影像/化验变量未取对应栏目"] += 1
        elif r["early_stop"] and r["early_stop"]["feasible_new_patient_units"] > 0:
            bucket[f"{arm}:过早停止且停止时仍有可负担未取栏目"] += 1
        else:
            bucket[f"{arm}:栏目已取或不可负担，错在推理或输出层"] += 1

summary = {
    "n": len(rows),
    "by_group": dict(Counter(r["group"] for r in rows)),
    "confusion_matrix_all91": cm,
    "label_distribution": dict(Counter(r["label"] for r in ledger.values())),
    "failure_buckets": dict(bucket),
    "both_wrong_all_label_no": all(r["label"] != "yes" for r in rows if r["group"] == "both_wrong"),
    "both_wrong_all_both_yes": all(r["fixed_decision"] == "yes" and r["adaptive_decision"] == "yes"
                                   for r in rows if r["group"] == "both_wrong"),
    "cases_with_unsupported_tool_dependent": sum(1 for r in rows if any(r["unsupported_tool_dependent"].values())),
}
json.dump(summary, open(f"{OUT}/attribution_summary.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1))
