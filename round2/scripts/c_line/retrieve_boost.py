"""章节标题增强前后对照：同一 query、同一管线，只换 recommendation 的向量。"""

import json
import re
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

NEW = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124")
EMB = "/home/hedongze/CHIMERA-agent/resources/embedding_model"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
QSRC = "/home/hedongze/CHIMERA-agent/output/planning_C01_retrieval_20261118/query_stages_private.json"
RRF_K = 60

recs = {r["id"]: r for r in (json.loads(l) for l in open(NEW / "records.jsonl"))}
core = [r for r in recs.values() if r["kind"] in ("narrative", "recommendation", "table")]
ids = [r["id"] for r in core]
texts = [r["text"] for r in core]
TITLED = [f'{r.get("section_title","").strip()}. {r["text"]}' if (r["kind"] == "recommendation" and (r.get("section_title") or "").strip()) else r["text"]
          for r in core]
V = {"base": np.load(NEW / "vectors.npy"), "boost": np.load(NEW / "vectors_boost.npy"),
     "boost_text": np.load(NEW / "vectors_boost.npy")}
for k in V:
    V[k] = V[k] / (np.linalg.norm(V[k], axis=1, keepdims=True) + 1e-9)

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
bm = BM25(texts)
rt = AutoTokenizer.from_pretrained(RER)
rm = AutoModelForSequenceClassification.from_pretrained(RER)
rm.eval()
print("ready", flush=True)

queries = {q["query_id"]: q["historical_query"] for q in json.load(open(QSRC))["queries"]}
WATCH = {"Q01": ["eau-00272", "eau-00271", "eau-00273"],
         "Q03": ["eau-00310"], "Q05": ["eau-00470", "eau-00471"]}
pos = {i: k for k, i in enumerate(ids)}

out = {}
for qid, qtext in queries.items():
    v = np.array(enc.encode_query(qtext))
    v = v / (np.linalg.norm(v) + 1e-9)
    bs = bm.scores(tok(qtext))
    b_idx = np.argsort(-bs)[:20]
    out[qid] = {}
    for name, vecs in V.items():
        sims = vecs @ v
        d_idx = np.argsort(-sims)[:20]
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
        rr_texts = TITLED if name == "boost_text" else texts
        pairs = [(qtext, rr_texts[i]) for i in order]
        inp = rt(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
        with torch.no_grad():
            sc = rm(**inp).logits.squeeze(-1).tolist()
        rk = [ids[order[j]] for j in np.argsort(-np.array(sc))]

        watch = {}
        for wid in WATCH.get(qid, []):
            k = pos.get(wid)
            if k is None:
                continue
            watch[wid] = {
                "dense": int(np.where(d_idx == k)[0][0]) + 1 if k in d_idx else None,
                "bm25": int(np.where(b_idx == k)[0][0]) + 1 if k in b_idx else None,
                "rrf": order.index(k) + 1 if k in order else None,
                "rerank": rk.index(wid) + 1 if wid in rk else None,
            }
        out[qid][name] = {
            "watch": watch,
            "top5": rk[:5],
            "top5_sections": [recs[i].get("section_id") for i in rk[:5]],
        }
    print(qid, "done", flush=True)

json.dump(out, open("/tmp/title_boost.json", "w"), ensure_ascii=False, indent=1)
for qid in out:
    print(f"\n=== {qid}")
    for name in ("base", "boost"):
        d = out[qid][name]
        print(f'  {name:6s} top5_sections={d["top5_sections"]}')
        for w, v in d["watch"].items():
            print(f'      {w}: dense{v["dense"]} bm25{v["bm25"]} rrf{v["rrf"]} rerank{v["rerank"]}')
