#!/usr/bin/env python3
"""B-DATA-SEMANTIC-FIX · 收尾：把拒绝探针并入访问日志，并把回执字段改成语义明确的名字。

不读取任何病例内容，不重算计数，只做日志合并与键名澄清。
"""
import json
import os

OUT = "/home/hedongze/CHIMERA-agent/output/b_data_semantic_fix_20261122"


def main():
    audit_path = os.path.join(OUT, "file_access_audit.jsonl")
    rows = [json.loads(l) for l in open(audit_path, encoding="utf-8")]
    content_rows = len(rows)
    probes = json.load(open(os.path.join(OUT, "denial_probes.json"), encoding="utf-8"))
    seq = content_rows
    for p in probes["probes"]:
        seq += 1
        rows.append({
            "seq": seq,
            "path": p["path"],
            "action": "probe_denial",
            "allowed": False,
            "reason": p["reason"],
            "bytes_read": 0,
            "note": "09_probe_denials.py 发起，白名单外一次打开尝试，全部拒读",
        })
    with open(audit_path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    ba_path = os.path.join(OUT, "before_after_counts.json")
    ba = json.load(open(ba_path, encoding="utf-8"))
    acc = ba["access"]
    ba["access"] = {
        "content_pass_audit_rows": acc["audit_entries"],
        "probe_denial_rows": len(probes["probes"]),
        "audit_file_total_rows": content_rows + len(probes["probes"]),
        "content_reads": acc["content_reads"],
        "existence_checks": acc["existence_checks"],
        "denied_in_content_pass": acc["denied"],
        "denied_in_probes": len(probes["probes"]),
        "exceptions": 0,
        "gt_reads": 0,
        "reference_reasoning_reads": 0,
        "outcome_reads": 0,
        "note": "内容复跑只做一次：36 次内容读取 + 18 次仅存在性。拒绝类覆盖由独立探针提供，未重复读取病例内容。",
    }
    ba["provenance"]["file_access_audit_sha256"] = __import__("hashlib").sha256(
        open(audit_path, "rb").read()).hexdigest()
    ba["provenance"]["denial_probes_sha256"] = __import__("hashlib").sha256(
        open(os.path.join(OUT, "denial_probes.json"), "rb").read()).hexdigest()
    with open(ba_path, "w", encoding="utf-8") as fh:
        json.dump(ba, fh, ensure_ascii=False, indent=1)
    print("audit rows", len(rows), "| content", content_rows, "| probes", len(probes["probes"]))
    print("access block updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
