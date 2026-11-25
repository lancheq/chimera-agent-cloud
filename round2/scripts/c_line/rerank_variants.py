"""重排器分数对照 R1-R6：固定同一条推荐，只改上下文，看原始分数。

R1 原文
R2 原文 + 所属小节标题
R3 原文 + 相关临床背景（同节叙述，PI-RADS/PSA-D 阈值）
R4 原文 + 同决策问题的其他分支（PI-RADS >=4 / =3）
R5 原文 + 无关章节文本（等长对照）
R6 原文 + 同节其他非分支推荐（相关但不适用）

R4 与 R5 长度对齐，用来分离「长度」与「内容相关性」。
"""

import json

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

REC = "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124/records.jsonl"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
Q01 = "biopsy indications for elevated PSA and PI-RADS 3 in 48-year-old male"

recs = {r["id"]: r for r in (json.loads(l) for l in open(REC))}
R1 = recs["eau-00272"]["text"].strip()
b1 = recs["eau-00271"]["text"].strip()
b3 = recs["eau-00273"]["text"].strip()
title = recs["eau-00272"]["section_title"].strip()
sec = recs["eau-00272"]["section_id"]

# 同节叙述（PI-RADS/PSA-D 阈值背景）
narr = [r for r in recs.values() if r["kind"] == "narrative" and r["section_id"].startswith("5.5")]
background = narr[0]["text"][:420] if narr else ""

# 无关但等长的原文（取 6.x 节叙述，截到与 R4 相近长度）
unrelated_src = next(r["text"] for r in recs.values()
                     if r["kind"] == "narrative" and r["section_id"].startswith("6.2.1"))

# 同节非分支推荐
others = [recs[i]["text"].strip() for i in ("eau-00266", "eau-00267") if i in recs]

R4 = R1 + " " + b1 + " " + b3
target_len = len(R4) - len(R1)
R5 = R1 + " " + unrelated_src[:target_len].rsplit(".", 1)[0] + "."
R6 = R1 + " " + " ".join(others)

VARIANTS = {
    "R1 原文": R1,
    "R2 原文+小节标题": f"{title} {R1}",
    "R3 原文+相关背景": f"{R1} {background}",
    "R4 原文+其他分支": R4,
    "R5 原文+等长无关": R5,
    "R6 原文+同节非分支": R6,
}

tok = AutoTokenizer.from_pretrained(RER)
mdl = AutoModelForSequenceClassification.from_pretrained(RER)
mdl.eval()

rows = []
for name, text in VARIANTS.items():
    inp = tok([(Q01, text)], padding=True, truncation=True, return_tensors="pt", max_length=512)
    with torch.no_grad():
        s = float(mdl(**inp).logits.squeeze(-1))
    n_tok = int(inp["input_ids"].shape[1])
    rows.append({"variant": name, "chars": len(text), "tokens": n_tok, "score": round(s, 4)})

base = rows[0]["score"]
for r in rows:
    r["delta"] = round(r["score"] - base, 4)
    print(f'{r["variant"]:22s} chars={r["chars"]:4d} tok={r["tokens"]:4d} score={r["score"]:+.4f} Δ={r["delta"]:+.4f}')

json.dump({"query": Q01, "rows": rows, "background_used": background[:120]},
          open("/tmp/rerank_variants.json", "w"), ensure_ascii=False, indent=1)
