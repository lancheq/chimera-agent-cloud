"""v2 分片编码：用 retrieval_text（章节标题 + 原文）建向量。

支持断点续跑：已存在的 shard 跳过。
"""

import argparse
import json
from pathlib import Path

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--records", required=True)
ap.add_argument("--model", default="/home/hedongze/CHIMERA-agent/resources/embedding_model")
ap.add_argument("--outdir", required=True)
ap.add_argument("--start", type=int, required=True)
ap.add_argument("--end", type=int, required=True)
ap.add_argument("--shard", type=int, required=True)
a = ap.parse_args()

recs = [json.loads(l) for l in open(a.records, encoding="utf-8")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]
batch = core[a.start : a.end]

from sentence_transformers import SentenceTransformer

m = SentenceTransformer(a.model, device="cpu", local_files_only=True)
m.max_seq_length = 512
v = m.encode_document(
    [r["retrieval_text"] for r in batch], batch_size=4, show_progress_bar=False
).astype("float32")

out = Path(a.outdir)
out.mkdir(parents=True, exist_ok=True)
np.save(out / f"shard_{a.shard:02d}.npy", v)
print(json.dumps({"shard": a.shard, "range": [a.start, a.end], "shape": list(v.shape)}), flush=True)
