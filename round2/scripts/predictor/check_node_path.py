#!/usr/bin/env python3
"""节点路径 vs 账本：predictor_node.revealed_sections 还原出的可见段落，和账本差多少。

这条路径决定了"真接进图里"时 Predictor 能拿到什么。已知嫌疑：
SECTION_KEY_TO_CD 只列了 6 个段落，没有 surgical_pathology_report。
这里用 C 臂真实的 reveal_sequence（提交口径里记录的实际揭示顺序）做对照，
不猜、不构造。
"""

import json
import sys
from collections import Counter
from pathlib import Path

ARM = Path("/home/hedongze/CHIMERA-armC")
sys.path.insert(0, str(ARM / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from chimera_agent_baseline.output.schema import TOOL_REVEAL_INFO  # noqa: E402

from eval_predictor_masked import (  # noqa: E402
    ALL_SECTIONS, CASELIST, arm_output_dir, visible_sections,
)

SECTION_KEY_TO_CD = {  # 抄自 agent/predictor_node.py
    "section_s3-mri": "radiology_report",
    "section_s3-psa": "psa_trend",
    "section_s3-labs": "laboratory_results",
    "section_s3-prev": "previous_notes",
    "section_s3-fh": "family_history",
    "section_s3-path": "pathology_report",
}


def main():
    print("工具 -> reveal key 全表：")
    for tool, (key, _label) in sorted(TOOL_REVEAL_INFO.items()):
        print(f"  {tool:38s} {key:22s} -> {SECTION_KEY_TO_CD.get(key, '【节点路径未映射】')}")

    summary = {}
    for task in (1, 2, 3):
        cases = json.loads((ARM / "scripts" / CASELIST[task]).read_text())
        lost = Counter()
        n_diff = 0
        examples = []
        for case in cases:
            out = arm_output_dir(task, case)
            ledger, _ = visible_sections(out / "information_access.sqlite")
            seq = json.loads((out / "prediction.json").read_text()).get("reveal_sequence") or []
            node = {SECTION_KEY_TO_CD.get(item.get("key")) for item in seq}
            node.discard(None)
            if node != ledger:
                n_diff += 1
                for sec in ledger - node:
                    lost[sec] += 1
                if len(examples) < 3:
                    examples.append({"case": case, "ledger": sorted(ledger), "node": sorted(node)})
        summary[task] = {"cases": len(cases), "cases_where_node_differs_from_ledger": n_diff,
                         "sections_lost_by_node": dict(lost), "examples": examples}
        print(f"\ntask{task}: {n_diff}/{len(cases)} 例节点还原与账本不一致；丢失段落 {dict(lost)}")
        for e in examples:
            print("   ", e)

    out = Path("/home/hedongze/CHIMERA-agent/output/predictor_replay_20261124/node_path.json")
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print("\n写入", out)


if __name__ == "__main__":
    main()
