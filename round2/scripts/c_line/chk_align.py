import json

import numpy as np
from sentence_transformers import SentenceTransformer

V2 = "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2"
recs = [json.loads(l) for l in open(V2 + "/records_v2.jsonl")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]
vec = np.load(V2 + "/vectors.npy")
print("core", len(core), "vecs", vec.shape)

m = SentenceTransformer("/home/hedongze/CHIMERA-agent/resources/embedding_model",
                        device="cpu", local_files_only=True)
m.max_seq_length = 512
idx = [0, 1, 47, 48, 500, 900, 1079]
got = m.encode_document([core[i]["retrieval_text"] for i in idx], batch_size=4,
                        show_progress_bar=False)


def cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


for k, i in enumerate(idx):
    c = cos(got[k], vec[i])
    flag = "OK" if c > 0.999 else ("CHECK" if c > 0.99 else "MISMATCH")
    print("idx %4d kind=%-14s cos=%.6f %-8s id=%s page=%s"
          % (i, core[i]["kind"], c, flag, core[i]["id"], core[i].get("page")))
