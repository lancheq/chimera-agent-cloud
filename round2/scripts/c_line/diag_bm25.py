"""Q05 BM25 分数来源诊断：不预设"语料缩小导致 IDF 上升"。

输出：
1) Q05 关键 query 词在新库(996)与旧库 2025 子集(1375)的 df / idf
2) 方法学段 eau-00011 与正确段 eau-00310 的逐词 BM25 贡献
"""

import json
import math
import re

import numpy as np

NEW = "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124/records.jsonl"
OLD = "/tmp/kb_export/old_kb.npz"

Q05 = "EAU guidelines re-biopsy prior positive biopsy chronic kidney disease"
TOK = re.compile(r"[a-z0-9]+")
tok = lambda s: TOK.findall(s.lower())


def corpus_stats(texts, k1=1.5, b=0.75):
    docs = [tok(t) for t in texts]
    n = len(docs)
    df = {}
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    dl = [len(d) for d in docs]
    avg = sum(dl) / n
    idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
    return {"n": n, "avg": avg, "df": df, "idf": idf, "docs": docs, "dl": dl, "k1": k1, "b": b}


def contrib(stats, q, i):
    """逐词 BM25 贡献（i 为文档下标）。"""
    tf = {}
    for w in stats["docs"][i]:
        tf[w] = tf.get(w, 0) + 1
    k1, b, avg, dl = stats["k1"], stats["b"], stats["avg"], stats["dl"][i]
    out = {}
    for w in q:
        c = tf.get(w, 0)
        if not c:
            continue
        out[w] = stats["idf"].get(w, 0) * c * (k1 + 1) / (
            c + k1 * (1 - b + b * dl / max(avg, 1)))
    return out


recs = [json.loads(l) for l in open(NEW)]
core = [r for r in recs if r["kind"] in ("narrative", "recommendation", "table")]
new_texts = [r["text"] for r in core]
new_ids = [r["id"] for r in core]

o = np.load(OLD, allow_pickle=True)
o_texts = [t for t, v in zip(o["texts"], o["versions"]) if v == "2025"]
o_ids = [i for i, v in zip(o["ids"], o["versions"]) if v == "2025"]

S_new = corpus_stats(new_texts)
S_old = corpus_stats(o_texts)

q = tok(Q05)
words = ["eau", "guideline", "guidelines", "biopsy", "prior", "positive", "chronic", "kidney", "disease"]

print(f'new corpus n={S_new["n"]} avgdl={S_new["avg"]:.1f} | old2025 n={S_old["n"]} avgdl={S_old["avg"]:.1f}')
print("\n词 | 新库 df | 新库 idf | 旧库 df | 旧库 idf")
for w in words:
    dn, do = S_new["df"].get(w, 0), S_old["df"].get(w, 0)
    print(f'{w:12s} | {dn:4d} | {S_new["idf"].get(w,0):6.3f} | {do:4d} | {S_old["idf"].get(w,0):6.3f}')

print("\n--- 新库内两段文本的逐词贡献（Q05 query）")
for target in ("eau-00011", "eau-00310"):
    if target not in new_ids:
        print(target, "missing")
        continue
    i = new_ids.index(target)
    c = contrib(S_new, q, i)
    print(f'\n{target} dl={S_new["dl"][i]} total={sum(c.values()):.3f}')
    for w, v in sorted(c.items(), key=lambda x: -x[1]):
        print(f'   {w:12s} idf={S_new["idf"].get(w,0):6.3f} contrib={v:7.3f}')

print("\n--- 旧库同名段（方法学/5.7.1）")
for oid in ("unified-0011", "unified-0204"):
    if oid not in o_ids:
        print(oid, "missing")
        continue
    i = o_ids.index(oid)
    c = contrib(S_old, q, i)
    print(f'\n{oid} dl={S_old["dl"][i]} total={sum(c.values()):.3f}')
    for w, v in sorted(c.items(), key=lambda x: -x[1]):
        print(f'   {w:12s} idf={S_old["idf"].get(w,0):6.3f} contrib={v:7.3f}')
