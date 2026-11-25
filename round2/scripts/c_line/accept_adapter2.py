"""补验收：阶段自洽性 + MCP 返回实际内容。"""

import json
import sys

sys.path.insert(0, "/tmp")
from v2_search_adapter import V2GuidelineSearch

V2 = "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2"
RES = "/home/hedongze/CHIMERA-agent/resources"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
QSRC = "/home/hedongze/CHIMERA-agent/output/planning_C01_retrieval_20261118/query_stages_private.json"

s = V2GuidelineSearch(V2, RES, RER, p1=True, qa=True)
queries = {q["query_id"]: q["historical_query"] for q in json.load(open(QSRC))["queries"]}

print("=== 阶段自洽性")
for qid in sorted(queries):
    st = s.query_debug(queries[qid])
    d = st["dense"]
    b = st["bm25"]
    r = st["rrf"]
    rr = st["rerank"]
    checks = {
        "dense 分数递减": all(d[i]["score"] >= d[i + 1]["score"] for i in range(len(d) - 1)),
        "bm25 分数递减": all(b[i]["score"] >= b[i + 1]["score"] for i in range(len(b) - 1)),
        "rrf 集合覆盖 dense∪bm25": set(x["id"] for x in d) | set(x["id"] for x in b)
                                   == set(x["id"] for x in r),
        "rerank 输入=rrf 前30": [x["id"] for x in rr] and set(x["id"] for x in rr)
                                <= set(x["id"] for x in r[:30]),
        "final=rerank 前5": st["final"] == [x["id"] for x in rr[:5]],
        "p1 与 top5 无重叠": not (set(st["p1_extra"]) & set(st["final"])),
    }
    bad = [k for k, v in checks.items() if not v]
    print(f'  {qid}: ' + ("全部通过" if not bad else "失败 " + str(bad)))

# MCP 返回样例
qid = "Q01"
hits = s.query(queries[qid])
payload = json.dumps({"query": queries[qid], "results": hits}, ensure_ascii=False)
print(f"\n=== MCP 返回样例（{qid}）")
print(f"条目数 {len(hits)}（chunk {sum(1 for h in hits if h['type']=='chunk')} / "
      f"branch {sum(1 for h in hits if h['type']=='branch')} / "
      f"qa {sum(1 for h in hits if h['type']=='qa')}）")
print(f"JSON 长度 {len(payload)} 字符")
print(f"其中正文总字符 {sum(len(h['text']) for h in hits)}")
print("\n前 3 条结构:")
for h in hits[:3]:
    print(f"  type={h['type']:7s} page={h['page']} section={h['section']} "
          f"score={h['score']} chars={len(h['text'])}")
    print(f"    {h['text'][:110].replace(chr(10),' ')}")
print("\nbranch 条目:")
for h in hits:
    if h["type"] == "branch":
        print(f"  section={h['section']} chars={len(h['text'])} :: {h['text'][:80]}")
