"""把结构感知重切分结果建成独立向量库（不触碰 resources/guidelines_db）。

- 输入：rechunk_20261124/records.jsonl（narrative / recommendation / table）
- 编码：resources/embedding_model（embeddinggemma-300m, CPU）
- 输出：rechunk_20261124/db（chroma，collection=guidelines）
- section_full 不入库（只作父文档回填用）
"""

import argparse
import json
from pathlib import Path

import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("--records", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--model", default="/home/hedongze/CHIMERA-agent/resources/embedding_model")
ap.add_argument("--batch", type=int, default=32)
a = ap.parse_args()

recs = [json.loads(l) for l in open(a.records, encoding="utf-8")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table")]
print(f"units to index: {len(core)}", flush=True)

from sentence_transformers import SentenceTransformer

model = SentenceTransformer(a.model, device="cpu", local_files_only=True)
texts = [r["text"] for r in core]
vecs = model.encode_document(
    texts, batch_size=a.batch, show_progress_bar=False, normalize_embeddings=False
).astype("float32")
assert np.isfinite(vecs).all()
print("embedding shape", vecs.shape, flush=True)

out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
import chromadb

client = chromadb.PersistentClient(path=str(out))
try:
    client.delete_collection("guidelines")
except Exception:
    pass
col = client.create_collection("guidelines")

for i in range(0, len(core), 256):
    sl = core[i : i + 256]
    col.add(
        ids=[r["id"] for r in sl],
        documents=[r["text"] for r in sl],
        embeddings=vecs[i : i + 256].tolist(),
        metadatas=[
            {
                "kind": r["kind"],
                "section_id": r.get("section_id", ""),
                "section_title": r.get("section_title", ""),
                "page": r.get("page") or 0,
                "strength": r.get("strength", ""),
            }
            for r in sl
        ],
    )

summary = {"indexed": col.count(), "model": a.model, "dim": int(vecs.shape[1])}
(out.parent / "index_summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary), flush=True)
