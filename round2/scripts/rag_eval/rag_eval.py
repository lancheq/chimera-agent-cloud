#!/usr/bin/env python
"""Retrieval quality evaluation for the deployed hybrid RAG pipeline (2026-10-25).

Read-only: loads the deployed GuidelinesSearch (2889 chunks, BM25, QA lib,
bge-reranker-base) and measures section-recall against the distill QA gold set
(question -> gold section_id). Nothing under resources/ is modified.

Tracks:
  zh_full   : all 428 verified QA items (Chinese questions)   - dense/bm25/rrf only
  zh_sample : 120-item stratified sample                       - all pipelines incl. rerank
  en_sample : same 120 items, English-translated queries        - all pipelines incl. rerank

Pipelines:
  dense20        deployed dense recall (ChromaDB, embeddinggemma-300m), top-20
  bm25_20        deployed BM25 recall, top-20
  rrf            deployed fusion: RRF(dense20, bm25_20, 0.8/0.2)
  rr_buggy       deployed rerank over ALL rrf candidates ([:512] CHAR truncation bug)
  rr_fixed       rerank with full chunk text (tokenizer-side truncation), top-30 candidates
  wide_rr_fixed  rerank fixed over RRF(dense50, bm25_50), top-30 candidates

Diagnostics:
  oracle      : does ANY chunk in the index carry the gold section number?
                (index-structure ceiling vs retrieval failure)
  qa_channel  : how often _qa_search returns a hit (zh vs en - expected dead for en)

Run:
  ~/miniconda3/envs/chimera/bin/python rag_eval.py \
      --resource-dir /home/hedongze/CHIMERA-agent/resources \
      --data-dir  <this dir>  --out <this dir>
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

SECTION_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)")
KS = (1, 5, 10, 20)


def chunk_section_id(hit: dict):
    sec = (hit.get("section") or "").strip()
    m = SECTION_NUM_RE.match(sec)
    return m.group(1) if m else None


def first_hit_rank(ranked: list[dict], gold: str):
    for rank, h in enumerate(ranked, 1):
        if chunk_section_id(h) == gold:
            return rank
    return None


class Metrics:
    def __init__(self):
        self.n = 0
        self.first_ranks = []
        self.latency = 0.0

    def add(self, ranked, gold, dt):
        self.n += 1
        self.latency += dt
        r = first_hit_rank(ranked, gold)
        if r is not None:
            self.first_ranks.append(r)

    def recall(self, k):
        return sum(1 for r in self.first_ranks if r <= k) / self.n if self.n else 0.0

    def mrr(self, k=10):
        return sum(1.0 / r for r in self.first_ranks if r <= k) / self.n if self.n else 0.0

    def row(self, name):
        return {
            "pipeline": name,
            "n": self.n,
            **{f"recall@{k}": round(self.recall(k), 4) for k in KS},
            "mrr@10": round(self.mrr(), 4),
            "mean_ms": round(self.latency / self.n * 1000, 1) if self.n else None,
        }


def rerank_pairs(gs, query, candidates, char_truncate):
    """Score candidates with the loaded cross-encoder. char_truncate=True replicates
    the deployed bug (text sliced to 512 chars before tokenization)."""
    import torch

    tok = gs._reranker["tokenizer"]
    model = gs._reranker["model"]
    texts = [c["text"][:512] if char_truncate else c["text"] for c in candidates]
    pairs = [(query, t) for t in texts]
    inputs = tok(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
    with torch.no_grad():
        scores = model(**inputs).logits.squeeze(-1).tolist()
    if isinstance(scores, float):
        scores = [scores]
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda cs: -cs[1])
    return [c for c, _ in scored]


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def gold_of(item):
    g = item.get("_primary_grounding") or []
    if g and len(g) >= 2:
        return g[1]
    return item["grounding"][0]["section_id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resource-dir", required=True)
    ap.add_argument("--reranker-dir", default=None,
                    help="Explicit reranker model dir (production wiring via model_dir is broken).")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--extra-file", default=None,
                    help="optional extra track jsonl (id/task/question/gold_section)")
    ap.add_argument("--only-extra", action="store_true", help="skip default tracks")
    args = ap.parse_args()
    out_dir = Path(args.out or args.data_dir)

    src = Path(args.resource_dir).parent / "src"
    sys.path.insert(0, str(src))
    from chimera_agent_baseline import rag  # noqa: E402

    # ---- index + oracle coverage ------------------------------------------
    t0 = time.time()
    svc = rag.EmbeddingService(str(Path(args.resource_dir) / "embedding_model"))
    gs = rag.GuidelinesSearch(args.resource_dir, reranker_dir=args.reranker_dir)
    print(f"[init] loaded in {time.time()-t0:.1f}s: {len(gs._chunks)} chunks, "
          f"{len(gs._qa)} QA items, reranker={'yes' if gs._reranker else 'no'}", flush=True)

    sections_in_index = {chunk_section_id(c) for c in gs._chunks}
    sections_in_index.discard(None)

    # ---- tracks -------------------------------------------------------------
    zh_full = [
        {"id": i["id"], "task": i["task"], "question": i["question"], "gold": gold_of(i)}
        for i in load_jsonl(Path(args.data_dir) / "qa_kept.jsonl")
    ]
    zh_sample = load_jsonl(Path(args.data_dir) / "rag_eval_sample_zh.jsonl")
    zh_sample = [{"gold": i["gold_section"], **{k: i[k] for k in ("id", "task", "question")}} for i in zh_sample]
    en_sample = load_jsonl(Path(args.data_dir) / "rag_eval_sample_en.jsonl")
    en_sample = [{"gold": i["gold_section"], **{k: i[k] for k in ("id", "task", "question")}} for i in en_sample]

    results = {"pipelines": {}, "oracle": {}, "qa_channel": {}, "per_task": {}, "misses": []}

    def eval_fast(track_name, items):
        """dense / bm25 / rrf without rerank."""
        m = {p: Metrics() for p in ("dense20", "bm25_20", "rrf")}
        for it in items:
            q = it["question"]
            t = time.time(); d = gs._dense_search(q, 20); td = time.time() - t
            t = time.time(); b = gs._bm25_search(q, 20); tb = time.time() - t
            t = time.time(); r = gs._merge_rrf(d, b); tr = time.time() - t
            m["dense20"].add(d, it["gold"], td)
            m["bm25_20"].add(b, it["gold"], tb)
            m["rrf"].add(r, it["gold"], tr + td + tb)
            if first_hit_rank(r, it["gold"]) is None:
                results["misses"].append({
                    "id": it["id"], "task": it["task"], "gold": it["gold"],
                    "in_index": it["gold"] in sections_in_index,
                    "q_en": None, "q_zh": q,
                })
        results["pipelines"][track_name] = [m[p].row(p) for p in m]
        for p in m:
            print(f"[{track_name}] {p}: R@5={m[p].recall(5):.3f} R@10={m[p].recall(10):.3f} "
                  f"MRR={m[p].mrr():.3f}", flush=True)

    def eval_rerank(track_name, items):
        m = {p: Metrics() for p in ("rrf", "rr_buggy", "rr_fixed", "wide_rr_fixed")}
        per_task = defaultdict(lambda: defaultdict(Metrics))
        for idx, it in enumerate(items, 1):
            q = it["question"]
            t = time.time(); d20 = gs._dense_search(q, 20); b20 = gs._bm25_search(q, 20)
            rrf = gs._merge_rrf(d20, b20); t_rrf = time.time() - t

            t = time.time(); rr_bug = rerank_pairs(gs, q, [dict(c) for c in rrf], True); t_bug = time.time() - t
            t = time.time(); rr_fix = rerank_pairs(gs, q, [dict(c) for c in rrf[:30]], False); t_fix = time.time() - t
            t = time.time()
            d50 = gs._dense_search(q, 50)
            b50 = gs._bm25_search(q, 50)
            wide = gs._merge_rrf(d50, b50)[:30]
            wide = rerank_pairs(gs, q, wide, False)
            t_wide = time.time() - t

            m["rrf"].add(rrf, it["gold"], t_rrf)
            m["rr_buggy"].add(rr_bug, it["gold"], t_bug + t_rrf)
            m["rr_fixed"].add(rr_fix, it["gold"], t_fix + t_rrf)
            m["wide_rr_fixed"].add(wide, it["gold"], t_wide + t_rrf)
            for p, ranked in (("rrf", rrf), ("rr_buggy", rr_bug), ("rr_fixed", rr_fix), ("wide_rr_fixed", wide)):
                per_task[it["task"]][p].add(ranked, it["gold"], 0)

            if first_hit_rank(rr_fix, it["gold"]) is None:
                results["misses"].append({
                    "id": it["id"], "task": it["task"], "gold": it["gold"],
                    "in_index": it["gold"] in sections_in_index,
                    "q_en": q if track_name.startswith("en") else None,
                    "q_zh": q if track_name.startswith("zh") else None,
                })
            if idx % 20 == 0:
                print(f"[{track_name}] {idx}/{len(items)} done", flush=True)
        results["pipelines"][track_name] = [m[p].row(p) for p in m]
        results["per_task"][track_name] = {
            task: [mm.row(p) for p, mm in pts.items()] for task, pts in per_task.items()
        }
        for p in m:
            print(f"[{track_name}] {p}: R@5={m[p].recall(5):.3f} R@10={m[p].recall(10):.3f} "
                  f"MRR={m[p].mrr():.3f} ({m[p].row(p)['mean_ms']}ms)", flush=True)

    if not args.only_extra:
        eval_fast("zh_full", zh_full)
    if gs._reranker is None:
        print("RERANKER NOT LOADED — rerank tracks skipped. "
              "Pass --reranker-dir pointing at the cross-encoder model dir.", flush=True)
    else:
        if not args.only_extra:
            eval_rerank("zh_sample", zh_sample)
            eval_rerank("en_sample", en_sample)

    if args.extra_file:
        ex = load_jsonl(Path(args.extra_file))
        ex = [{"gold": i["gold_section"], **{k: i[k] for k in ("id", "task", "question")}} for i in ex]
        eval_fast("extra", ex)
        if gs._reranker is not None:
            eval_rerank("extra", ex)

    # ---- diagnostics ---------------------------------------------------------
    for name, items in (("zh_full", zh_full), ("en_sample", en_sample), ("zh_sample", zh_sample)):
        golds = [i["gold"] for i in items]
        results["oracle"][name] = {
            "n": len(golds),
            "gold_section_in_index": round(sum(1 for g in golds if g in sections_in_index) / len(golds), 4),
        }
    qa_fire = {"zh": 0, "en": 0}
    for it in zh_sample:
        if any(h.get("score", 0) and h["score"] > 0 for h in gs._qa_search(it["question"], 3)):
            qa_fire["zh"] += 1
    for it in en_sample:
        if any(h.get("score", 0) and h["score"] > 0 for h in gs._qa_search(it["question"], 3)):
            qa_fire["en"] += 1
    results["qa_channel"] = {"zh_fired": f"{qa_fire['zh']}/{len(zh_sample)}",
                             "en_fired": f"{qa_fire['en']}/{len(en_sample)}"}

    with open(out_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    with open(out_dir / "misses.jsonl", "w", encoding="utf-8") as f:
        for mrec in results["misses"]:
            f.write(json.dumps(mrec, ensure_ascii=False) + "\n")

    print("\n=== ORACLE (gold section exists in index) ===")
    for k, v in results["oracle"].items():
        print(f"{k}: {v['gold_section_in_index']:.3f} of {v['n']}")
    print("=== QA channel fires ===", results["qa_channel"])
    print("Done. results.json / misses.jsonl written to", out_dir, flush=True)

    svc.stop()


if __name__ == "__main__":
    main()
