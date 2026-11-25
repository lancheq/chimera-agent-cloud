"""A/B/C/D 四组语料对照检索（C 线方向一/三）。

四组只改「语料」，dense / BM25 / RRF / 重排 / top-k 逻辑完全一致：
  A 原混合库       = 旧库全部 2889
  B 替换切分混合库  = 新库 996 + 旧库 2026 的 1501 + 摘要 13
  C 纯 2025 旧库    = 旧库 2025 的 1375
  D 纯 2025 新库    = 新库 996

输出 /tmp/abcd_result.json
"""

import json
import re
import time
from pathlib import Path

import numpy as np

OLD = "/tmp/kb_export/old_kb.npz"
NEW = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124")
QSRC = "/home/hedongze/CHIMERA-agent/output/planning_C01_retrieval_20261118/query_stages_private.json"
EMB = "/home/hedongze/CHIMERA-agent/resources/embedding_model"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
TOP_K = (5, 8, 12)
RRF_K = 60

old = np.load(OLD, allow_pickle=True)
o_ids = list(old["ids"])
o_vecs, o_texts, o_ver = old["vectors"], old["texts"], old["versions"]
o_pos = {i: k for k, i in enumerate(o_ids)}

new_recs = [json.loads(l) for l in open(NEW / "records.jsonl", encoding="utf-8")]
new_core = [r for r in new_recs if r["kind"] in ("narrative", "recommendation", "table")]
n_ids = [r["id"] for r in new_core]
n_vecs = np.load(NEW / "vectors.npy")
n_texts = [r["text"] for r in new_core]
n_pos = {i: k for k, i in enumerate(n_ids)}


def norm(m):
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


m26 = o_ver == "2026"
msum = o_ver == "summary"

CORPUS = {
    "A_old_mixed": (o_ids, norm(o_vecs), o_texts, {i: "old" for i in o_ids}),
    "B_new2025_plus_2026": (
        n_ids + [o_ids[k] for k in np.where(m26)[0]] + [o_ids[k] for k in np.where(msum)[0]],
        norm(np.vstack([n_vecs, o_vecs[m26], o_vecs[msum]])),
        n_texts + [o_texts[k] for k in np.where(m26)[0]] + [o_texts[k] for k in np.where(msum)[0]],
        None,
    ),
    "C_old_2025": (
        [o_ids[k] for k in np.where(o_ver == "2025")[0]],
        norm(o_vecs[o_ver == "2025"]),
        [o_texts[k] for k in np.where(o_ver == "2025")[0]],
        None,
    ),
    "D_new_2025": (n_ids, norm(n_vecs), n_texts, None),
}
for g in CORPUS:
    ids = CORPUS[g][0]
    CORPUS[g] = (ids, CORPUS[g][1], CORPUS[g][2], {i: k for k, i in enumerate(ids)})

queries = {q["query_id"]: q for q in json.load(open(QSRC))["queries"]}
targets = json.load(open(QSRC))["target_index_check_details"]

from sentence_transformers import SentenceTransformer

enc = SentenceTransformer(EMB, device="cpu", local_files_only=True)
print("encoder loaded", flush=True)
qvec = {}
for qid, q in queries.items():
    v = np.array(enc.encode_query(q["historical_query"]))
    qvec[qid] = v / (np.linalg.norm(v) + 1e-9)

# 目标：旧 id → 新库对应单元
target_map = {}
new_norm = norm(n_vecs)
for tid, meta in targets.items():
    k = o_pos.get(tid)
    if k is None:
        continue
    v = o_vecs[k] / (np.linalg.norm(o_vecs[k]) + 1e-9)
    sims = new_norm @ v
    j = int(np.argmax(sims))
    target_map[tid] = {
        "old_id": tid, "new_id": n_ids[j], "cos": round(float(sims[j]), 4),
        "old_version": str(o_ver[k]), "review": meta.get("review", ""), "page": meta.get("page"),
    }

TOK = re.compile(r"[a-z0-9]+")
tok = lambda s: TOK.findall(s.lower())


class BM25:
    def __init__(self, corpus, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tf = [dict() for _ in corpus]
        self.dl = []
        df = {}
        for i, t in enumerate(corpus):
            d = tok(t)
            self.dl.append(len(d))
            for w in d:
                self.tf[i][w] = self.tf[i].get(w, 0) + 1
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
                if not c:
                    continue
                s += self.idf.get(w, 0) * c * (self.k1 + 1) / (
                    c + self.k1 * (1 - self.b + self.b * dl / max(self.avg, 1))
                )
            out[i] = s
        return out


print("building bm25 ...", flush=True)
BM = {g: BM25(CORPUS[g][2]) for g in CORPUS}
print("bm25 ready", flush=True)

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

rt = AutoTokenizer.from_pretrained(RER)
rm = AutoModelForSequenceClassification.from_pretrained(RER)
rm.eval()
print("reranker ready", flush=True)


def rerank(qtext, cands, top=30):
    if not cands:
        return []
    pairs = [(qtext, c[2]) for c in cands[:top]]
    inp = rt(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
    with torch.no_grad():
        sc = rm(**inp).logits.squeeze(-1).tolist()
    out = [(c[0], c[1], c[2], float(s)) for c, s in zip(cands[:top], sc)]
    out.sort(key=lambda x: -x[3])
    return out


def resolve(gid, tid, m):
    """组内用于判定的 id。"""
    if gid in ("A_old_mixed", "C_old_2025"):
        return tid if gid != "C_old_2025" or m["old_version"] == "2025" else None
    if gid == "D_new_2025":
        return m["new_id"] if tid in o_pos and o_ver[o_pos[tid]] == "2025" else None
    # B: 2026/summary 目标仍用旧 id，2025 目标用新库单元
    return tid if m["old_version"] in ("2026", "summary") else m["new_id"]


results = {}
t_all = time.time()
for gid, (ids, vecs, texts, pos) in CORPUS.items():
    results[gid] = {}
    for qid, q in queries.items():
        t0 = time.time()
        sims = vecs @ qvec[qid]
        d_idx = np.argsort(-sims)[:20]
        bs = BM[gid].scores(tok(q["historical_query"]))
        b_idx = np.argsort(-bs)[:20]

        fused, pool = {}, {}
        for rank, i in enumerate(d_idx):
            pool[i] = 1
            fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + rank + 1)
        for rank, i in enumerate(b_idx):
            if bs[i] <= 0:
                continue
            pool[i] = 1
            fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + rank + 1)
        order = sorted(pool, key=lambda i: -fused.get(i, 0))
        cands = [(int(i), float(fused.get(i, 0)), texts[i]) for i in order[:30]]
        ranked_ids = [ids[i] for i, _, _, _ in rerank(q["historical_query"], cands)]

        tgt = {}
        for tid, m in target_map.items():
            want = resolve(gid, tid, m)
            if want is None:
                continue
            rk = ranked_ids.index(want) + 1 if want in ranked_ids else None
            tgt[tid] = {
                "judged_id": want,
                "rank_after_rerank": rk,
                "in_top5": want in ranked_ids[:5],
                "in_top8": want in ranked_ids[:8],
                "in_top12": want in ranked_ids[:12],
            }
        results[gid][qid] = {
            "dense_top20": [ids[i] for i in d_idx],
            "bm25_top20": [ids[i] for i in b_idx],
            "rrf_top30": [ids[i] for i in order[:30]],
            "rerank_order": ranked_ids,
            "targets": tgt,
            "seconds": round(time.time() - t0, 2),
        }
        print(gid, qid, round(time.time() - t0, 1), "s", flush=True)

json.dump({"target_map": target_map, "results": results}, open("/tmp/abcd_result.json", "w"),
          ensure_ascii=False, indent=1)
print("TOTAL", round(time.time() - t_all, 1), "s -> /tmp/abcd_result.json", flush=True)
