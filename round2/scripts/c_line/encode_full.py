"""全量编码 + 建 chroma（tmux 内运行，绕开 ssh 会话时长限制）。

环境必须用 numpy/sentence-transformers 与旧库一致的 venv：
    /home/hedongze/venvs/chimera_access/bin/python
"""

import json
import time
from pathlib import Path

import numpy as np

RECORDS = "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124/records.jsonl"
OUT = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124")
MODEL = "/home/hedongze/CHIMERA-agent/resources/embedding_model"

t0 = time.time()
recs = [json.loads(l) for l in open(RECORDS, encoding="utf-8")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table")]
print(f"units {len(core)}", flush=True)

from sentence_transformers import SentenceTransformer

m = SentenceTransformer(MODEL, device="cpu", local_files_only=True)
m.max_seq_length = 512
print(f"model loaded {time.time()-t0:.1f}s", flush=True)

vecs = m.encode_document(
    [r["text"] for r in core], batch_size=16, show_progress_bar=False
).astype("float32")
assert np.isfinite(vecs).all()
np.save(OUT / "vectors.npy", vecs)
print(f"encoded {vecs.shape} in {time.time()-t0:.1f}s", flush=True)

import chromadb

db = OUT / "db"
db.mkdir(parents=True, exist_ok=True)
client = chromadb.PersistentClient(path=str(db))
try:
    client.delete_collection("guidelines")
except Exception:
    pass
col = client.create_collection("guidelines")

for i in range(0, len(core), 128):
    sl = core[i : i + 128]
    col.add(
        ids=[r["id"] for r in sl],
        documents=[r["text"] for r in sl],
        embeddings=vecs[i : i + 128].tolist(),
        metadatas=[
            {
                "kind": r["kind"],
                "section_id": r.get("section_id", ""),
                "section_title": (r.get("section_title") or "")[:200],
                "page": r.get("page") or 0,
                "strength": r.get("strength", ""),
            }
            for r in sl
        ],
    )
print(f"chroma indexed {col.count()} in {time.time()-t0:.1f}s", flush=True)
(OUT / "index_summary.json").write_text(
    json.dumps(
        {"indexed": col.count(), "dim": int(vecs.shape[1]), "model": MODEL,
         "encode_seconds": round(time.time() - t0, 1)},
        indent=2,
    )
)
print("DONE", flush=True)
