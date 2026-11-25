"""父子回填 P0-P3 对照（Q01 为主，其余 5 条给通用指标）。

检索单元始终是单条（新库 996），只在「交给模型的阅读单元」上变化：
  P0 仅命中条目
  P1 命中条目 + 同小节其他条件分支（When/If/In men/Where 开头）
  P2 命中条目 + 整个父章节 section_full
  P3 不回填，但放宽到 8 / 12 条

指标：正确分支是否出现、错误分支条数、token、覆盖小节数
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

BRANCH = re.compile(r"^(when|if|in men|in patients|where|for patients)", re.I)


def is_branch(r):
    return r["kind"] == "recommendation" and bool(BRANCH.match(r["text"].strip()))


BY_SECTION = {}
for r in core:
    BY_SECTION.setdefault((r.get("section_id"), r.get("kind")), []).append(r["id"])

# 正确的分支（Q01：患者 MRI 2 类 → PI-RADS ≤2 那条）
CORRECT = {"Q01": "eau-00272"}
# 该病例不适用的分支（PI-RADS >=4 与 =3）
WRONG_BRANCH = {"5.5.7": ["eau-00271", "eau-00273"]}

def norm_ws(x):
    return re.sub(r"\s+", " ", x).strip()


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
pos = {i: k for k, i in enumerate(ids)}


def retrieve(qtext, topk=12):
    v = np.array(enc.encode_query(qtext))
    v = v / (np.linalg.norm(v) + 1e-9)
    sims = vecs @ v
    d_idx = np.argsort(-sims)[:20]
    bs = bm.scores(tok(qtext))
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
    return [ids[i] for i in rk[:topk]]


def build(plan, hits):
    """返回交给模型的条目 id 列表（去重保序）。"""
    if plan == "P3_top12":
        return hits[:12]
    k = 5 if plan in ("P0", "P1", "P2") else 8
    base = hits[:k]
    out = list(base)
    if plan == "P0" or plan == "P3_top8":
        return out
    for i in base:
        r = recs[i]
        if plan == "P2":
            parent = [x for x in recs.values() if x["kind"] == "section_full"
                      and x.get("section_id") == r.get("section_id")]
            for p in parent[:1]:
                if p["id"] not in out:
                    out.append(p["id"])
        elif plan == "P1":
            if not is_branch(r):
                continue
            sibs = [s for s in BY_SECTION.get((r.get("section_id"), "recommendation"), []) if s != i]
            for s in sibs:
                if is_branch(recs[s]) and s not in out:
                    out.append(s)
    return out


results = {}
for qid, qtext in queries.items():
    hits = retrieve(qtext)
    results[qid] = {"hits_top12": hits, "plans": {}}
    for plan in ("P0", "P1", "P2", "P3_top8", "P3_top12"):
        ctx = build(plan, hits)
        body = "\n\n".join(recs[i]["text"] for i in ctx)
        body_n = norm_ws(body)
        n_tok = len(rt(body)["input_ids"])
        secs = {recs[i].get("section_id") for i in ctx}
        wrong = 0
        for sec, wl in WRONG_BRANCH.items():
            wrong += sum(1 for w in wl if norm_ws(recs[w]["text"])[:90] in body_n)
        correct = CORRECT.get(qid)
        results[qid]["plans"][plan] = {
            "n_items": len(ctx),
            "tokens": n_tok,
            "sections": len(secs),
            "correct_branch_present": (
                norm_ws(recs[correct]["text"])[:90] in body_n) if correct else None,
            "wrong_branches_present": wrong,
            "ids": ctx,
        }

json.dump(results, open("/tmp/backfill.json", "w"), ensure_ascii=False, indent=1)

for qid in ("Q01",):
    print(f"\n=== {qid}")
    print(f'{"plan":10s} {"items":>5s} {"tokens":>7s} {"secs":>5s} {"correct":>8s} {"wrong":>6s}')
    for plan, d in results[qid]["plans"].items():
        print(f'{plan:10s} {d["n_items"]:5d} {d["tokens"]:7d} {d["sections"]:5d} '
              f'{str(d["correct_branch_present"]):>8s} {d["wrong_branches_present"]:6d}')

print("\n=== 全 6 条汇总（正确分支覆盖 / 平均 token）")
for plan in ("P0", "P1", "P2", "P3_top8", "P3_top12"):
    cov = [results[q]["plans"][plan]["correct_branch_present"] for q in results
           if results[q]["plans"][plan]["correct_branch_present"] is not None]
    toks = [results[q]["plans"][plan]["tokens"] for q in results]
    print(f"{plan:10s} correct {sum(cov)}/{len(cov)}  avg_tokens {sum(toks)//len(toks)}")
