#!/usr/bin/env python3
"""
trace_monitor.py — 运行期内联监视器（v3）
模式:
  --watch        边跑边监（与主 run 并行）
  无 --watch     事后审计（对已有目录整体扫描）
四维健康检查: 结构 / 数值 / 行为 / 分布
用法:
  python trace_monitor.py --pred test/output.gt_v3_20260823 --watch --interval 60
  python trace_monitor.py --pred test/output.gt_v3_20260823
"""

import argparse, json, re, sys, time
from collections import Counter, deque
from pathlib import Path

THINKING_PATTERNS = [
    "here's a thinking process", "here is a thinking process",
    "analyze user input", "let's analyze the provided",
    "the user wants me to audit", "i need to check for:",
    "i'll analyze", "let me review",
]

GT_FILES = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}

def load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None

def pred_decision(task, pred):
    if task == 1: return pred.get("biopsy_decision")
    if task == 2: return (pred.get("treatment_recommendation") or {}).get("primary")
    return pred.get("event")

def gt_decision(task, gt):
    if task == 1: return gt
    if task == 2: return gt
    return gt.get("event") if isinstance(gt, dict) else gt

def extract_psa_vol(free_text, patient):
    """从 free_text + patient 提取 PSA/vol，用于 PSAD 重算校验。"""
    psa = None; vol = None
    m = re.search(r"PSA[:\s]+(\d+\.?\d*)", free_text, re.I)
    if m: psa = float(m.group(1))
    m = re.search(r"(?:volume|vol)[:\s]*(\d+\.?\d*)", free_text, re.I)
    if m: vol = float(m.group(1))
    if psa is None and patient: psa = patient.get("psa")
    return psa, vol

class Monitor:
    def __init__(self, pred_dir, data_dir, window=20, calibrate=10):
        self.pred_dir = Path(pred_dir)
        self.data_dir = Path(data_dir)
        self.window_size = window
        self.calibrate = calibrate
        self.window = deque(maxlen=window)
        self.alerts = []
        self.stats = Counter()
        self.gt_dec_dist = {1: Counter(), 2: Counter(), 3: Counter()}
        self._load_gt_dist()

    def _load_gt_dist(self):
        for task, fn in GT_FILES.items():
            root = self.data_dir / f"task{task}" / "agent_input"
            if not root.is_dir(): continue
            for c in root.iterdir():
                gf = c / fn
                if gf.exists() and (gt := load(gf)) is not None:
                    self.gt_dec_dist[task][str(gt_decision(task, gt))] += 1

    def observe(self, task, case_dir, data_dir) -> list[tuple[str, str]]:
        """对单个 case 做四维检查，返回 [(level, issue)]"""
        out = []
        cdir = Path(case_dir)
        if (cdir / ".failed").exists():
            out.append(("HARD", "case failed"))
            return out
        pred = load(cdir / "prediction.json")
        if pred is None:
            out.append(("HARD", "prediction.json 不可解析"))
            return out

        # ── A 结构健康 ──
        if task == 1:
            for f in ("biopsy_decision", "confidence", "variable_weights", "reveal_sequence", "free_text"):
                if f not in pred: out.append(("HARD", f"缺字段 {f}"))
        elif task == 2:
            for f in ("treatment_recommendation", "confidence", "variable_weights", "reveal_sequence", "free_text"):
                if f not in pred: out.append(("HARD", f"缺字段 {f}"))
        else:
            for f in ("event", "months_to_recurrence", "reveal_sequence", "free_text"):
                if f not in pred: out.append(("HARD", f"缺字段 {f}"))
        if not pred.get("reveal_sequence"):
            out.append(("HARD", "reveal_sequence 为空"))
        ft = pred.get("free_text") or ""
        if len(ft) < 40:
            out.append(("HARD", f"free_text 过短 ({len(ft)})"))

        # ── B 数值健康 ──
        psa, vol = extract_psa_vol(ft, pred.get("patient"))
        if psa and vol:
            psad_claim = re.search(r"PSA\s*density[:\s]*(\d+\.?\d*)", ft, re.I)
            if psad_claim:
                calc = round(psa / vol, 3)
                claimed = float(psad_claim.group(1))
                if abs(claimed - calc) > 0.01 * max(calc, 0.01):
                    out.append(("HARD", f"PSAD 声称 {claimed} 但 PSA/{vol} = {calc}"))
        if task == 3 and pred.get("event") == 1:
            # BCR 阈值: free_text 提到 PSA 且 < 0.2 却判复发
            m = re.search(r"PSA[:\s]+(0\.[0-9]+)", ft)
            if m and float(m.group(1)) < 0.2 and "0.2" in ft and "nadir" not in ft:
                out.append(("SOFT", f"PSA {m.group(1)} 判复发，疑似阈值误用"))

        # ── C 行为健康 ──
        tr = cdir / "trace"
        if tr.is_dir():
            polluted = 0
            for cf in tr.glob("critique_iter*.json"):
                raw = (load(cf) or {}).get("critique_raw_full", "")[:200].lower()
                if any(p in raw for p in THINKING_PATTERNS):
                    polluted += 1
            if polluted:
                out.append(("SOFT", f"critique thinking 污染 ×{polluted}"))
            refined = tr / "refined_iter1.txt"
            if refined.exists() and refined.stat().st_size < 50:
                out.append(("SOFT", "refine 输出过短（疑似未真正改写）"))
            plan = load(tr / "plan.json")
            if plan:
                sel, exe = plan.get("selected_tools") or [], plan.get("executed_tools") or []
                forced = [t for t in exe if t not in sel]
                if forced:
                    out.append(("SOFT", f"工具被强制添加: {forced}"))
        # confidence-证据匹配
        conf = pred.get("confidence")
        if task in (1, 2) and conf == "uncertain":
            w = pred.get("variable_weights") or {}
            if w and all(v == "decisive" for v in w.values()):
                out.append(("SOFT", "uncertain 但全部 decisive（校准矛盾）"))

        # ── D 分布健康（返回快照供滚动统计）──
        self.window.append({
            "task": task, "case": Path(cdir).name,
            "decision": pred_decision(task, pred),
            "confidence": conf,
        })
        return out

    def check_drift(self):
        """滚动窗口 vs GT 分布漂移检测。"""
        if len(self.window) < self.calibrate:
            return []
        issues = []
        for task in (1, 2, 3):
            win = [s for s in self.window if s["task"] == task]
            if len(win) < 10: continue
            gt = self.gt_dec_dist[task]
            if not gt: continue
            cur = Counter(s["decision"] for s in win)
            for k, v in cur.items():
                gk = gt.get(k, 0)
                if gk and v / len(win) > 2 * (gk / sum(gt.values())) + 0.15:
                    issues.append(f"Task{task} 决策分布漂移: {k}={v}/{len(win)} vs GT {gk}")
        win12 = [s for s in self.window if s["task"] in (1, 2)]
        if len(win12) >= 10:
            unc = sum(1 for s in win12 if s["confidence"] == "uncertain")
            if unc / len(win12) > 0.35:
                issues.append(f"uncertain 比例过高: {unc}/{len(win12)} (GT ≈16%)")
        return issues

    def run(self, watch=False, interval=60):
        print(f"Monitor start: {self.pred_dir}  (watch={watch})")
        while True:
            found = 0
            for task_dir in sorted(self.pred_dir.glob("task*")):
                task = int(task_dir.name.replace("task", ""))
                for cdir in sorted(task_dir.iterdir()):
                    if not cdir.is_dir(): continue
                    marker = cdir / ".monitored"
                    if marker.exists(): continue
                    if not (cdir / "prediction.json").exists():
                        continue
                    marker.touch()
                    found += 1
                    issues = self.observe(task, cdir, self.data_dir)
                    for lv, msg in issues:
                        self.alerts.append((cdir.name, lv, msg))
                        prefix = "[HARD]" if lv == "HARD" else "[soft]"
                        print(f"{prefix} {cdir.name}: {msg}", flush=True)
                    if not issues:
                        self.stats["clean"] += 1
                    else:
                        self.stats["has_issue"] += 1
                    for lv, _ in issues:
                        self.stats[lv] += 1
                    for d in self.check_drift():
                        self.alerts.append(("__drift__", "SOFT", d))
                        print(f"[DRIFT] {d}", flush=True)
                    self._flush_report()
            if not watch:
                break
            if found:
                print(f"  ... {found} new case(s) processed, total {self.stats['clean']+self.stats['has_issue']}", flush=True)
            time.sleep(interval)

    def _flush_report(self):
        lines = ["# Monitor Report", f"- 已分析: {self.stats['clean']+self.stats['has_issue']} cases",
                 f"- 干净: {self.stats['clean']} / 有问题: {self.stats['has_issue']}",
                 f"- HARD: {self.stats.get('HARD',0)} / SOFT: {self.stats.get('SOFT',0)}", ""]
        for cid, lv, msg in self.alerts[-100:]:
            lines.append(f"- [{lv}] {cid}: {msg}")
        Path("monitor_report.md").write_text("\n".join(lines))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default="test/output.gt_v3_20260823")
    ap.add_argument("--data", default="data")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    a = ap.parse_args()
    Monitor(a.pred, a.data).run(watch=a.watch, interval=a.interval)
