"""导出旧库全部向量到 npz（在副本上操作，不触碰原库）。

输出：/tmp/kb_export/old_kb.npz
  ids, vectors(float32), texts, versions(2026/2025/summary)
"""

import json
import shutil
from pathlib import Path

import numpy as np

SRC = Path("/home/hedongze/CHIMERA-agent/resources/guidelines_db")
WORK = Path("/tmp/kb_export")
DST = WORK / "guidelines_db"

if DST.exists():
    shutil.rmtree(DST)
WORK.mkdir(parents=True, exist_ok=True)
shutil.copytree(SRC, DST)

import chromadb

client = chromadb.PersistentClient(path=str(DST))
col = client.get_collection("guidelines")
got = col.get(include=["embeddings", "documents", "metadatas"])
ids = got["ids"]
vecs = np.array(got["embeddings"], dtype="float32")
texts = got["documents"]
metas = got["metadatas"]

# 用官方导出（2026）的文本哈希标记版本
official = [json.loads(l) for l in open(
    "/home/hedongze/CHIMERA-agent/guideline_dataset/raw/eau_official_chunks.jsonl")]
import hashlib

h26 = {hashlib.md5((o.get("text") or "").encode()).hexdigest() for o in official}

versions = []
for t, m in zip(texts, metas):
    src = (m or {}).get("source", "")
    if src in ("AUA", "PIRADS"):
        versions.append("summary")
    elif hashlib.md5((t or "").encode()).hexdigest() in h26:
        versions.append("2026")
    else:
        versions.append("2025")

np.savez_compressed(
    WORK / "old_kb.npz",
    ids=np.array(ids),
    vectors=vecs,
    texts=np.array(texts, dtype=object),
    versions=np.array(versions),
)
from collections import Counter

print(json.dumps({"n": len(ids), "shape": list(vecs.shape),
                  "versions": dict(Counter(versions))}, ensure_ascii=False))
