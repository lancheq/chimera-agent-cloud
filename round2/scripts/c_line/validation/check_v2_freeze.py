#!/usr/bin/env python3
"""v2 冻结前三项机检：向量逐条对应、表格 caption 与所属小节、覆盖率分母重算。

只读 v2 产物与 PDF，不改任何文件。产物写到 /tmp/v2_freeze_checks.json。
"""

from __future__ import annotations

import json
import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
PDF = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/raw/eau_2025_prostate.pdf")
EMB = "/home/hedongze/CHIMERA-agent/resources/embedding_model"
QSRC = Path("/home/hedongze/CHIMERA-agent/output/planning_C01_retrieval_20261118/query_stages_private.json")
OUT = Path("/tmp/v2_freeze_checks.json")

CORE_KINDS = ("narrative", "recommendation", "table", "nonclinical")


def load_core():
    recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]
    core = [r for r in recs if r["kind"] in CORE_KINDS]
    return recs, core


# ---------------------------------------------------------------- 1 向量对应

def check_vectors(core, sample=60, seed=20261124):
    ids = [r["id"] for r in core]
    vecs = np.load(V2 / "vectors.npy")
    rep = {"units": len(core), "vectors": int(vecs.shape[0]), "dim": int(vecs.shape[1]),
           "ids_unique": len(set(ids)) == len(ids), "norms": {}}
    norms = np.linalg.norm(vecs, axis=1)
    rep["norms"] = {"min": round(float(norms.min()), 4), "max": round(float(norms.max()), 4),
                    "zero_vectors": int((norms < 1e-6).sum())}

    # 与分片重拼是否一致（分片按文件名排序拼接）
    shards = sorted((V2 / "vec").glob("shard_*.npy"))
    if shards:
        restack = np.vstack([np.load(p) for p in shards]).astype("float32")
        rep["shards"] = {"n": len(shards), "rows": int(restack.shape[0]),
                         "equals_vectors_npy": bool(restack.shape == vecs.shape and np.allclose(restack, vecs))}
        rep["shard_rows"] = [int(np.load(p).shape[0]) for p in shards]

    # 逐条：重新编码同一 retrieval_text，看能否找回自己的向量
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(EMB, device="cpu", local_files_only=True)
    m.max_seq_length = 512
    rng = np.random.default_rng(seed)
    idx = sorted(rng.choice(len(core), size=min(sample, len(core)), replace=False).tolist())
    fresh = m.encode_document([core[i]["retrieval_text"] for i in idx], batch_size=4,
                              show_progress_bar=False).astype("float32")
    vn = vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)
    fn = fresh / (np.linalg.norm(fresh, axis=1, keepdims=True) + 1e-9)

    per = []
    for k, i in enumerate(idx):
        sims = vn @ fn[k]
        order = np.argsort(-sims)
        per.append({"record": ids[i], "self_cos": round(float(sims[i]), 4),
                    "argmax_is_self": bool(order[0] == i),
                    "argmax": ids[int(order[0])], "argmax_cos": round(float(sims[order[0]]), 4),
                    "rank_of_self": int(np.where(order == i)[0][0]) + 1})
    rep["self_check"] = {"sampled": len(idx),
                         "argmax_is_self": sum(p["argmax_is_self"] for p in per),
                         "min_self_cos": min(p["self_cos"] for p in per),
                         "worst_rank_of_self": max(p["rank_of_self"] for p in per),
                         "cases": per}
    return rep


# ---------------------------------------------------------------- 2 表格归属

CAP_NUM = re.compile(r"Table\s+([0-9]+(?:\.[0-9]+)*)", re.I)


def check_tables(core):
    tables = [r for r in core if r["kind"] == "table"]
    sec_pages = defaultdict(list)
    sec_ctx = defaultdict(Counter)
    for r in core:
        if r["kind"] in ("narrative", "recommendation"):
            if isinstance(r.get("page"), int):
                sec_pages[r["section_id"]].append(r["page"])
            for c in r.get("clinical_context") or []:
                sec_ctx[r["section_id"]][c] += 1

    rows = []
    for r in tables:
        cap = r.get("caption") or ""
        m = CAP_NUM.search(cap)
        cap_num = m.group(1) if m else None
        sid = r.get("section_id")
        pages = sec_pages.get(sid) or []
        row = {
            "id": r["id"], "caption": cap[:90], "caption_num": cap_num, "section_id": sid,
            "section_title": r.get("section_title"), "page": r.get("page"),
            "num_matches_section": (cap_num == sid) if cap_num else None,
            "page_in_section_range": (min(pages) <= r["page"] <= max(pages)) if pages and r.get("page") else None,
            "section_page_range": [min(pages), max(pages)] if pages else None,
            "context": r.get("clinical_context"),
            "section_modal_context": (sec_ctx[sid].most_common(1)[0][0] if sec_ctx.get(sid) else None),
        }
        row["context_matches_section"] = (row["context"] == [row["section_modal_context"]]
                                          if row["section_modal_context"] else None)
        rows.append(row)

    known = [r for r in rows if r["caption_num"]]
    summary = {
        "tables": len(rows),
        "caption_number_parsed": len(known),
        "caption_num_equals_section_id": sum(1 for r in rows if r["num_matches_section"]),
        "caption_num_differs": [{"id": r["id"], "caption_num": r["caption_num"], "section_id": r["section_id"],
                                 "section_title": r["section_title"], "page": r["page"]}
                                for r in rows if r["num_matches_section"] is False],
        "page_inside_section_range": sum(1 for r in rows if r["page_in_section_range"]),
        "page_outside_section_range": [{"id": r["id"], "page": r["page"], "range": r["section_page_range"],
                                        "section_id": r["section_id"]} for r in rows
                                       if r["page_in_section_range"] is False],
        "context_unknown": sum(1 for r in rows if "unknown" in (r["context"] or [])),
        "context_disagrees_with_section": [{"id": r["id"], "context": r["context"],
                                            "section_modal": r["section_modal_context"],
                                            "section_id": r["section_id"]} for r in rows
                                           if r["context_matches_section"] is False],
    }
    return summary, rows


def pdf_spotcheck(rows, picks=("6.2.8", "5.8", "3.1", "6.4.3", "5.5.7")):
    """在 PDF 对应页上确认该表确实出现在这一页，并打印该页可见的小节编号。"""
    import fitz
    doc = fitz.open(PDF)
    out = []
    for want in picks:
        cand = [r for r in rows if r["caption_num"] == want and r.get("page")]
        if not cand:
            continue
        r = cand[0]
        page = doc[r["page"] - 1]
        text = page.get_text()
        # 页面上出现的形如 6.2.8 / 5.8 的编号
        nums = sorted(set(re.findall(r"\b([0-9]+\.[0-9]+(?:\.[0-9]+)*)\b", text)))
        cap_line = next((ln for ln in text.splitlines() if CAP_NUM.search(ln)), "")
        out.append({"caption_num": want, "id": r["id"], "page": r["page"],
                    "section_id": r["section_id"], "section_title": r["section_title"],
                    "caption_on_page": cap_line.strip()[:100],
                    "numbers_on_page": nums[:12],
                    "section_id_on_page": r["section_id"] in nums})
    return out


# ---------------------------------------------------------------- 4 分母

def check_coverage(core, retrieved):
    q = json.loads(QSRC.read_text())
    pos = {r["id"]: r for r in core}
    tm = json.load(open("/tmp/abcd_result.json"))["target_map"] if Path("/tmp/abcd_result.json").exists() else {}

    # 旧 id -> v2 id：与 retrieve_v2.py 同一套文本匹配
    def norm(x):
        return re.sub(r"\s+", " ", x).strip().lower()
    by_text = {}
    for r in core:
        by_text.setdefault(norm(r["original_text"]), r["id"])

    out = {}
    for item in q["queries"]:
        qid = item["query_id"]
        src = item.get("source_index_targets") or {}
        old_2025 = src.get("EAU_2025") or []
        old_2026 = src.get("EAU_2026") or []
        mapped = {}
        for oid in old_2025:
            nid = (tm.get(oid) or {}).get("new_id")
            vid = by_text.get(norm(nid)) if nid else None
            if vid is None and nid:
                # 兜底：v2 id 与 v1 id 直接对应关系未知时按 eau-xxxx → v2-xxxx 找不到，用文本相似度不在此重算
                vid = None
            mapped[oid] = vid
        distinct = sorted({v for v in mapped.values() if v})
        ctx = retrieved.get(qid, {})
        covered = ctx.get("correct_covered") or []
        # 现口径（retrieve_v2.py）：固定 5 元素列表、重复项计数
        now = {"numerator": len(covered), "denominator": ctx.get("correct_total")}
        # 修正口径：只算该 query 的 2025 目标、去重、版本可映射
        fixed_num = len([d for d in distinct if d in set(covered)])
        out[qid] = {
            "old_2025": old_2025, "old_2026": old_2026,
            "mapped_v2": mapped, "distinct_v2_targets": distinct,
            "duplicate_old_to_one_v2": len(old_2025) - len(distinct),
            "now": now,
            "fixed": {"numerator": fixed_num, "denominator": len(distinct),
                      "rate": round(fixed_num / len(distinct), 3) if distinct else None},
            "covered_ids": sorted(set(covered)),
            "unmapped_2026_count": len(old_2026),
        }
    return out


def main():
    recs, core = load_core()
    rep = {"records_total": len(recs), "core_units": len(core)}
    print("== 1 向量逐条对应")
    rep["vectors"] = check_vectors(core)
    v = rep["vectors"]
    print(f"   units={v['units']} vectors={v['vectors']} dim={v['dim']} ids_unique={v['ids_unique']} "
          f"zero_vectors={v['norms']['zero_vectors']}")
    print(f"   分片 {v['shards']['n']} 个 → {v['shards']['rows']} 行，与 vectors.npy 一致={v['shards']['equals_vectors_npy']}")
    sc = v["self_check"]
    print(f"   抽样 {sc['sampled']} 条：argmax 命中自身 {sc['argmax_is_self']}/{sc['sampled']}，"
          f"自身余弦最低 {sc['min_self_cos']}，自身最差名次 {sc['worst_rank_of_self']}")

    print("== 2 表格归属")
    tab_sum, tab_rows = check_tables(core)
    rep["tables"] = tab_sum
    print(f"   表 {tab_sum['tables']} 个，caption 解析出编号 {tab_sum['caption_number_parsed']}，"
          f"编号==section_id {tab_sum['caption_num_equals_section_id']}，"
          f"页落在小节范围内 {tab_sum['page_inside_section_range']}，"
          f"context=unknown {tab_sum['context_unknown']}，"
          f"context 与小节不一致 {len(tab_sum['context_disagrees_with_section'])}")
    rep["pdf_spotcheck"] = pdf_spotcheck(tab_rows)
    for s in rep["pdf_spotcheck"]:
        print(f"   [{s['caption_num']}] p{s['page']} section={s['section_id']} "
              f"编号出现在页内={s['section_id_on_page']} caption={s['caption_on_page'][:60]}")

    print("== 4 覆盖率分母")
    retrieved = json.load(open("/tmp/retrieve_v2.json")) if Path("/tmp/retrieve_v2.json").exists() else {}
    cov = check_coverage(core, retrieved)
    rep["coverage"] = cov
    for qid, c in cov.items():
        print(f"   {qid} 现口径 {c['now']['numerator']}/{c['now']['denominator']} "
              f"→ 修正 {c['fixed']['numerator']}/{c['fixed']['denominator']} "
              f"（2025 目标 {len(c['old_2025'])} 个 → 去重 {len(c['distinct_v2_targets'])} 个 v2 单元；"
              f"2026 目标 {c['unmapped_2026_count']} 个无对应）")

    OUT.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str) + "\n")
    print("\n写入", OUT)


if __name__ == "__main__":
    main()
