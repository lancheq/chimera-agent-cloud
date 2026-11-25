import json
import re

QSRC = "/home/hedongze/CHIMERA-agent/output/planning_C01_retrieval_20261118/query_stages_private.json"
qs = {q["query_id"]: q for q in json.load(open(QSRC))["queries"]}

print("########## Q05 decision_target")
dt = qs["Q05"].get("decision_target", {})
print(json.dumps(dt, ensure_ascii=False, indent=1)[:1200])
print("\nQ05 fact:", qs["Q05"].get("A01_query_fact_check"), "|", qs["Q05"].get("A01_query_fact_note"))
print("\n########## Q03 decision_target")
dt3 = qs["Q03"].get("decision_target", {})
print(json.dumps(dt3, ensure_ascii=False, indent=1)[:900])

print("\n\n########## Q05 C/D/E 输出")
d = json.load(open("/tmp/llm_c39e.json"))
for r in d["runs"]:
    if r["group"] in ("C_gold_verbatim", "D_gold_structured", "E_new_kb_p1"):
        print(f'\n----- {r["group"]} rep{r["rep"]}')
        print(r["content"])

print("\n\n########## Q03 E 组输出")
d3 = json.load(open("/tmp/llm_4f33.json"))
for r in d3["runs"]:
    if r["group"] == "E_new_kb_p1":
        print(f'\n----- E rep{r["rep"]}')
        print(r["content"])
