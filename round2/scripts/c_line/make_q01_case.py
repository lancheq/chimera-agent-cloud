"""生成 Q01 完整真实检索案例（原始 Query / 各阶段排名 / 目标证据去向）。"""

import json
import sqlite3

BASE = "/home/hedongze/CHIMERA-agent/output"
DB = "/home/hedongze/CHIMERA-agent/resources/guidelines_db/chroma.sqlite3"
OUT = "/tmp/Q01_case.md"

stages = json.load(open(f"{BASE}/planning_C01_frozen_retrieval_replay_20261118/Q01_stages_private.json"))
qmeta = {q["query_id"]: q for q in json.load(open(f"{BASE}/planning_C01_retrieval_20261118/query_stages_private.json"))["queries"]}
Q = qmeta["Q01"]

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
texts = dict(con.execute(
    "select e.embedding_id, m.string_value from embeddings e "
    "join embedding_metadata m on m.id = e.id where m.key='chroma:document'").fetchall())
con.close()


def snip(rid, n=95):
    t = (texts.get(rid) or "").replace("\n", " ")
    return t[:n].strip()


def stage_table(rows, cols=("rank", "record_id", "indexed_page", "dense_score", "bm25_score", "rrf_score", "rerank_score")):
    out = ["| # | record_id | page | dense | bm25 | rrf | rerank | 文本开头 |", "|---:|---|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        vals = []
        for c in cols[3:]:
            v = r.get(c)
            vals.append("" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v)))
        out.append(
            f"| {r.get('rank','')} | `{r.get('record_id','')}` | {r.get('indexed_page','')} | "
            + " | ".join(vals) + f" | {snip(r.get('record_id',''))} |"
        )
    return "\n".join(out)


L = []
L.append("# Q01 完整真实检索案例\n")
L.append(f"**Query**：`{Q['historical_query']}`\n")
L.append(f"**病例**：`{Q['historical_trace_binding']['run_directory'].split('/')[-1]}`"
         f"（task1，{Q['historical_trace_binding']['worker']}，fixed 步）\n")
L.append(f"**A01 事实核查**：`{Q['A01_query_fact_check']}` — {Q['A01_query_fact_note']}\n")
dt = Q.get("decision_target", {})
L.append(f"**决策目标**：{dt.get('documentary_target','')}\n")
L.append(f"**合法事实**：{dt.get('legal_facts','')}\n")

L.append("\n## 一、阶段规模\n")
L.append(f"- dense 召回：{len(stages['dense_top_20'])} 条")
L.append(f"- BM25 召回：{len(stages['bm25_top_20'])} 条")
L.append(f"- 候选并集（两路 20+20 去重）：{len(stages['candidate_union_insertion_order'])} 条")
L.append(f"- RRF 全序：{len(stages['rrf_full_order'])} 条，取前 30 进入重排")
L.append(f"- 进入重排：{len(stages['rerank_input_after_top_30'])} 条")
L.append(f"- 重排输出：{len(stages['rerank_full_order'])} 条")
qa_pos = [p for p in (stages.get('final_formatted_positions') or []) if p.get('channel') != 'chunk']
L.append(f"- 最终正文：{len(stages['final_chunk_top_5'])} 条 + QA {len(qa_pos)} 条（合计 {len(stages.get('final_formatted_positions') or [])} 个位置）")
L.append(f"- 检索耗时：{stages['retrieval_wall_seconds']:.1f} 秒\n")

L.append("\n## 二、Dense 前 20\n")
L.append(stage_table(stages["dense_top_20"]))
L.append("\n## 三、BM25 前 20\n")
L.append(stage_table(stages["bm25_top_20"]))
L.append("\n## 四、RRF 前 15（共 36）\n")
L.append(stage_table(stages["rrf_full_order"][:15]))
L.append("\n## 五、重排前 12（共 36）\n")
L.append(stage_table(stages["rerank_full_order"][:12]))

L.append("\n## 六、最终送达模型的 5+3 条\n")
L.append(stage_table(stages["final_chunk_top_5"]))
L.append("\n| # | QA record | score |\n|---:|---|---:|")
for p in qa_pos:
    L.append(f"| {p.get('rank')} | `{p.get('record_id','')}` | {p.get('score','')} |")

L.append("\n## 七、目标证据的去向\n")
det = json.load(open(f"{BASE}/planning_C01_retrieval_20261118/query_stages_private.json"))["target_index_check_details"]
pos = {}
for name, rows in (
    ("dense", stages["dense_top_20"]),
    ("bm25", stages["bm25_top_20"]),
    ("rrf", stages["rrf_full_order"]),
    ("rerank", stages["rerank_full_order"]),
    ("final", stages["final_chunk_top_5"]),
):
    for r in rows:
        pos.setdefault(r["record_id"], {})[name] = r.get("rank")

for tid, meta in det.items():
    p = pos.get(tid, {})
    L.append(f"\n### `{tid}` — {meta.get('review','')}\n")
    L.append(f"- 索引状态：`{meta.get('snapshot_status')}`　页码：{meta.get('page')}　"
             f"section：`{meta.get('section_metadata','')}`")
    L.append(f"- 阶段名次：dense {p.get('dense','未进前20')} / bm25 {p.get('bm25','未进前20')} / "
             f"rrf {p.get('rrf','未进前36')} / rerank {p.get('rerank','—')} / final {p.get('final','未送达')}")
    L.append(f"\n> {(texts.get(tid) or '（库内无此 id）')[:420]}\n")

L.append("\n## 八、结论\n")
L.append(f"**stage_localization**：{Q.get('stage_localization','')}\n")
L.append(f"**引用核查**：{Q.get('diagnosis_citation_or_application_review','')}\n")

open(OUT, "w", encoding="utf-8").write("\n".join(L))
print("wrote", OUT, len("\n".join(L)), "chars")
