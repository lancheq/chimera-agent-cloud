"""T1 独立验证病例抽样（第一阶段的结项实验）。

规则：
- 池：phase2 训练集 task1，195 例
- 排除：曾在方法设计、错误分析、提示词调整、检索调参中使用过的病例
- 分层：按 structured-prompt.json 的 bx 字段（None=初次诊断 / Negative=阴性后复评 / Positive=阳性后复评）
- 每层目标 10 例，不足则取实际可用数量并如实报告
- 固定随机种子 20261124，抽样只看 bx 与排除集，不看任何模型输出

输出：/tmp/t1_sample.json + 控制台清单
"""

import glob
import json
import os
import random
import re
from collections import Counter

POOL = ("/home/hedongze/CHIMERA-agent/data/round2_materials_20261121/"
        "train_release_transfer/20261121_104011_78d1168e/train_release_chimera_phase2/task1")
OUT = "/home/hedongze/CHIMERA-agent/output"
EXCLUDE_DIRS = [
    "s4_mechanism_review_20261115",
    "evidence_applicability_audit_20261117",
    "planning_C01_retrieval_20261118",
    "planning_C01_frozen_retrieval_replay_20261118",
    "s4_shared_plan_performance_20261115_v2r1",
    "c03_corpus_audit_20261121",
    "c04_1_p2_consumer_replay_20261123",
]
SEED = 20261124
TARGET = 10

pat = re.compile(r"PT-pseudo_[0-9a-f]{12}")
excluded = {}
for d in EXCLUDE_DIRS:
    found = set()
    for root, _, files in os.walk(os.path.join(OUT, d)):
        for fn in files:
            if not fn.endswith((".json", ".jsonl", ".md", ".py", ".csv", ".txt", ".log")):
                continue
            try:
                with open(os.path.join(root, fn), "r", errors="ignore") as f:
                    found.update(pat.findall(f.read()))
            except Exception:
                pass
    for cid in found:
        excluded.setdefault(cid, []).append(d)

print(f"排除集: {len(excluded)} 例（来自 {len(EXCLUDE_DIRS)} 个实验目录）")
print("排除集规模明细:", {d: sum(1 for v in excluded.values() if d in v) for d in EXCLUDE_DIRS})

cases = sorted(os.listdir(POOL))
meta = {}
for cid in cases:
    p = os.path.join(POOL, cid, "structured-prompt.json")
    try:
        d = json.load(open(p))
    except Exception:
        continue
    meta[cid] = {"bx": str(d.get("bx")), "age": d.get("age"), "psav": d.get("psav"),
                 "psa": d.get("psa"), "months": d.get("months")}

SCENE = {"None": "initial_diagnosis", "Negative": "post_negative_review",
         "Positive": "post_positive_review"}

pool_by_scene = {v: [] for v in SCENE.values()}
for cid, m in meta.items():
    if cid in excluded:
        continue
    s = SCENE.get(m["bx"])
    if s:
        pool_by_scene[s].append(cid)

print("\n排除后各场景可用数：")
for s, lst in pool_by_scene.items():
    print(f"  {s:22s} {len(lst):3d}")

rng = random.Random(SEED)
sample = {}
for s, lst in pool_by_scene.items():
    avail = sorted(lst)
    take = min(TARGET, len(avail))
    picked = rng.sample(avail, take) if take else []
    sample[s] = sorted(picked)
    print(f"  {s:22s} 抽 {take} 例")

print("\n=== 抽样清单")
for s, ids in sample.items():
    print(f"\n[{s}] {len(ids)} 例")
    for cid in ids:
        m = meta[cid]
        print(f"  {cid}  bx={m['bx']:8s} age={m['age']} psav={m['psav']}")

result = {
    "seed": SEED,
    "pool": len(meta),
    "excluded_total": len(excluded),
    "excluded_dirs": EXCLUDE_DIRS,
    "available_by_scene": {k: len(v) for k, v in pool_by_scene.items()},
    "sample": sample,
    "target_per_scene": TARGET,
}
open("/tmp/t1_sample.json", "w").write(json.dumps(result, ensure_ascii=False, indent=1))
print("\nWROTE /tmp/t1_sample.json")
