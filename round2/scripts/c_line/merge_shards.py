"""合并分片向量并校验顺序。

分片按 records 顺序切（每片 48 条），shard 编号连续，直接按编号 vstack。
"""

import json
from pathlib import Path

import numpy as np

OUT = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124")
vec_dir = OUT / "vec"
recs = [json.loads(l) for l in open(OUT / "records.jsonl", encoding="utf-8")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table")]

shards = sorted(vec_dir.glob("shard_*.npy"))
vecs = np.vstack([np.load(p) for p in shards]).astype("float32")
assert vecs.shape[0] == len(core), f"{vecs.shape[0]} vectors vs {len(core)} units"
np.save(OUT / "vectors.npy", vecs)
np.save(OUT / "vector_ids.npy", np.array([r["id"] for r in core]))
(out := OUT / "merge_summary.json").write_text(
    json.dumps({"shards": len(shards), "shape": list(vecs.shape)}, indent=2)
)
print(json.dumps({"shards": len(shards), "shape": list(vecs.shape)}))
