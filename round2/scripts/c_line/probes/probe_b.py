"""旧库检索探针 B：指定查询的完整链路回放（dense / BM25 / RRF / rerank）+ 原文全文。

只读。输出 /tmp/probe_b.json
"""
import json, os, re, sys

sys.path.insert(0, "/home/hedongze/CHIMERA-agent/src")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from chimera_agent_baseline.rag import start_embedding_service, GuidelinesSearch

RES = "/home/hedongze/CHIMERA-agent/resources"
svc = start_embedding_service(RES + "/embedding_model")
gs = GuidelinesSearch(RES, RES + "/reranker_model")
print("chunks:", len(gs._chunks), "reranker:", gs._reranker is not None, flush=True)

SEC_RE = re.compile(r"^(\d+(?:\.\d+)*)")
QUERIES = [
    ("qa-T1-s0-005", "T1", "5.4.2.2", "Is biopsy recommended for patients with negative MRI and PSA density <0.15 ng/mL/cc?"),
    ("qa-T1-s1-013", "T1", "5.1.3", "Should prostate cancer early detection continue in asymptomatic men with life expectancy below 15 years?"),
    ("qa-T2-s2-015", "T2", "6.6.8", "Can androgen receptor antagonist monotherapy be used in patients with M1 prostate cancer?"),
    ("qa-T3-s11-021", "T3", "6.4.8", "Should salvage radiotherapy be delayed when PET/CT is negative?"),
    ("qa-T2-s21-123", "T2", "7.3.1", "How does the EAU define focal therapy?"),
]
# 真实 agent 线上查询风格（关键词堆叠，取自 10-26 冒烟 trace）
QUERIES.append(("agent-style", "T1", "5.5", "EAU guidelines biopsy recommendation PI-RADS 2 prior positive biopsy PSA density repeat biopsy indication"))


def sec_id(h):
    m = SEC_RE.match((h.get("section") or "").strip())
    return m.group(1) if m else None


def pack(h, rank):
    return {
        "rank": rank,
        "id": h["id"],
        "section": h.get("section"),
        "sec_id": sec_id(h),
        "page": h.get("page"),
        "dense": h.get("dense_score"),
        "bm25": h.get("bm25_score"),
        "rrf": h.get("rrf_score"),
        "rerank": h.get("rerank_score"),
        "len": len(h["text"] or ""),
        "text": (h["text"] or ""),
    }


out = []
for qid, task, gold, q in QUERIES:
    dense = gs._dense_search(q, 20)
    bm25 = gs._bm25_search(q, 20)
    cand = gs._merge_rrf(dense, bm25)
    ranked = gs._rerank(q, cand[: gs.RERANK_CANDIDATES])
    qa_hits = gs._qa_search(q, 3)
    gold_rank = next((r for r, h in enumerate(ranked, 1) if sec_id(h) == gold), None)
    out.append(
        {
            "id": qid, "task": task, "gold": gold, "q": q,
            "n_cand": len(cand),
            "gold_rank_rerank": gold_rank,
            "dense20": [pack(h, r) for r, h in enumerate(dense, 1)],
            "bm25_20": [pack(h, r) for r, h in enumerate(bm25, 1)],
            "rrf_top10": [pack(h, r) for r, h in enumerate(cand[:10], 1)],
            "rerank_top10": [pack(h, r) for r, h in enumerate(ranked[:10], 1)],
            "qa_hits": qa_hits,
        }
    )
    print("done", qid, "gold_rank", gold_rank, flush=True)

json.dump(out, open("/tmp/probe_b.json", "w"), ensure_ascii=False, indent=1)

# 新切分库（结构感知）中同节的样子：section_full + 该节的 recommendation 条目
recs = [json.loads(l) for l in open("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124/records.jsonl", encoding="utf-8")]
gold_secs = {g for _, _, g, _ in QUERIES}
pick = {"section_full": [], "recommendation": []}
for r in recs:
    sid = str(r.get("section_id") or "")
    if sid in gold_secs:
        pick.setdefault(r["kind"], []).append(r)
json.dump(pick, open("/tmp/probe_new.json", "w"), ensure_ascii=False, indent=1)
print("new records:", {k: len(v) for k, v in pick.items()}, flush=True)
svc.stop()
