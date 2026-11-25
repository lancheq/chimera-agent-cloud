#!/usr/bin/env python3
"""v2 冻结件 + 独立验证病例预注册（不参考任何系统输出）。

冻结：把 v2 三个产物的 sha256 与配置写进 FREEZE.json，作为后续对比的锚点。
选例：T1 有 GT 的 91 例里，按 sha256(case_id) 排序取前 10/层，三层共 30 例；
      排除已用于调参的 3 例；不读任何 prediction/trace。规则先写死再执行。
"""

import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
DATA = Path("/home/hedongze/CHIMERA-agent/data/task1/agent_input")
OUT = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2/FREEZE.json")
SELECT = Path("/home/hedongze/CHIMERA-agent/output/c_line_validation_20261124/selection.json")

TUNING = ["PT-pseudo_562159dc2cc6", "PT-pseudo_4f33b0253633", "PT-pseudo_c39e94a13920"]
PER_STRATUM = 10
SCEN = {"None": "initial_diagnostic", "Negative": "repeat_after_negative", "Positive": "repeat_after_positive"}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    files = {}
    for name in ("records_v2.jsonl", "vectors.npy", "summary_v2.json", "merge_summary.json"):
        p = V2 / name
        if p.exists():
            files[name] = {"sha256": sha(p), "bytes": p.stat().st_size}
    vec_dir = V2 / "vec"
    shards = sorted(vec_dir.glob("shard_*.npy")) if vec_dir.exists() else []
    files["vec_shards"] = {"n": len(shards),
                           "sha256_concat": hashlib.sha256(
                               "".join(sha(p) for p in shards).encode()).hexdigest()}

    config = {
        "corpus": "rechunk_20261124_v2 (EAU 2025, 结构感知切分)",
        "retrievable_units": 1080,
        "section_full_not_indexed": 292,
        "embedding": "resources/embedding_model (embeddinggemma-300m), retrieval_text, max_seq 512",
        "reranker": "resources/reranker_model, max_length 512",
        "recall": {"dense_top": 20, "bm25_top": 20, "rrf_k": 60, "rrf_candidates": 30},
        "final_context": 5,
        "p1_backfill": {"enabled": True, "section_cap": 2, "order": "按首次命中的重排顺序",
                        "dedupe": "original_text 归一化去重 + 表格按 caption 去重", "truncate": False},
        "generic_word_filter": "F6（BM25 + dense + rerank 三处同时去 EAU/AUA/guideline 等泛词）",
        "clinical_context_filter": "关闭",
        "frozen_at": "2026-11-24",
        "known_defects_not_fixed": [
            "表格记录 section_id 自指（89/97 等于表号），其中 38 条为幻影小节",
            "同一张表因跨页重复入库：24 组、多出 43 条记录",
            "表格 clinical_context 随幻影小节继承，已确证 2 例判错（Table 6.2.8 / 6.2.10 → active_surveillance_confirmed）",
            "部分表格 section_id/section_title 为空",
        ],
    }
    OUT.write_text(json.dumps({"files": files, "config": config}, indent=2, ensure_ascii=False) + "\n")
    print("冻结件写入", OUT)
    for k, v in files.items():
        print("  ", k, v)

    # ---- 选例
    cases = sorted(os.listdir(DATA))
    rows = defaultdict(list)
    for c in cases:
        if c in TUNING:
            continue
        if not (DATA / c / "prostate-biopsy-decision.json").exists():
            continue
        try:
            bx = json.loads((DATA / c / "structured-prompt.json").read_text()).get("bx")
        except Exception:
            continue
        scen = SCEN.get(str(bx))
        if scen:
            rows[scen].append(c)

    chosen, pool = [], {}
    for scen in ("initial_diagnostic", "repeat_after_negative", "repeat_after_positive"):
        cand = sorted(rows[scen], key=lambda c: hashlib.sha256(c.encode()).hexdigest())
        pool[scen] = len(cand)
        take = cand[:PER_STRATUM]
        chosen += [(scen, c, hashlib.sha256(c.encode()).hexdigest()[:12]) for c in take]

    SELECT.parent.mkdir(parents=True, exist_ok=True)
    SELECT.write_text(json.dumps({
        "rule": "T1 中有 GT 的病例，排除已用于调参的 3 例（sha256 排序的固定种子无涉），"
                "按 sha256(case_id) 升序每层取前 10；选取时未读取任何 prediction/trace/输出",
        "excluded_tuning_cases": TUNING,
        "pool_size_by_stratum": pool,
        "per_stratum": PER_STRATUM,
        "selected": [{"scenario": s, "case": c, "hash": h} for s, c, h in chosen],
    }, indent=2, ensure_ascii=False) + "\n")
    print("\n选例写入", SELECT)
    print("每层候选:", pool)
    print("分布:", Counter(s for s, _, _ in chosen))
    for s, c, h in chosen:
        print("  ", s, c, h)


if __name__ == "__main__":
    main()
