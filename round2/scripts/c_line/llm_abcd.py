"""35B 标准证据四组实验（方向二）。

A 只有患者资料
B 患者资料 + 旧库真实检索返回（trace 里那条 8000 字符）
C 患者资料 + 人工核实的完整指南原文（§5.5.7 十条推荐逐条）
D 患者资料 + 与 C 医学内容相同、但带 ID/小节/强度字段的结构化版本

固定：模型、患者资料、输出要求、temperature=0、top_p。
"""

import argparse
import json
import time
from pathlib import Path

import requests

API = "http://127.0.0.1:8765/v1/chat/completions"
MODEL = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
REC = "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124/records.jsonl"
CASES = "/tmp/case_inputs.json"

ap = argparse.ArgumentParser()
ap.add_argument("--case", default="PT-pseudo_562159dc2cc6")
ap.add_argument("--section", default="5.5.7")
ap.add_argument("--reps", type=int, default=2)
ap.add_argument("--max-tokens", type=int, default=1600)
ap.add_argument("--out", default="/tmp/llm_abcd.json")
ap.add_argument("--groups", default="A,B,C,D,E")
ap.add_argument("--e-file", default="/tmp/e_evidence.json")
a = ap.parse_args()

recs = [json.loads(l) for l in open(REC)]
sec_recs = [r for r in recs if r.get("section_id") == a.section and r["kind"] == "recommendation"]
sec_narr = [r for r in recs if r.get("section_id") == a.section and r["kind"] == "narrative"]
print(f"section {a.section}: {len(sec_recs)} recommendations, {len(sec_narr)} narratives", flush=True)

cases = json.load(open(CASES))
case = cases[a.case]
human = case["human"]

# 患者资料 = human + 已获取的临床文档（PSA 趋势/影像/化验），不含检索返回
tools = case["tool_returns"]
patient_tools = [t for t in tools if '"query"' not in t]
rag_tools = [t for t in tools if '"query"' in t]
b_text = max(rag_tools, key=len) if rag_tools else ""
patient_ctx = human + "\n\n--- Retrieved clinical documents ---\n" + "\n".join(patient_tools)
print("patient docs:", len(patient_tools), "chars", len(patient_ctx),
      "| RAG evidence chars:", len(b_text), flush=True)
print("radiology has PI-RADS:", "PI-RADS" in " ".join(patient_tools), flush=True)

# C 组：完整原文逐条
c_text = "\n".join(
    f"- {r['text'].strip()} [Strength: {r['strength']}]" for r in sec_recs
)
# D 组：同内容 + 字段标签
title = sec_recs[0].get("section_title", "") if sec_recs else ""
d_text = "\n\n".join(
    f"[{r['id']}]\nSection: {a.section} {title}\nStatement: {r['text'].strip()}\n"
    f"Strength: {r['strength']}\nSource: EAU 2025"
    for r in sec_recs
)

TAIL = (
    "\n\nAll patient information available to you has already been retrieved; you cannot call tools "
    "in this session. Based on the material above, give your final assessment now: "
    "(1) the PI-RADS category and risk factors you actually rely on, "
    "(2) whether a biopsy is indicated and why, "
    "(3) which guideline statement you applied, quoting the condition that made it applicable, "
    "(4) anything you consider unknown and would not assume. "
    "Be concise; no tool calls."
)

E_EVID = ""
try:
    e_all = json.load(open(a.e_file))
    key = a.case if a.case in e_all else a.case.replace("_adaptive", "")
    E_EVID = e_all.get(key, {}).get("evidence", "")
    print("E evidence chars:", len(E_EVID), "| items:", e_all.get(key, {}).get("n_items"), flush=True)
except Exception as exc:
    print("no E evidence:", exc, flush=True)

GROUPS = {
    "A_no_evidence": patient_ctx + TAIL,
    "B_old_rag": patient_ctx + "\n\n--- Retrieved guideline passages ---\n" + b_text + TAIL,
    "C_gold_verbatim": patient_ctx + "\n\n--- Full guideline recommendations (EAU 2025) ---\n" + c_text + TAIL,
    "D_gold_structured": patient_ctx + "\n\n--- Structured guideline evidence (EAU 2025) ---\n" + d_text + TAIL,
    "E_new_kb_p1": patient_ctx + "\n\n--- Retrieved guideline passages (new KB, branch-completed) ---\n" + E_EVID + TAIL,
}
want = [g.strip().upper() for g in a.groups.split(",")]
GROUPS = {k: v for k, v in GROUPS.items() if k[0] in want}

out = {"case": a.case, "section": a.section, "evidence": {
    "patient_chars": len(patient_ctx), "B_chars": len(b_text),
    "C_chars": len(c_text), "D_chars": len(d_text), "n_recs": len(sec_recs)}, "runs": []}

for gname, prompt in GROUPS.items():
    for rep in range(a.reps):
        t0 = time.time()
        r = requests.post(API, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": a.max_tokens,
            "temperature": 0.0,
            "top_p": 0.95,
            "seed": 20261124,
            "chat_template_kwargs": {"enable_thinking": False},
        }, timeout=600)
        r.raise_for_status()
        j = r.json()
        msg = j["choices"][0]["message"]
        content = msg.get("content") or ""
        out["runs"].append({
            "group": gname, "rep": rep, "seconds": round(time.time() - t0, 1),
            "chars": len(content),
            "content": content,
            "reasoning": (msg.get("reasoning_content") or "")[:2000],
            "usage": j.get("usage", {}),
        })
        print(f'{gname} rep{rep} {len(content)} chars {time.time()-t0:.1f}s', flush=True)

Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
print("WROTE", a.out, flush=True)
