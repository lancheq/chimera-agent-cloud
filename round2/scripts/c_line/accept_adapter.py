"""v2 适配器验收：6 条历史 query 逐阶段检查。

对比基准：
- final top5 与 P1 新增 → /tmp/retrieve_v2.json（冻结点之前的离线实验）
- 各阶段规模与传递自洽性 → 适配器内部
- 编码一致性 → 本地编码两次（确定性），若 socket 可用则与 socket 比对

差异预期：P1 上限从当时的不限改为 2 小节，可能出现条目数差异，需逐条说明。
"""

import json
import sys

import numpy as np

sys.path.insert(0, "/tmp")
from v2_search_adapter import V2GuidelineSearch

V2 = "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2"
RES = "/home/hedongze/CHIMERA-agent/resources"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
QSRC = "/home/hedongze/CHIMERA-agent/output/planning_C01_retrieval_20261118/query_stages_private.json"

queries = {q["query_id"]: q["historical_query"] for q in json.load(open(QSRC))["queries"]}
base = json.load(open("/tmp/retrieve_v2.json"))

s = V2GuidelineSearch(V2, RES, RER, p1=True, qa=True)
print(f"units={len(s.units)} qa={len(s.qa)} p1={s.use_p1}", flush=True)

# 编码一致性：两次本地编码
e1 = s._encode("prostate biopsy indication")
e2 = s._encode("prostate biopsy indication")
cos = float(e1 @ e2 / (np.linalg.norm(e1) * np.linalg.norm(e2) + 1e-9))
print(f"encode determinism cos={cos:.6f}", flush=True)

report = {}
for qid in sorted(queries):
    st = s.query_debug(queries[qid])
    b = base.get(qid, {})
    base_top5 = b.get("top5", [])
    base_ctx = b.get("ctx_ids", [])
    base_p1 = [i for i in base_ctx if i not in base_top5]
    same_top5 = st["final"] == base_top5
    same_p1 = st["p1_extra"] == base_p1
    report[qid] = {
        "counts": st["counts"],
        "final": st["final"],
        "base_top5": base_top5,
        "same_top5": same_top5,
        "p1": st["p1_extra"],
        "base_p1": base_p1,
        "same_p1": same_p1,
        "qa_n": len(st["qa"]),
        "rerank_top3": [(x["id"], x["score"]) for x in st["rerank"][:3]],
    }
    print(f'\n{qid}  dense={st["counts"]["dense"]} bm25={st["counts"]["bm25"]} '
          f'rrf={st["counts"]["rrf"]} rerank={st["counts"]["rerank"]} '
          f'final={st["counts"]["final"]} p1={st["counts"]["p1"]} qa={len(st["qa"])}')
    print(f'  top5 一致: {same_top5}')
    if not same_top5:
        print(f'    adapter: {st["final"]}')
        print(f'    offline: {base_top5}')
    print(f'  P1 一致: {same_p1}  adapter={st["p1_extra"]}  offline={base_p1}')

ok5 = sum(1 for q in report if report[q]["same_top5"])
okp = sum(1 for q in report if report[q]["same_p1"])
print(f"\n=== 验收汇总")
print(f"final top5 一致: {ok5}/6")
print(f"P1 新增一致  : {okp}/6")
print(f"QA 条数: " + ", ".join(f"{q}={report[q]['qa_n']}" for q in report))

json.dump(report, open("/tmp/adapter_acceptance.json", "w"), ensure_ascii=False, indent=1)
print("\nWROTE /tmp/adapter_acceptance.json")
