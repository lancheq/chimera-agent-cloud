"""v2 冻结口径回放：P1 回填最多 2 小节、按首次命中的重排顺序取、正文去重、不截断。

与 retrieve_v2.py 的差别（都是按冻结要求改的）：
  1. 回填上限：最多补 2 个小节（原来无上限，Q06 一次展开 2 节以上）
  2. 补条顺序：小节按"该节在重排结果里首次出现的位置"排序；节内按重排名次
     （原来按 BY_SECTION 的插入顺序，与重排名次无关）
  3. 去重：按 original_text 归一化后去重；表格按 caption 去重（v2 里同一张表因跨页
     重复入库，Table 5.8 有 4 条，会把同一内容当多条证据喂进去）
  4. 不截断：单条推荐保持完整原文

其余保持不变：初始双路各 20、RRF 前 30、最终正文前 5、F6 泛词过滤、临床场景过滤关闭。
"""

import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
EMB = "/home/hedongze/CHIMERA-agent/resources/embedding_model"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
QSRC = "/home/hedongze/CHIMERA-agent/output/planning_C01_retrieval_20261118/query_stages_private.json"
RRF_K = 60
P1_SECTION_CAP = 2
OUT = Path("/tmp/retrieve_v2_frozen.json")

recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]
ids = [r["id"] for r in core]
texts = [r["retrieval_text"] for r in core]
vecs = np.load(V2 / "vectors.npy")
vecs = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
pos = {i: k for k, i in enumerate(ids)}

GENERIC = re.compile(r"\b(eau|aua|sioq|esur|isup|guidelines?)\b", re.I)
strip_generic = lambda q: re.sub(r"\s+", " ", GENERIC.sub(" ", q)).strip()
TOK = re.compile(r"[a-z0-9]+")
tok = lambda s: TOK.findall(s.lower())
norm_ws = lambda x: re.sub(r"\s+", " ", x).strip().lower()


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

queries = {q["query_id"]: q["historical_query"] for q in json.load(open(QSRC))["queries"]}
BRANCH = re.compile(r"^(when|if|in men|in patients|where|for patients)", re.I)

BY_SECTION = defaultdict(list)
for r in core:
    if r["kind"] == "recommendation":
        BY_SECTION[r.get("section_id")].append(r["id"])

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
    inp = rt([(q_use, texts[i]) for i in order], padding=True, truncation=True,
             return_tensors="pt", max_length=512)
    with torch.no_grad():
        sc = rm(**inp).logits.squeeze(-1).tolist()
    rk = [ids[order[j]] for j in np.argsort(-np.array(sc))]

    top5 = rk[:5]
    ctx = list(top5)
    seen_text = {norm_ws(core[pos[i]]["original_text"]) for i in top5}
    seen_cap = {(core[pos[i]].get("caption") or "").strip().lower()
                for i in top5 if core[pos[i]]["kind"] == "table"}

    # 命中条目里属于"条件型推荐"的小节，按其在重排里的首次出现位置排序
    hit_sections = []
    for i in top5:
        r = core[pos[i]]
        if r["kind"] == "recommendation" and BRANCH.match(r["original_text"].strip()):
            if r.get("section_id") not in hit_sections:
                hit_sections.append(r.get("section_id"))
    filled_sections = hit_sections[:P1_SECTION_CAP]
    added = []
    for sec in filled_sections:
        members = [m for m in BY_SECTION.get(sec, [])]
        members.sort(key=lambda m: rk.index(m) if m in rk else 10**6)
        for m in members:
            r = core[pos[m]]
            if not BRANCH.match(r["original_text"].strip()):
                continue
            if m in ctx:
                continue
            nt = norm_ws(r["original_text"])
            if nt in seen_text:
                continue
            cap = (r.get("caption") or "").strip().lower()
            if cap and cap in seen_cap:
                continue
            ctx.append(m)
            seen_text.add(nt)
            if cap:
                seen_cap.add(cap)
            added.append(m)

    body = "\n\n".join(core[pos[i]]["original_text"] for i in ctx)
    n_tok = len(rt(body)["input_ids"])
    out[qid] = {
        "query_used": q_use,
        "top5": top5,
        "filled": added,
        "filled_sections": filled_sections,
        "hit_sections_in_rerank_order": hit_sections,
        "context_items": len(ctx),
        "tokens": n_tok,
        "chars": len(body),
        "items": [{"id": i, "section": core[pos[i]].get("section_id"),
                   "title": core[pos[i]].get("section_title"),
                   "kind": core[pos[i]]["kind"],
                   "doc_type": core[pos[i]]["doc_type"],
                   "context": core[pos[i]].get("clinical_context"),
                   "page": core[pos[i]].get("page"),
                   "text": core[pos[i]]["original_text"][:220]} for i in ctx],
    }
    print(f"{qid} top5={[core[pos[i]].get('section_id') for i in top5]} "
          f"hit_sections={hit_sections} filled={len(added)} items={len(ctx)} tok={n_tok}", flush=True)

OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
print("\n=== 冻结口径 token 对比（原 → 冻结）")
prev = json.load(open("/tmp/retrieve_v2.json"))
for q in out:
    print(f"  {q}: {prev[q]['tokens']:5d} → {out[q]['tokens']:5d}  "
          f"items {prev[q]['context_items']} → {out[q]['context_items']}")
print("写入", OUT)
