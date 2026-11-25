"""组合方案回放：v2 库（标题入检索文本）+ F6 泛词过滤 + P1 分支回填。

回放 6 条历史 query，记录：
  证据覆盖率（正确条目是否进入最终上下文）
  场景适用率（上下文条目 clinical_context 是否与正确条目一致）
  无关内容数（nonclinical / epidemiology 类条目数）
  token 成本
"""

import json
import re
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
V1 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124")
EMB = "/home/hedongze/CHIMERA-agent/resources/embedding_model"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
QSRC = "/home/hedongze/CHIMERA-agent/output/planning_C01_retrieval_20261118/query_stages_private.json"
RRF_K = 60

recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]
ids = [r["id"] for r in core]
texts = [r["retrieval_text"] for r in core]
orig = [r["original_text"] for r in core]
vecs = np.load(V2 / "vector_out.npy") if (V2 / "vector_out.npy").exists() else np.load(V2 / "vectors.npy")
vecs = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
assert vecs.shape[0] == len(core), f"{vecs.shape[0]} vs {len(core)}"
pos = {i: k for k, i in enumerate(ids)}

# v1 id -> v2 id（按 original_text 匹配）
v1 = [json.loads(l) for l in open(V1 / "records.jsonl", encoding="utf-8")]
def norm_ws(x):
    return re.sub(r"\s+", " ", x).strip().lower()
v2_by_text = {}
for r in core:
    v2_by_text.setdefault(norm_ws(r["original_text"]), r["id"])
v1_to_v2 = {}
for r in v1:
    k = norm_ws(r.get("text", ""))
    if k in v2_by_text:
        v1_to_v2[r["id"]] = v2_by_text[k]
print(f"v1->v2 mapped {len(v1_to_v2)}/{len(v1)}", flush=True)

GENERIC = re.compile(r"\b(eau|aua|sioq|esur|isup|guidelines?)\b", re.I)
strip_generic = lambda q: re.sub(r"\s+", " ", GENERIC.sub(" ", q)).strip()

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
tm = json.load(open("/tmp/abcd_result.json"))["target_map"]
# 每条 query 的正确目标（来自 C01 的判定集合）
prev = json.load(open("/tmp/abcd_result.json"))["results"]["D_new_2025"]
watch = {}
for q in queries:
    want = []
    for v in prev[q]["targets"].values():
        vid = v.get("judged_id")
        if vid and vid in v1_to_v2:
            want.append(v1_to_v2[vid])
    watch[q] = want

BY_SECTION = {}
for r in core:
    if r["kind"] == "recommendation":
        BY_SECTION.setdefault(r.get("section_id"), []).append(r["id"])
BRANCH = re.compile(r"^(when|if|in men|in patients|where|for patients)", re.I)

out = {}
for qid, qt in queries.items():
    q_use = strip_generic(qt)
    v = np.array(enc.encode_query(q_use))
    v = v / (np.linalg.norm(v) + 1e-9)
    sims = vecs @ v
    d_idx = np.argsort(-sims)[:20]
    bs = bm.scores(tok(q_use))
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
    pairs = [(q_use, texts[i]) for i in order]
    inp = rt(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
    with torch.no_grad():
        sc = rm(**inp).logits.squeeze(-1).tolist()
    rk = [ids[order[j]] for j in np.argsort(-np.array(sc))]

    top5 = rk[:5]
    ctx = list(top5)
    for i in top5:
        r = core[pos[i]]
        if r["kind"] == "recommendation" and BRANCH.match(r["original_text"].strip()):
            for s in BY_SECTION.get(r.get("section_id"), []):
                if s not in ctx and BRANCH.match(core[pos[s]]["original_text"].strip()):
                    ctx.append(s)

    # 目标临床场景（按 C01 每条 query 的决策问题）
    EXPECT = {
        "Q01": ["mri_biopsy_decision"], "Q02": ["mri_biopsy_decision"],
        "Q03": ["repeat_biopsy_prior_result", "repeat_biopsy_prior_negative"],
        "Q04": ["repeat_biopsy_prior_result", "repeat_biopsy_prior_negative"],
        "Q05": ["active_surveillance_confirmed"], "Q06": ["active_surveillance_confirmed"],
    }
    exp = EXPECT.get(qid, [])
    noise = mismatch = applicable = 0
    for i in ctx:
        r = core[pos[i]]
        if r["doc_type"] == "nonclinical" or r["clinical_context"] == ["unknown"]:
            noise += 1
        elif exp and not (set(r["clinical_context"]) & set(exp)):
            mismatch += 1
        else:
            applicable += 1

    body = "\n\n".join(core[pos[i]]["original_text"] for i in ctx)
    n_tok = len(rt(body)["input_ids"])
    ctxs = [c for i in ctx for c in core[pos[i]]["clinical_context"]]
    correct_ids = [w for w in watch[qid] if w in ctx]
    out[qid] = {
        "query_used": q_use,
        "top5": top5,
        "top5_sections": [core[pos[i]].get("section_id") for i in top5],
        "top5_doc_types": [core[pos[i]]["doc_type"] for i in top5],
        "top5_contexts": [core[pos[i]]["clinical_context"] for i in top5],
        "context_items": len(ctx),
        "tokens": n_tok,
        "watch": {w: (rk.index(w) + 1 if w in rk else None) for w in watch[qid]},
        "correct_covered": correct_ids,
        "correct_total": len(watch[qid]),
        "ctx_ids": ctx,
        "noise": noise, "mismatch": mismatch, "applicable": applicable,
        "watch": {w: v for w, v in {w: (rk.index(w) + 1 if w in rk else None) for w in watch[qid]}.items() if v is not None or True},
    }
    print(qid, "top5", out[qid]["top5_sections"], "tokens", n_tok, flush=True)

json.dump(out, open("/tmp/retrieve_v2.json", "w"), ensure_ascii=False, indent=1)
print("\n=== 汇总")
for q in out:
    d = out[q]
    print(f'{q}: items={d["context_items"]:2d} tok={d["tokens"]:5d} '
          f'covered={len(d["correct_covered"])}/{d["correct_total"]} noise={d["noise"]} mismatch={d["mismatch"]} ok={d["applicable"]} '
          f'watch={d["watch"]}')
