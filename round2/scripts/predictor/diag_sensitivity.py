#!/usr/bin/env python3
"""诊断：掩码改动了特征，为什么某些任务的预测一例都不翻？敏感度到底来自哪里。"""

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ARM = Path("/home/hedongze/CHIMERA-armC")
sys.path.insert(0, str(ARM / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chimera_agent_baseline.predictor import CLINICAL_FILE, Predictor, _feat_matrix, build_case_features  # noqa: E402

from eval_predictor_masked import (  # noqa: E402
    CASELIST, MODEL_DIR, arm_output_dir, assemble_row, case_dir, dependencies,
    forced_features, normalise_pred, visible_sections,
)

OUT = Path("/home/hedongze/CHIMERA-agent/output/predictor_replay_20261124")


def top_by_abs(m, names, k=8):
    clf = m.get("clf")
    coef = getattr(clf, "coef_", None)
    if coef is None or not names:
        return {}
    v = np.abs(np.asarray(coef, dtype=float)).mean(0) if np.ndim(coef) > 1 else np.abs(coef)
    if len(v) != len(names):
        return {"note": f"coef 维度 {len(v)} != num_names {len(names)}"}
    return {n: round(float(x), 4) for n, x in sorted(zip(names, v), key=lambda kv: -kv[1])[:k]}


def main():
    predictor = Predictor(MODEL_DIR)
    report = {}
    for task in (1, 2, 3):
        cases = json.loads((ARM / "scripts" / CASELIST[task]).read_text())
        m = predictor._models[task]
        names = list(m.get("num_names") or m.get("all_num_names") or [])
        imp = getattr(m.get("clf"), "feature_importances_", None)

        block = {"n_num_features": len(names), "matrix_differs": Counter(),
                 "pred_identical_vs_full": Counter(), "n_cases": len(cases)}
        if imp is not None and len(imp) >= len(names):
            num_imp = dict(zip(names, imp[: len(names)]))
            block["top_numeric_importance"] = {n: round(float(v), 4) for n, v in
                                               sorted(num_imp.items(), key=lambda kv: -kv[1])[:10]}
        else:
            block["model"] = type(m.get("clf")).__name__
            block["top_abs_coef"] = top_by_abs(m, names)
        section_imp = {}

        for case in cases:
            cdir = case_dir(task, case)
            vis, _ = visible_sections(arm_output_dir(task, case) / "information_access.sqlite")
            cd = json.loads((cdir / CLINICAL_FILE[task]).read_text())
            dep = dependencies(task, cd)
            row_full, vec = build_case_features(cdir, task, allowed_sections=None)
            row_zero, _ = assemble_row(task, cdir, vis, "zero", dep)
            row_nan, _ = assemble_row(task, cdir, vis, "nan", dep)

            mats, preds = {}, {}
            for name, row in (("full", row_full), ("zero", row_zero), ("nan", row_nan)):
                mats[name] = _feat_matrix(row, vec, m)
                with forced_features(row, vec):
                    preds[name] = normalise_pred(task, predictor.predict(cdir, task))

            for name in ("zero", "nan"):
                block["matrix_differs"][f"{name}_vs_full"] += int(
                    not np.array_equal(mats["full"], mats[name]))
                block["pred_identical_vs_full"][name] += int(preds[name] == preds["full"])

            if imp is not None and len(imp) >= len(names):
                num_imp = dict(zip(names, imp[: len(names)]))
                for f in names:
                    if f in num_imp and dep.get(f) and not dep[f] <= vis:
                        section_imp[f] = round(float(num_imp[f]), 4)

        if section_imp:
            block["masked_feature_importance"] = dict(sorted(section_imp.items(), key=lambda kv: -kv[1])[:10])
            block["masked_feature_importance_sum"] = round(sum(section_imp.values()), 4)
            block["total_importance"] = round(float(np.sum(imp[: len(names)])), 4)

        report[task] = block
        print(f"task{task} ({block.get('model') or type(m.get('clf')).__name__}): "
              f"矩阵改变 {dict(block['matrix_differs'])} / {len(cases)} 例；"
              f"预测与全量一致 {dict(block['pred_identical_vs_full'])}")
        if "masked_feature_importance_sum" in block:
            print("   被掩码特征的重要性合计 %.4f / 全部 %.4f，top: %s"
                  % (block["masked_feature_importance_sum"], block["total_importance"],
                     list(block["masked_feature_importance"].items())[:4]))

    (OUT / "sensitivity.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print("写入", OUT / "sensitivity.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
