"""抽取三个 C01 病例的诊断输入（方向二：标准证据四组实验的基座）。

每个病例输出：
  human       —— 最初的患者上下文（含阅读表单，未获取信息在工具后）
  tool_returns—— 各工具真实返回（含旧库 search_guidelines 的返回）
  final_ai    —— 历史最终输出（仅作参考，不当 ground truth）
  case_id / task
"""

import json
from pathlib import Path

ROOT = Path("/home/hedongze/CHIMERA-agent/output/s4_shared_plan_performance_20261115_v2r1")
CASES = {
    "PT-pseudo_562159dc2cc6": "worker0/fixed/task1/PT-pseudo_562159dc2cc6",
    "PT-pseudo_562159dc2cc6_adaptive": "worker0/adaptive/task1/PT-pseudo_562159dc2cc6",
    "PT-pseudo_4f33b0253633": "worker1/fixed/task1/PT-pseudo_4f33b0253633",
    "PT-pseudo_4f33b0253633_adaptive": "worker1/adaptive/task1/PT-pseudo_4f33b0253633",
    "PT-pseudo_c39e94a13920": "worker1/fixed/task1/PT-pseudo_c39e94a13920",
    "PT-pseudo_c39e94a13920_adaptive": "worker1/adaptive/task1/PT-pseudo_c39e94a13920",
}

out = {}
for name, rel in CASES.items():
    p = ROOT / rel / "trace.json"
    if not p.exists():
        print("missing", p)
        continue
    t = json.loads(p.read_text())
    msgs = t.get("messages", [])
    human = next((m["content"] for m in msgs if isinstance(m, dict) and m.get("type") == "human"), "")
    tools = []
    for m in msgs:
        if isinstance(m, dict) and m.get("type") == "tool":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            tools.append(str(c))
    final_ai = ""
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("type") == "ai":
            c = m.get("content")
            final_ai = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
            break
    out[name] = {
        "case_id": t.get("case_id"),
        "task": t.get("task"),
        "status": t.get("status"),
        "human": human,
        "tool_returns": tools,
        "final_ai": final_ai,
        "elapsed_seconds": t.get("elapsed_seconds"),
    }
    print(name, "human", len(human), "tools", len(tools), "final", len(final_ai))

Path("/tmp/case_inputs.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
print("WROTE /tmp/case_inputs.json", len(out), "cases")
