"""章节标题增强：给每条独立推荐前缀真实 section_title，重新编码，其余条目不变。

输出 /home/hedongze/.../rechunk_20261124/vectors_boost.npy（996×768）
以及 boost_meta.json 记录替换了哪些条目。
"""

import json
from pathlib import Path

import numpy as np

NEW = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124")
MODEL = "/home/hedongze/CHIMERA-agent/resources/embedding_model"

recs = [json.loads(l) for l in open(NEW / "records.jsonl", encoding="utf-8")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table")]
base = np.load(NEW / "vectors.npy")
assert base.shape[0] == len(core)

idx = [i for i, r in enumerate(core) if r["kind"] == "recommendation" and (r.get("section_title") or "").strip()]
print(f"boosting {len(idx)} recommendation units with section_title", flush=True)

from sentence_transformers import SentenceTransformer

m = SentenceTransformer(MODEL, device="cpu", local_files_only=True)
m.max_seq_length = 512

texts = [f'{core[i]["section_title"].strip()}. {core[i]["text"].strip()}' for i in idx]
vecs = m.encode_document(texts, batch_size=4, show_progress_bar=False).astype("float32")
assert np.isfinite(vecs).all()

out = base.copy()
out[idx] = vecs
np.save(NEW / "vectors_boost.npy", out)

sample = [(core[i]["id"], core[i]["section_title"][:60]) for i in idx[:5]]
(NEW / "boost_meta.json").write_text(json.dumps(
    {"boosted": len(idx), "total": len(core), "sample": sample}, ensure_ascii=False, indent=1))
print("DONE", out.shape, flush=True)
