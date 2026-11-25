import json
from pathlib import Path

import numpy as np

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]

shards = sorted((V2 / "vec").glob("shard_*.npy"))
vecs = np.vstack([np.load(p) for p in shards]).astype("float32")
assert vecs.shape[0] == len(core), f"{vecs.shape[0]} vectors vs {len(core)} units"
np.save(V2 / "vectors.npy", vecs)
(V2 / "merge_summary.json").write_text(json.dumps(
    {"shards": len(shards), "shape": list(vecs.shape)}, indent=2))
print(json.dumps({"shards": len(shards), "shape": list(vecs.shape)}))
