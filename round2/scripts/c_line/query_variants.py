"""Q01 Query 修正对照：原始（PI-RADS 3）vs 修正（PI-RADS 2）。

在 A 混合库 / C 纯2025旧 / D 纯2025新 上各跑一次，看 PI-RADS ≤2 那条正确推荐
（旧 unified-0179 / 新 eau-00272）与 =3 那条（新 eau-00273）的名次如何移动。
"""

import json
import re

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

OLD = "/tmp/kb_export/old_kb.npz"
from pathlib import Path as _P
NEW = _P("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124")
EMB = "/home/hedongze/CHIMERA-agent/resources/embedding_model"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
RRF_K = 60

Q_ORIG = "biopsy indications for elevated PSA and PI-RADS 3 in 48-year-old male"
Q_CORR = "biopsy indications for elevated PSA and PI-RADS 2 in 48-year-old male"

old = np.load(OLD, allow_pickle=True)
o_ids, o_vecs, o_texts, o_ver = list(old["ids"]), old["vectors"], old["texts"], old["versions"]
recs = {r["id"]: r for r in (json.loads(l) for l in open(NEW / "records.jsonl"))}
n_core = [r for r in recs.values() if r["kind"] in ("narrative", "recommendation", "table")]
n_ids = [r["id"] for r in n_core]
n_vecs = np.load(NEW / "vectors.npy")
n_texts = [r["text"] for r in n_core]


def norm(m):
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


m26, msum = o_ver == "2026", o_ver == "summary"
GROUPS = {
    "A_old_mixed": (o_ids, norm(o_vecs), o_texts),
    "C_old_2025": ([o_ids[k] for k in np.where(o_ver == "2025")[0]],
                   norm(o_vecs[o_ver == "2025"]),
                   [o_texts[k] for k in np.where(o_ver == "2025")[0]]),
    "D_new_2025": (n_ids, norm(n_vecs), n_texts),
}

TOK = re.compile(r"[a-z0-9]+")
tok = lambda s: TOK.findall(s.lower())


class BM25:
    def __init__(self, corpus, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tf, self.dl, df = [], [], {}
        for t in corpus:
            d = tok(t)
            self.dl.append(len(d))
            c = {}
            for w in d:
                c[w] = c.get(w, 0) + 1
            self.tf.append(c)
            for w in set(d):
                df[w] = df.get(w, 0) + 1
        self.n = len(corpus)
        self.avg = sum(self.dl) / max(self.n, 1)
        self.idf = {w: np.log(1 + (self.n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def scores(self, q):
        out = np.zeros(self.n, dtype="float32")
        for i in range(self.n):
            tf, dl = self.tf[i], self.dl[i]
            s = 0.0
            for w in q:
                c = tf.get(w)
                if c:
                    s += self.idf.get(w, 0) * c * (self.k1 + 1) / (
                        c + self.k1 * (1 - self.b + self.b * dl / max(self.avg, 1)))
            out[i] = s
        return out


from sentence_transformers import SentenceTransformer

enc = SentenceTransformer(EMB, device="cpu", local_files_only=True)
QV = {}
for name, q in (("orig", Q_ORIG), ("corr", Q_CORR)):
    v = np.array(enc.encode_query(q))
    QV[name] = v / (np.linalg.norm(v) + 1e-9)
print("encoder ready", flush=True)

BM = {g: BM25(GROUPS[g][2]) for g in GROUPS}
rt = AutoTokenizer.from_pretrained(RER)
rm = AutoModelForSequenceClassification.from_pretrained(RER)
rm.eval()
print("reranker ready", flush=True)

# 关注的具体条目
WATCH = {
    "unified-0179": "旧库 PI-RADS<=2 完整行",
    "eau-00272": "新库 PI-RADS<=2 单条",
    "eau-00271": "新库 PI-RADS>=4 分支",
    "eau-00273": "新库 PI-RADS=3 分支",
}

out = {}
for g, (ids, vecs, texts) in GROUPS.items():
    pos = {i: k for k, i in enumerate(ids)}
    out[g] = {}
    for qn, qtext in (("orig", Q_ORIG), ("corr", Q_CORR)):
        sims = vecs @ QV[qn]
        d_idx = np.argsort(-sims)[:20]
        bs = BM[g].scores(tok(qtext))
        b_idx = np.argsort(-bs)[:20]

        fused, pool = {}, {}
        for r, i in enumerate(d_idx):
            pool[i] = 1
            fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + r + 1)
        for r, i in enumerate(b_idx):
            if bs[i] <= 0:
                continue
            pool[i] = 1
            fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + r + 1)
        order = sorted(pool, key=lambda i: -fused.get(i, 0))[:30]
        pairs = [(qtext, texts[i]) for i in order]
        inp = rt(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
        with torch.no_grad():
            sc = rm(**inp).logits.squeeze(-1).tolist()
        rk = [order[j] for j in np.argsort(-np.array(sc))]
        rk_ids = [ids[i] for i in rk]

        detail = {}
        for wid, label in WATCH.items():
            if wid not in pos:
                detail[wid] = None
                continue
            k = pos[wid]
            detail[wid] = {
                "dense": int(np.where(d_idx == k)[0][0]) + 1 if k in d_idx else None,
                "bm25": int(np.where(b_idx == k)[0][0]) + 1 if k in b_idx else None,
                "rrf": order.index(k) + 1 if k in order else None,
                "rerank": rk_ids.index(wid) + 1 if wid in rk_ids else None,
                "top5": wid in rk_ids[:5],
            }
        out[g][qn] = {"detail": detail,
                      "top5_ids": rk_ids[:5],
                      "top5_sections": [recs.get(i, {}).get("section_id", "~") for i in rk_ids[:5]]}

json.dump(out, open("/tmp/query_variants.json", "w"), ensure_ascii=False, indent=1)

for g in out:
    for qn in ("orig", "corr"):
        print(f"\n=== {g} / {qn}")
        for wid, d in out[g][qn]["detail"].items():
            if d:
                print(f'  {wid:14s} dense{d["dense"]} bm25{d["bm25"]} rrf{d["rrf"]} rerank{d["rerank"]} top5={d["top5"]}')
        print("  top5:", out[g][qn]["top5_ids"])
