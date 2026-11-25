#!/usr/bin/env python3
"""B-DATA-SEMANTIC-FIX · 字段重标：把误当成逐条值的任务级旧计数改为明确的任务级字段。

不重算语义，不读取病例内容；只改语义明确性。
"""
import json
import os

OUT = "/home/hedongze/CHIMERA-agent/output/b_data_semantic_fix_20261122"
TASK_OLD_DIFF = {1: 104, 2: 110, 3: 0}


def main():
    path = os.path.join(OUT, "semantic_delta.jsonl")
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    for r in rows:
        r.pop("legacy_same_day_from_sealed_total", None)
        r["legacy_task_level_differing_operations"] = TASK_OLD_DIFF[r["task"]]
        r["legacy_field_note"] = "该值为旧回执的任务级合计，重复记录在每条上；不得读作逐条计数"
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    import hashlib
    ba_path = os.path.join(OUT, "before_after_counts.json")
    ba = json.load(open(ba_path, encoding="utf-8"))
    ba["provenance"]["semantic_delta_sha256"] = hashlib.sha256(open(path, "rb").read()).hexdigest()
    ba.setdefault("post_generation_edits", []).append(
        "11_relabel_delta.py: 删除逐条误挂的任务级旧计数、改名为 legacy_task_level_differing_operations，"
        "并同步 provenance.semantic_delta_sha256；未重算任何语义或计数")
    with open(ba_path, "w", encoding="utf-8") as fh:
        json.dump(ba, fh, ensure_ascii=False, indent=1)
    print("relabelled", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
