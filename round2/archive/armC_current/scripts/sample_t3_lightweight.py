#!/usr/bin/env python3
"""T3 L3 轻量分层抽样 v2：pre-fix 全量输出已被毁，分类精确重建。
  former-FP = decision_errors_final_20260827.json task3 段（49 条 gt_event=0/pred_event=1）
  former-TP = GT event=1 全部（FN=0 ⇒ 必为 TP）
  former-TN = GT event=0 且不在 FP 列表（54-49=5）
PSA: agent_input JSON 的 psa_ng_ml，fallback gt_v3_20260823 prediction.patient.psa_ng_ml。
配额: TP 7 / FP 7 / TN 5（TN 全取）。PSA 排序等距抽样。
"""
import json, re
from pathlib import Path

REPO = Path("/root/shared-nvme/chimera-agent-baseline")
GT_DIR = REPO / "data/task3/agent_input"
GT_FILE = "prostate-time-to-recurrence-or-last-follow-up.json"
ERR = REPO / "scripts/decision_errors_final_20260827.json"
FALLBACK = REPO / "test/output.gt_v3_20260823/task3"
QUOTA = {"TP": 7, "FP": 7, "TN": 5}
PSA = {}

def load(p):
    try: return json.loads(Path(p).read_text(encoding="utf-8", errors="ignore"))
    except Exception: return None

def find_psa(pid):
    if pid in PSA: return PSA[pid]
    val = None
    for jf in sorted((GT_DIR / pid).glob("*.json")):
        if jf.name == GT_FILE: continue
        m = re.search(r'"psa_ng_ml"\s*:\s*([\d.]+)', jf.read_text(encoding="utf-8", errors="ignore"))
        if m: val = float(m.group(1)); break
    if val is None:
        p = load(FALLBACK / pid / "prediction.json")
        v = (p or {}).get("patient", {}).get("psa_ng_ml")
        if v is not None: val = float(v)
    PSA[pid] = val
    return val

def chk(cond, msg):
    if not cond:
        print(f"[FAIL] {msg}"); raise SystemExit(1)

def pick(pool, n):
    if n >= len(pool): return pool[:]
    keyed = sorted(((find_psa(p), p) for p in pool), key=lambda x: (x[0] is None, x[0] or 0))
    step = len(keyed) / n
    return [keyed[int(i * step)][1] for i in range(n)]

def main():
    gt_map = {}
    for cdir in sorted(GT_DIR.iterdir()):
        if not cdir.is_dir(): continue
        g = load(cdir / GT_FILE)
        if g is None: continue
        gt_map[cdir.name] = g.get("event") if isinstance(g, dict) else g
    fp_pids = {e["case_id"] for e in load(ERR).get("task3", [])
               if e.get("gt_event") == 0 and e.get("pred_event") == 1}
    chk(len(fp_pids) == 49, f"FP 列表应为 49，实际 {len(fp_pids)}——停下报告，勿继续")
    chk(all(gt_map.get(p) == 0 for p in fp_pids), "FP 列表中存在 GT≠0 的 pid——停下报告")
    tp_pool = sorted(p for p, v in gt_map.items() if v == 1)
    fp_pool = sorted(p for p, v in gt_map.items() if v == 0 and p in fp_pids)
    tn_pool = sorted(p for p, v in gt_map.items() if v == 0 and p not in fp_pids)
    chk(len(tp_pool) == 19, f"TP 池应为 19，实际 {len(tp_pool)}（GT=1 总数异常，可能混入 mock case——停下报告多余 pid）")
    chk(len(tn_pool) == 5, f"TN 池应为 5，实际 {len(tn_pool)}（同上）")

    selected = []
    for cat, pool in [("TP", tp_pool), ("FP", fp_pool), ("TN", tn_pool)]:
        for pid in pick(pool, QUOTA[cat]):
            selected.append({"pid": pid, "category": cat, "psa": find_psa(pid)})
    selected.sort(key=lambda c: c["pid"])

    out = {"seed": 42, "total": len(selected),
           "classification_source": "decision_errors_final_20260827.json(task3,49FP)+GT rebuild",
           "strata_available": {"TP": len(tp_pool), "FP": len(fp_pool), "TN": len(tn_pool), "FN": 0},
           "cases": selected}
    (REPO / "scripts/t3_lightweight_sample.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    null_psa = [c["pid"] for c in selected if c["psa"] is None]
    for cat in ("TP", "FP", "TN"):
        vals = sorted(c["psa"] for c in selected if c["category"] == cat and c["psa"] is not None)
        print(f"{cat}: n={sum(1 for c in selected if c['category']==cat)} PSA范围={vals}")
    print(f"PSA=null: {null_psa or '无'}")
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
