#!/usr/bin/env python
"""Score the two A/B arms on the 91 GT-labelled T1 cases and compare.

Components reuse the official evaluator functions exactly as
tools/official_score_t1.py does (baseline branch only — no P8 recalibration):
  variable_weight_score, important_decisive_factor_score, section_grounding_score
plus decision accuracy, arm-vs-arm decision flips, and guideline-citation counts.

Usage:
  ~/miniconda3/envs/chimera/bin/python score_ab_arms.py \
      --old ~/CHIMERA-agent-ragAB_old/test/output.ab_old/task1 \
      --new ~/CHIMERA-agent/test/output.ab_new/task1 \
      --out ab_score_report.json
"""

import argparse
import collections
import glob
import importlib.util
import json
import os
import re
import sys

ROOT = "/home/hedongze/CHIMERA-agent"
EVAL = f"{ROOT}/CHIMERA-agent/evaluation/evaluate.py"
GT_P = f"{ROOT}/data/task1/agent_input/{{}}/prostate-biopsy-decision-reasoning.json"
SEC_RE = re.compile(r"\b\d{1,2}\.\d{1,2}(?:\.\d{1,2}){0,3}\b")


def load_eval():
    spec = importlib.util.spec_from_file_location("official_eval", EVAL)
    m = importlib.util.module_from_spec(spec)
    sys.modules["official_eval"] = m
    spec.loader.exec_module(m)
    return m


def dec_of(rec):
    j = rec.get("judgement") or rec
    return j.get("biopsy_decision") or rec.get("biopsy_decision")


def score_arm(ev, arm_dir):
    rows = []
    stats = {"n": 0, "match": 0, "mismatch": 0}
    cit = {"cases_with_citation": 0, "n_citations": 0}
    vw, fs, sg = [], [], []
    ung = collections.Counter()
    for pd in sorted(glob.glob(f"{arm_dir}/*/prediction.json")):
        cid = pd.split("/")[-2]
        gp = GT_P.format(cid)
        if not os.path.exists(gp):
            continue
        gt = json.load(open(gp))
        try:
            pr = json.load(open(pd))
        except Exception:
            continue
        if not gt.get("variable_weights") or not pr.get("variable_weights"):
            continue
        stats["n"] += 1
        if dec_of(gt) == dec_of(pr):
            stats["match"] += 1
        else:
            stats["mismatch"] += 1
        a = ev.variable_weight_score(gt, pr)
        b = ev.important_decisive_factor_score(gt, pr)
        c, det = ev.section_grounding_score(pr)
        if a is not None:
            vw.append(a)
        if b is not None:
            fs.append(b)
        if c is not None:
            sg.append(c)
            for v in det.get("ungrounded_variables", []):
                ung[v] += 1
        secs = SEC_RE.findall((pr.get("judgement") or pr).get("free_text", "") or "")
        if secs:
            cit["cases_with_citation"] += 1
            cit["n_citations"] += len(secs)
        rows.append((cid, gt, pr))
    mean = lambda x: sum(x) / len(x) if x else float("nan")  # noqa: E731
    return {
        "n": stats["n"],
        "decision_accuracy": round(stats["match"] / max(stats["n"], 1), 4),
        "variable_weight": round(mean(vw), 4),
        "factor_f1": round(mean(fs), 4),
        "section_grounding": round(mean(sg), 4),
        "weighted_case_delta_potential": round(
            0.25 * mean(vw) + 0.15 * mean(fs) + 0.15 * mean(sg), 4),
        "citations": {**cit, "mean_per_citing_case": round(
            cit["n_citations"] / max(cit["cases_with_citation"], 1), 2)},
        "top_ungrounded": ung.most_common(8),
        "_rows": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", default="ab_score_report.json")
    args = ap.parse_args()
    os.environ.setdefault("CHIMERA_PROMOTE_NOT_USED", "1")
    ev = load_eval()

    report = {}
    rows_by_arm = {}
    for name, arm in (("old", args.old), ("new", args.new)):
        r = score_arm(ev, arm)
        rows_by_arm[name] = {cid: (gt, pr) for cid, gt, pr in r.pop("_rows")}
        report[name] = r
        print(name, json.dumps(r, ensure_ascii=False), flush=True)

    flips = []
    for cid in sorted(set(rows_by_arm["old"]) & set(rows_by_arm["new"])):
        gt_o, pr_o = rows_by_arm["old"][cid]
        _, pr_n = rows_by_arm["new"][cid]
        if dec_of(pr_o) != dec_of(pr_n):
            flips.append({"case": cid, "gt": dec_of(gt_o), "old": dec_of(pr_o), "new": dec_of(pr_n)})
    report["decision_flips"] = flips
    report["delta_new_minus_old"] = {
        k: round(report["new"][k] - report["old"][k], 4)
        for k in ("decision_accuracy", "variable_weight", "factor_f1", "section_grounding")
    }
    print("flips:", json.dumps(flips, ensure_ascii=False))
    print("deltas:", json.dumps(report["delta_new_minus_old"], ensure_ascii=False))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("written", args.out)


if __name__ == "__main__":
    main()
