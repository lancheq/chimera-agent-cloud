"""L2 directional test: old vs new guidelines DB on gray-zone queries.

Compares the production hybrid pipeline (dense + BM25 + RRF + rerank + QA)
across two resource setups:

  OLD: resources/guidelines_db.bak (1501 EAU-only chunks) + old QA library (24)
  NEW: resources/guidelines_db    (2889 chunks incl. guidelines_unified)
       + expanded QA library (164, DeepSeek-V4-Flash generated)

For each of 10 gray-zone queries, reports top-5 expected-section hit for both
setups, plus whether NEW-content chunks (id prefix ``unified-``) are reachable
in the new DB's recall lists.

Usage:
    cd <project_root>
    PYTHONPATH=src python scripts/test_hybrid_rag.py
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from chimera_agent_baseline.rag import EmbeddingService, GuidelinesSearch

# 10 gray-zone queries with expected section IDs (grounded in EAU section ids)
GRAY_ZONE_QUERIES = [
    {
        "query": "PSA 8 + PI-RADS 3 是否活检？PSAd 0.15 临界值",
        "expect_sections": ["5.4.2.2"],
        "label": "T1: PSA 8 + PI-RADS 3 临界值",
    },
    {
        "query": "PSA 3 + PI-RADS 3 + PSAd 0.07 是否可以不活检",
        "expect_sections": ["5.4.2.2"],
        "label": "T1: 低PSA + PI-RADS 3 不活检",
    },
    {
        "query": "PSA 密度如何计算，什么时候用于活检决策",
        "expect_sections": ["5.2.3", "5.4.2.2"],
        "label": "T1: PSA density 使用场景",
    },
    {
        "query": "55 岁无症状男性要不要做 PSA 筛查，筛查间隔多久",
        "expect_sections": ["5.1.4", "5.1.5"],
        "label": "T1: 人群筛查共同决策",
    },
    {
        "query": "中危 favorably intermediate 能否选主动监测",
        "expect_sections": ["6.3.2.2", "6.3.2.6", "6.3.2"],
        "label": "T2: favorable intermediate AS",
    },
    {
        "query": "高危局部进展期 cT3 GG4 先手术还是先放疗加内分泌",
        "expect_sections": ["6.3.3", "6.3.3.3", "6.3.4"],
        "label": "T2: 高危局部治疗策略",
    },
    {
        "query": "pN1 淋巴结转移术后要不要辅助 RT 和 ADT",
        "expect_sections": ["6.3.5.6.2", "6.3.5.7", "6.3.4.2"],
        "label": "T2: pN1 辅助治疗",
    },
    {
        "query": "术后 PSA 0.1 可测但低于 0.2 如何处理",
        "expect_sections": ["6.4.2", "6.3.6", "6.4.1"],
        "label": "T3: 术后 PSA persistence",
    },
    {
        "query": "BCR 后 salvage RT 最佳时机 PSA 阈值",
        "expect_sections": ["6.4.5", "6.4.5.1"],
        "label": "T3: salvage RT 时机",
    },
    {
        "query": "生化复发后什么时候做 PSMA PET/CT",
        "expect_sections": ["6.4.4.1.5", "6.4.4"],
        "label": "T3: PSMA PET/CT 时机",
    },
]


def check_section_hit(results, expect_sections):
    """Check if any expected section appears in top results."""
    for r in results:
        section = r.get("section", "") or ""
        guideline_id = r.get("guideline_id", "") or ""
        for exp in expect_sections:
            if exp in section or exp in guideline_id:
                return True, section or guideline_id
    return False, ""


def new_content_in_recall(gs, query, k=20):
    """Check whether unified-* (new) chunks appear in dense/bm25 recall lists."""
    ids = set()
    for h in gs._dense_search(query, k):
        ids.add(h["id"])
    for h in gs._bm25_search(query, k):
        ids.add(h["id"])
    return sum(1 for i in ids if i.startswith("unified-"))


def evaluate(gs, top_k=5):
    """Run all queries through the production pipeline; return per-query stats."""
    rows = []
    for q in GRAY_ZONE_QUERIES:
        results = gs.query(q["query"], top_k=top_k)
        hit, matched = check_section_hit(results, q["expect_sections"])
        qa_hit, qa_matched = check_section_hit(
            [r for r in results if r.get("type") == "qa"], q["expect_sections"]
        )
        rows.append(
            {
                "label": q["label"],
                "hit": hit,
                "matched": matched,
                "qa_hit": qa_hit,
                "qa_matched": qa_matched if qa_hit else "",
                "n_results": len(results),
            }
        )
    return rows


def setup_old_resource_dir(tmp: Path) -> Path:
    """Build a resource dir pointing at the OLD DB + OLD QA library."""
    old = tmp / "old_resources"
    old.mkdir(parents=True)
    (old / "guidelines_db").symlink_to(PROJECT_ROOT / "resources" / "guidelines_db.bak")
    shutil.copy(
        PROJECT_ROOT / "resources" / "guideline_qa.jsonl.bak24",
        old / "guideline_qa.jsonl",
    )
    reranker = PROJECT_ROOT / "resources" / "reranker_model"
    if reranker.exists():
        (old / "reranker_model").symlink_to(reranker)
    return old


def main():
    tmp = Path(tempfile.mkdtemp(prefix="rag_eval_"))
    resource_dir = PROJECT_ROOT / "resources"

    embed_svc = EmbeddingService(str(resource_dir / "embedding_model"))
    try:
        print("Initializing NEW setup (2889 chunks + QA 164)...")
        t0 = time.time()
        gs_new = GuidelinesSearch(resource_dir)
        print(f"  chunks={len(gs_new._chunks)} qa={len(gs_new._qa)} init={time.time()-t0:.1f}s")

        print("Initializing OLD setup (1501 chunks + QA 24)...")
        old_dir = setup_old_resource_dir(tmp)
        t0 = time.time()
        gs_old = GuidelinesSearch(old_dir)
        print(f"  chunks={len(gs_old._chunks)} qa={len(gs_old._qa)} init={time.time()-t0:.1f}s")

        print("\n" + "=" * 100)
        print("Gray-zone top-5 section hit: OLD (1501 chunks, QA 24) vs NEW (2889 chunks, QA 164)")
        print("=" * 100)

        rows_old = evaluate(gs_old)
        rows_new = evaluate(gs_new)

        print(f"{'Query':38s} | {'OLD':4s} | {'NEW':4s} | NEW matched section / QA source")
        print("-" * 100)
        old_hits = 0
        new_hits = 0
        new_content_seen = 0
        for q, ro, rn in zip(GRAY_ZONE_QUERIES, rows_old, rows_new):
            old_hits += ro["hit"]
            new_hits += rn["hit"]
            if rn["hit"]:
                new_content_seen += 1
            detail = rn["matched"][:40] if rn["hit"] else "-"
            qa_mark = f" [QA:{rn['qa_matched'][:24]}]" if rn["qa_hit"] else ""
            print(
                f"{q['label'][:38]:38s} | {'HIT ' if ro['hit'] else 'miss'} "
                f"| {'HIT ' if rn['hit'] else 'miss'} | {detail}{qa_mark}"
            )

        n = len(GRAY_ZONE_QUERIES)
        print("-" * 100)
        print(f"  OLD top-5 section hit rate : {old_hits}/{n} ({old_hits / n * 100:.0f}%)")
        print(f"  NEW top-5 section hit rate : {new_hits}/{n} ({new_hits / n * 100:.0f}%)")

        # New-content reachability: unified chunks in recall lists
        print("\nNew-content reachability (unified-* chunks in top-20 recall):")
        for q in GRAY_ZONE_QUERIES:
            cnt = new_content_in_recall(gs_new, q["query"])
            print(f"  {'HIT' if cnt > 0 else '   '} unified-chunks={cnt:2d} | {q['label'][:50]}")

        verdict = "PASS" if new_hits >= old_hits else "CHECK"
        print(f"\nVERDICT: NEW ({new_hits}/{n}) vs OLD ({old_hits}/{n}) -> {verdict}")
    finally:
        embed_svc.stop()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
