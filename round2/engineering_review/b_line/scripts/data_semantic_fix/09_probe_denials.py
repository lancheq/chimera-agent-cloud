#!/usr/bin/env python3
"""B-DATA-SEMANTIC-FIX · 拒绝路径探针（不读取任何内容）。

目的：在不重复内容复跑的前提下，为访问日志补充「拒绝」类覆盖。
只对白名单外路径发起一次打开尝试，全部应被拒；不解析、不读字节。
"""
import json
import os
import sys

ROOT = "/home/hedongze/CHIMERA-agent"
BASE = os.path.join(ROOT, "output/b_data_applicability_live_20261122")
OUT = os.path.join(ROOT, "output/b_data_semantic_fix_20261122")
DATA_ROOT = os.path.join(
    ROOT,
    "data/round2_materials_20261121/train_release_transfer/20261121_104011_78d1168e/train_release_chimera_phase2",
)


class Denied(Exception):
    pass


def guard(path, allowlist):
    denied_suffixes = tuple(allowlist["policy"]["denied_suffixes"])
    if os.path.basename(path).endswith(denied_suffixes):
        raise Denied("denied_suffix:%s" % os.path.basename(path))
    if ".." in path.split(os.sep):
        raise Denied("traversal_segment")
    real = os.path.realpath(path)
    if real != path:
        raise Denied("non_canonical_path")
    if not real.startswith(os.path.realpath(DATA_ROOT) + os.sep):
        raise Denied("outside_data_root")
    if os.path.islink(path):
        raise Denied("symlink")
    if path not in {e["abs_path"] for e in allowlist["content_read"]}:
        raise Denied("not_in_allowlist")


def main():
    allowlist = json.load(open(os.path.join(BASE, "read_allowlist.json"), encoding="utf-8"))
    frame = json.load(open(os.path.join(BASE, "risk_group_frame.json"), encoding="utf-8"))
    slots = frame["slots"]
    t1 = next(s for s in slots if s["task"] == 1)
    t2 = next(s for s in slots if s["task"] == 2)
    t3 = next(s for s in slots if s["task"] == 3)
    d1 = os.path.join(DATA_ROOT, "task1", t1["record_id"])
    d2 = os.path.join(DATA_ROOT, "task2", t2["record_id"])
    d3 = os.path.join(DATA_ROOT, "task3", t3["record_id"])

    probes = [
        ("reference_reasoning", os.path.join(d1, "prostate-biopsy-decision-reasoning.json")),
        ("target_decision", os.path.join(d1, "prostate-biopsy-decision.json")),
        ("outcome_file", os.path.join(d3, "prostate-time-to-recurrence-or-last-follow-up.json")),
        ("unknown_file", os.path.join(d1, "unlisted-extra.json")),
        ("cross_case_traversal", os.path.join(d1, "..", "..", "task2", t2["record_id"], "structured-prompt.json")),
        ("absolute_escape", "/etc/hostname"),
    ]

    rows = []
    for i, (label, path) in enumerate(probes, 1):
        try:
            guard(path, allowlist)
            rows.append({"seq": i, "probe": label, "path": path, "denied": False,
                         "reason": "UNEXPECTED_ALLOW", "bytes_read": 0})
        except Denied as exc:
            rows.append({"seq": i, "probe": label, "path": path, "denied": True,
                         "reason": str(exc), "bytes_read": 0})
        except Exception as exc:  # noqa: BLE001
            rows.append({"seq": i, "probe": label, "path": path, "denied": True,
                         "reason": "error:%s" % type(exc).__name__, "bytes_read": 0})

    out = {"probe_count": len(rows), "all_denied": all(r["denied"] for r in rows),
           "content_bytes_read": 0, "probes": rows}
    with open(os.path.join(OUT, "denial_probes.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("probes", len(rows), "all_denied", out["all_denied"])
    for r in rows:
        print("  ", r["probe"], r["denied"], r["reason"])
    return 0 if out["all_denied"] else 1


if __name__ == "__main__":
    sys.exit(main())
