"""F0-F3：非临床段落过滤 × Query 泛词移除。

F0 当前新库（996）
F1 排除非临床段落（section_id 以 1./2. 开头：引言/方法学/未来计划）
F2 Query 去掉 EAU / guidelines 等泛词
F3 F1 + F2

不改正文、不重新编码，直接用已有向量做子集。
"""

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
vecs = np.load(NEW / "vectors.npy")
vecs = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)

NONCLIN = re.compile(r"^(1\.|2\.)")
keep = np.array([not NONCLIN.match(str(r.get("section_id", "")) or "") for r in core])
print(f"clinical units {keep.sum()} / {len(core)} (dropped {(~keep).sum()})", flush=True)

GENERIC = re.compile(r"\b(eau|guidelines?)\b", re.I)


def strip_generic(q):
    q2 = GENERIC.sub(" ", q)
    return re.sub(r"\s+", " ", q2).strip()


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
rt = AutoTokenizer.from_pretrained(RER)
rm = AutoModelForSequenceClassification.from_pretrained(RER)
rm.eval()
print("models ready", flush=True)

# (语料下标, strip_bm25, strip_dense, strip_rerank)
ALL = list(range(len(core)))
CLIN = [i for i in range(len(core)) if keep[i]]
CFG = {
    "F0_base": (ALL, False, False, False),
    "F4_bm25_only": (ALL, True, False, False),
    "F5_bm25_dense": (ALL, True, True, False),
    "F6_all_three": (ALL, True, True, True),
}

queries = {q["query_id"]: q["historical_query"] for q in json.load(open(QSRC))["queries"]}
tm = json.load(open("/tmp/abcd_result.json"))["target_map"]
watch = {q: [v["new_id"] for v in tm.values() if v["old_version"] == "2025" and v["new_id"] in recs]
         for q in queries}
# 只看与本 query 相关的目标：用 abcd 结果里该 query 的判定集合
prev = json.load(open("/tmp/abcd_result.json"))["results"]["D_new_2025"]
for q in watch:
    ids_q = [v["judged_id"] for v in prev[q]["targets"].values() if v.get("judged_id")]
    watch[q] = [i for i in ids_q if i in recs]

out = {}
for cname, (idx, s_bm, s_dn, s_rr) in CFG.items():
    sub_ids = [ids[i] for i in idx]
    sub_texts = [texts[i] for i in idx]
    sub_vecs = vecs[idx]
    pos = {i: k for k, i in enumerate(sub_ids)}
    bm = BM25(sub_texts)
    out[cname] = {}
    for qid, qt in queries.items():
        q_bm = strip_generic(qt) if s_bm else qt
        q_dn = strip_generic(qt) if s_dn else qt
        q_rr = strip_generic(qt) if s_rr else qt
        q_use = q_bm
        v = np.array(enc.encode_query(q_dn))
        v = v / (np.linalg.norm(v) + 1e-9)
        sims = sub_vecs @ v
        d_idx = np.argsort(-sims)[:20]
        bs = bm.scores(tok(q_bm))
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
        pairs = [(q_rr, sub_texts[i]) for i in order]
        inp = rt(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
        with torch.no_grad():
            sc = rm(**inp).logits.squeeze(-1).tolist()
        rk = [sub_ids[order[j]] for j in np.argsort(-np.array(sc))]

        w = {}
        for tid in watch[qid]:
            k = pos.get(tid)
            w[tid] = {
                "dense": int(np.where(d_idx == k)[0][0]) + 1 if k is not None and k in d_idx else None,
                "bm25": int(np.where(b_idx == k)[0][0]) + 1 if k is not None and k in b_idx else None,
                "rrf": order.index(k) + 1 if k is not None and k in order else None,
                "rerank": rk.index(tid) + 1 if tid in rk else None,
            }
        out[cname][qid] = {"query_bm25": q_bm, "query_dense": q_dn, "query_rerank": q_rr, "top5": rk[:5],
                           "top5_sections": [recs[i].get("section_id") for i in rk[:5]],
                           "watch": w}
    print(cname, "done", flush=True)

json.dump(out, open("/tmp/f_experiment.json", "w"), ensure_ascii=False, indent=1)

print("\n=== 目标条目重排名次（越小越好，None=未进）")
for qid in queries:
    print(f"\n{qid}")
    for cname in CFG:
        d = out[cname][qid]
        ranks = {k: v["rerank"] for k, v in d["watch"].items()}
        print(f'  {cname:18s} top5={d["top5_sections"]}')
        print(f'     {" ".join(f"{k}:{v}" for k, v in ranks.items())}')
