"""旧库检索质量探针 A：全量 120 条英文查询，dense/BM25/RRF 三路逐条落盘。

只读 resources/guidelines_db，不改任何东西。输出 /tmp/probe_a.json
"""
import json, os, re, sys, statistics

sys.path.insert(0, "/home/hedongze/CHIMERA-agent/src")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from chimera_agent_baseline.rag import start_embedding_service, GuidelinesSearch

RES = "/home/hedongze/CHIMERA-agent/resources"
svc = start_embedding_service(RES + "/embedding_model")
gs = GuidelinesSearch(RES, None)  # 不加载 reranker：本阶段只看召回层
print("chunks:", len(gs._chunks), flush=True)

SEC_RE = re.compile(r"^(\d+(?:\.\d+)*)")
JOURNAL = re.compile(r"(Lancet|Eur Urol|J Clin Oncol|N Engl J Med|J Urol|BJU Int|Radiology|Cancers|Oncol|Urol)", re.I)


def sec_id(hit):
    m = SEC_RE.match((hit.get("section") or "").strip())
    return m.group(1) if m else None


def flags(text, meta):
    t = text.strip()
    page = meta.get("page") or 0
    out = []
    # 句中起始：小写/标点/逗号开头，或第一个词不是大写首字母
    if re.match(r"^[a-z,;)\]]", t):
        out.append("mid_sentence")
    # 目录页特征：大量编号 + 平均句长极短
    nums = len(re.findall(r"\b\d+\.\d+(\.\d+)*\b", t))
    if isinstance(page, int) and page <= 12 and nums >= 6:
        out.append("toc_like")
    sec = meta.get("section") or ""
    if re.match(r"^\d+\s", sec) and JOURNAL.search(sec):
        out.append("ref_pseudo_section")
    if re.search(r"\b(Strong|Weak)\b", t) and "Recommendations" not in t:
        out.append("rating_without_rule")
    if "LIMITED UPDATE MARCH 2026" in t:
        out.append("hdr_2026")
    elif "MARCH 2025" in t:
        out.append("hdr_2025")
    return out


def pack(hit, rank, retriever):
    meta = {"page": hit.get("page"), "section": hit.get("section")}
    return {
        "rank": rank,
        "retriever": retriever,
        "id": hit["id"],
        "section": hit.get("section"),
        "sec_id": sec_id(hit),
        "page": hit.get("page"),
        "dense": hit.get("dense_score"),
        "bm25": hit.get("bm25_score"),
        "rrf": hit.get("rrf_score"),
        "len": len(hit["text"] or ""),
        "flags": flags(hit["text"] or "", meta),
        "head": (hit["text"] or "")[:400],
    }


qs = [json.loads(l) for l in open("/home/denghze/x", "r")] if False else None
qs = [json.loads(l) for l in open("/home/hedongze/CHIMERA-agent/rag_eval_20261025/rag_eval_sample_en.jsonl", encoding="utf-8")]

out = []
for i, q in enumerate(qs):
    text = q["question"]
    dense = gs._dense_search(text, 20)
    bm25 = gs._bm25_search(text, 20)
    cand = gs._merge_rrf(dense, bm25)
    gold = q.get("gold_section") or q.get("gold")
    gold_rank = None
    for r, h in enumerate(cand, 1):
        if sec_id(h) == gold:
            gold_rank = r
            break
    out.append(
        {
            "id": q["id"],
            "task": q.get("task"),
            "gold": gold,
            "q": text,
            "n_cand": len(cand),
            "gold_rank_rrf": gold_rank,
            "top10": [pack(h, r, "rrf") for r, h in enumerate(cand[:10], 1)],
            "dense_top5": [pack(h, r, "dense") for r, h in enumerate(dense[:5], 1)],
            "bm25_top5": [pack(h, r, "bm25") for r, h in enumerate(bm25[:5], 1)],
        }
    )
    if (i + 1) % 20 == 0:
        print("done", i + 1, flush=True)

json.dump(out, open("/tmp/probe_a.json", "w"), ensure_ascii=False, indent=1)
miss = [o for o in out if o["gold_rank_rrf"] is None]
toc = [o for o in out if any("toc_like" in c["flags"] for c in o["top10"][:5])]
mid = [o for o in out if any("mid_sentence" in c["flags"] for c in o["top10"][:3])]
ref = [o for o in out if any("ref_pseudo_section" in c["flags"] for c in o["top10"][:5])]
print(json.dumps({
    "n": len(out),
    "gold_not_in_top20": len(miss),
    "top5_has_toc": len(toc),
    "top3_has_mid_sentence": len(mid),
    "top5_has_ref_pseudo_section": len(ref),
}, ensure_ascii=False))
svc.stop()
