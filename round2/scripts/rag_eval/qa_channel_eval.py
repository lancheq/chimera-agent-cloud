#!/usr/bin/env python
"""QA-channel comparison: old (183-item lib, BM25) vs new (428-item lib, dense).

Measures, per track (en_sample / t1_en):
  hit@1 / hit@3 : gold section_id appears in top-1 / top-3 QA hits
  top1 sim distribution (dense arm) - to choose the relevance floor

Self-contained: builds both channels directly, independent of rag.py wiring.

Run AFTER deploying the new lib + precomputing embeddings:
  ~/miniconda3/envs/chimera/bin/python qa_channel_eval.py \
      --resource-dir ~/CHIMERA-agent/resources --data-dir . --out qa_channel_results.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path("/home/hedongze/CHIMERA-agent/src")))
from chimera_agent_baseline import rag  # noqa: E402  (reuses _tokenize/_build_bm25/_embed_query)

TOKEN_RE = re.compile(r"^(\d+(?:\.\d+)*)$")


def norm_sec(s):
    s = (s or "").strip()
    m = re.match(r"^(\d+(?:\.\d+)*)", s)
    if m:
        return m.group(1)
    return None


def gold_of(item):
    return item.get("gold_section") or item.get("gold")


def load_jsonl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resource-dir", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", default="qa_channel_results.json")
    args = ap.parse_args()
    res = Path(args.resource_dir)
    data = Path(args.data_dir)

    old_lib = load_jsonl(res / "guideline_qa.jsonl.bak_183") if (res / "guideline_qa.jsonl.bak_183").exists() else []
    if not old_lib:  # not yet deployed: current lib IS the old one
        old_lib = load_jsonl(res / "guideline_qa.jsonl")
        print("NOTE: .bak_183 not found — treating current lib as OLD arm", flush=True)
    new_lib = load_jsonl(data / "qa_kept.jsonl")

    # old gold: guideline_id "EAU-5.4.2.2" -> "5.4.2.2"
    old_gold = [str(q.get("guideline_id", "")).split("-", 1)[-1] for q in old_lib]
    old_gold = [norm_sec(g) or g for g in old_gold]
    new_gold = [str((q.get("grounding") or [{}])[0].get("section_id", "")) for q in new_lib]

    # BM25 over old lib questions (replicates deployed _qa_search)
    bm25 = rag._build_bm25([rag._tokenize(q["question"]) for q in old_lib]) if old_lib else None

    import numpy as np

    vecs_path = data / "qa_new.embeddings.npy"
    if not vecs_path.exists():
        vecs_path = res / "guideline_qa.embeddings.npy"
    vecs = np.load(str(vecs_path)) if vecs_path.exists() else None
    if vecs is None:
        print("WARN: no precomputed embeddings — dense arm unavailable until precompute runs")

    svc = rag.EmbeddingService(str(res / "embedding_model"))

    def qa_hits_bm25(q, k=3):
        if bm25 is None:
            return []
        scores = bm25.get_scores(rag._tokenize(q))
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [(old_gold[i], scores[i]) for i in ranked if scores[i] > 0]

    def qa_hits_dense(q, k=3):
        if vecs is None:
            return []
        qv = np.array(rag._embed_query(q))
        sims = (vecs @ qv) / (np.linalg.norm(vecs, axis=1) * np.linalg.norm(qv))
        order = np.argsort(-sims)[:k]
        return [(new_gold[i], float(sims[i])) for i in order]

    results = {}
    for name, f in (("en_sample", "rag_eval_sample_en.jsonl"), ("t1_en", "rag_eval_t1_en_full.jsonl")):
        p = data / f
        if not p.exists():
            continue
        items = load_jsonl(p)
        stats = {a: {"h1": 0, "h3": 0, "n": 0, "fired": 0} for a in ("bm25_old", "dense_new")}
        top1_sims = []
        for it in items:
            g = gold_of(it)
            for arm, fn in (("bm25_old", qa_hits_bm25), ("dense_new", qa_hits_dense)):
                hits = fn(it["question"], 3)
                s = stats[arm]
                s["n"] += 1
                if hits:
                    s["fired"] += 1
                if hits and hits[0][0] == g:
                    s["h1"] += 1
                if any(h[0] == g for h in hits):
                    s["h3"] += 1
                if arm == "dense_new" and hits:
                    top1_sims.append(hits[0][1])
        for arm in stats:
            s = stats[arm]
            results[f"{name}.{arm}"] = {
                **s,
                "hit@1": round(s["h1"] / s["n"], 4) if s["n"] else None,
                "hit@3": round(s["h3"] / s["n"], 4) if s["n"] else None,
                "fire_rate": round(s["fired"] / s["n"], 4) if s["n"] else None,
            }
        if top1_sims:
            import statistics
            top1_sims.sort()
            results[f"{name}.dense_top1_sim"] = {
                "min": round(top1_sims[0], 3), "p25": round(top1_sims[len(top1_sims)//4], 3),
                "median": round(statistics.median(top1_sims), 3),
                "p75": round(top1_sims[3*len(top1_sims)//4], 3), "max": round(top1_sims[-1], 3),
            }
        print(name, json.dumps({k: v for k, v in results.items() if k.startswith(name)}, ensure_ascii=False), flush=True)

    with open(data / args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("written", data / args.out)
    svc.stop()


if __name__ == "__main__":
    main()
