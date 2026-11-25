"""自动生成 E 组证据：新库检索 top5 + P1 规则回填同节条件分支。

不做人工补入。回填规则固定：
  命中条目若为 recommendation 且以 (When|If|In men|In patients|Where|For patients) 开头，
  则补齐同一 section 内所有满足同一条件的 recommendation。
"""

import json
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

NEW = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124")
EMB = "/home/hedongze/CHIMERA-agent/resources/embedding_model"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
RRF_K = 60

# 三个病例的原始检索 query
CASES = {
    "PT-pseudo_562159dc2cc6": "biopsy indications for elevated PSA and PI-RADS 3 in 48-year-old male",
    "PT-pseudo_4f33b0253633": "EAU guidelines re-biopsy after negative biopsy persistent PSA elevation",
    "PT-pseudo_c39e94a13920": "EAU guidelines re-biopsy prior positive biopsy chronic kidney disease",
}

recs = {r["id"]: r for r in (json.loads(l) for l in open(NEW / "records.jsonl"))}
core = [r for r in recs.values() if r["kind"] in ("narrative", "recommendation", "table")]
ids = [r["id"] for r in core]
texts = [r["text"] for r in core]
vecs = np.load(NEW / "vectors.npy")
vecs = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)

import re

BRANCH = re.compile(r"^(when|if|in men|in patients|where|for patients)", re.I)
TOK = re.compile(r"[a-z0-9]+")
tok = lambda s: TOK.findall(s.lower())

BY_SECTION = {}
for r in core:
    if r["kind"] == "recommendation":
        BY_SECTION.setdefault(r.get("section_id"), []).append(r["id"])


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

out = {}
for case, q in CASES.items():
    v = np.array(enc.encode_query(q))
    v = v / (np.linalg.norm(v) + 1e-9)
    sims = vecs @ v
    d_idx = np.argsort(-sims)[:20]
    bs = bm.scores(tok(q))
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
    pairs = [(q, texts[i]) for i in order]
    inp = rt(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
    with torch.no_grad():
        sc = rm(**inp).logits.squeeze(-1).tolist()
    rk = [ids[order[j]] for j in np.argsort(-np.array(sc))]
    top5 = rk[:5]

    ctx = list(top5)
    for i in top5:
        r = recs[i]
        if r["kind"] == "recommendation" and BRANCH.match(r["text"].strip()):
            for s in BY_SECTION.get(r.get("section_id"), []):
                if s not in ctx and BRANCH.match(recs[s]["text"].strip()):
                    ctx.append(s)

    body = "\n".join(
        f"- [{recs[i].get('section_id')}] {recs[i]['text'].strip()}"
        + (f" [Strength: {recs[i].get('strength')}]" if recs[i].get("strength") else "")
        for i in ctx
    )
    out[case] = {"query": q, "top5": top5, "context_ids": ctx, "evidence": body,
                 "n_items": len(ctx), "chars": len(body)}
    print(f'{case}: top5={len(top5)} ctx={len(ctx)} chars={len(body)}', flush=True)

Path("/tmp/e_evidence.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print("WROTE /tmp/e_evidence.json", flush=True)
