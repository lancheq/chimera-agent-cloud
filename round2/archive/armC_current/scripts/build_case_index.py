#!/usr/bin/env python3
"""集B 步骤9：为 T1 GT 91 case 建相似度索引。

用结构化临床特征（PSA/PSAD/age/PI-RADS/vol/DRE/cspca）构建
feature 向量，cosine 相似度检索 top-K 相似 GT case。

输出: resources/case_index/t1_case_index.json
  {"cases": [{"case_id": ..., "features": [...], "feature_names": [...],
              "gt_decision": "yes"/"no", "gt_confidence": ..., "gt_reasoning": ...}]}
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUT_DIR = PROJECT_ROOT / "resources" / "case_index"

# Feature extraction: numeric features used for similarity
FEATURE_NAMES = ["psa", "psad", "age", "vol", "pirads_num", "cspca", "psav", "psap"]


def safe_float(v, default=0.0):
    try:
        f = float(v)
        return f if f == f else default  # NaN check
    except (TypeError, ValueError):
        return default


def extract_features(sp: dict) -> list[float]:
    """Extract numeric feature vector from structured-prompt."""
    pirads_map = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "None": 0, None: 0}
    pirads_raw = str(sp.get("pirads", "0"))
    pirads_num = pirads_map.get(pirads_raw, 0)
    return [
        safe_float(sp.get("psa")),
        safe_float(sp.get("psad")),
        safe_float(sp.get("age")),
        safe_float(sp.get("vol")),
        float(pirads_num),
        safe_float(sp.get("cspca")),
        safe_float(sp.get("psav")),
        safe_float(sp.get("psap")),
    ]


def normalize(features: list[float], means: list[float], stds: list[float]) -> list[float]:
    """Z-score normalize."""
    return [(f - m) / s if s > 1e-9 else 0.0 for f, m, s in zip(features, means, stds)]


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return dot / (na * nb)


def main() -> int:
    gt_dir = DATA_DIR / "task1" / "agent_input"
    gt_cases = sorted(
        c for c in os.listdir(gt_dir)
        if (gt_dir / c / "prostate-biopsy-decision.json").exists()
    )
    print(f"[index] Found {len(gt_cases)} T1 GT cases")

    cases = []
    all_features = []
    for cid in gt_cases:
        cdir = gt_dir / cid
        sp = json.load(open(cdir / "structured-prompt.json"))
        dec = json.load(open(cdir / "prostate-biopsy-decision.json"))
        rpath = cdir / "prostate-biopsy-decision-reasoning.json"
        rsn = json.load(open(rpath)) if rpath.exists() else {}

        feats = extract_features(sp)
        all_features.append(feats)
        cases.append({
            "case_id": cid,
            "features": feats,
            "gt_decision": dec,
            "gt_confidence": rsn.get("confidence", ""),
            "gt_variable_weights": rsn.get("variable_weights", {}),
            "gt_reveal_sequence": rsn.get("reveal_sequence", []),
            "clinical_summary": {
                "psa": sp.get("psa"), "psad": sp.get("psad"),
                "age": sp.get("age"), "pirads": sp.get("pirads"),
                "vol": sp.get("vol"), "cspca": sp.get("cspca"),
            },
        })

    # Compute means/stds for normalization
    n_feats = len(FEATURE_NAMES)
    means = [sum(f[i] for f in all_features) / len(all_features) for i in range(n_feats)]
    stds = [
        (sum((f[i] - means[i]) ** 2 for f in all_features) / len(all_features)) ** 0.5
        for i in range(n_feats)
    ]

    # Normalize all
    for c in cases:
        c["features_norm"] = normalize(c["features"], means, stds)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "feature_names": FEATURE_NAMES,
        "means": means,
        "stds": stds,
        "cases": cases,
    }
    out_path = OUT_DIR / "t1_case_index.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # Quick self-test: find top-3 for first case
    test = cases[0]
    sims = []
    for c in cases[1:]:  # exclude self
        s = cosine_sim(test["features_norm"], c["features_norm"])
        sims.append((s, c["case_id"], c["gt_decision"]))
    sims.sort(reverse=True)
    print(f"[self-test] Top-3 for {test['case_id']} (GT={test['gt_decision']}):")
    for s, cid, dec in sims[:3]:
        print(f"  {cid}: sim={s:.3f}, GT={dec}")

    print(f"[done] Index saved -> {out_path} ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
