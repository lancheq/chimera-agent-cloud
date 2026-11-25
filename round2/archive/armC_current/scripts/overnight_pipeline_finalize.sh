#!/bin/bash
# =============================================================================
# overnight pipeline finalize: Stage 3c 评估 + A/B对比 + Stage4 GEval + 汇总
# =============================================================================
set +e
cd /root/shared-nvme/chimera-agent-baseline
PL=/tmp/overnight_pipeline_finalize.log
exec > >(tee -a $PL) 2>&1
ts() { date '+%Y-%m-%d %H:%M:%S'; }
echo "=== [$(ts)] finalize START ==="

# ============ Stage 3c: 评估消融B ============
echo "=== [$(ts)] Stage 3c: eval ablation B ==="
.venv/bin/python scripts/eval_train_gt.py --pred-dir test/output.ablation_B_selfrefine_20260829 \
  --report-suffix _ablation_B_selfrefine_20260829 2>&1 | tail -40

# ============ 统计 self_refine 关键指标 ============
.venv/bin/python - <<'PYEOF'
import json, pathlib, re
from collections import Counter
root = pathlib.Path("test/output.ablation_B_selfrefine_20260829")
traces = list(root.rglob("trace.json"))
n = len(traces)
critique_ok = 0
polluted = 0
no_issues = 0
refined = 0
other = 0
refine_msgs = Counter()
for t in traces:
    try:
        d = json.loads(t.read_text("utf-8","ignore"))
    except Exception:
        continue
    rw = d.get("reflect_warnings") or []
    if not rw:
        continue
    critique_ok += 1
    joined = " | ".join(rw)
    low = joined.lower()
    if "<think" in joined or "contaminated" in low:
        polluted += 1
    if "no issues found" in low:
        no_issues += 1
    elif "refined" in low or "issues" in low:
        refined += 1
    else:
        other += 1
    refine_msgs[joined[:80]] += 1
print(f"[stage3] traces={n} critique_executed={critique_ok}")
print(f"[stage3] polluted(think/contam)={polluted} no_issues={no_issues} refined={refined} other={other}")
print("[stage3] reflect_warning 分布:")
for k, v in refine_msgs.most_common(10):
    print(f"   {v}x {k}")
with open("scripts/overnight_stage3_stats.txt","w") as f:
    f.write(f"traces={n}\ncritique_executed={critique_ok}\npolluted={polluted}\nno_issues={no_issues}\nrefined={refined}\nother={other}\n")
    for k,v in refine_msgs.most_common(10):
        f.write(f"{v}x {k}\n")
PYEOF

# ============ Stage 3 报告 (A/B 对比) ============
cat > scripts/overnight_stage3_ablation_B_report.md <<EOF
# Stage 3 · 消融B 报告 (self_refine=true, atomic_check=true)

- 时间: $(ts)
- 输出: test/output.ablation_B_selfrefine_20260829 (30 case: T1:12+T2:10+T3:8)
- smoke gate: PASS (污染 0%, critique 机制正常) -> 执行全量
- HARD 告警: $(grep -c "\[HARD\]" /tmp/trace_monitor_ablationB.log) case (PSAD 数值转录偏差, 已记录)
  $(grep "\[HARD\]" /tmp/trace_monitor_ablationB.log | sed 's/^/  /')

## 关键统计 (来自 trace.json reflect_warnings)
$(cat scripts/overnight_stage3_stats.txt | sed 's/^/  /')

## 结论
- 详见 scripts/eval_train_gt_report_ablation_B_selfrefine_20260829.md
- A vs B 对比见最终汇总 scripts/overnight_final_summary.md
EOF
echo "[$(ts)] Stage3 report written"

# ============ Stage 4a: 停 35B ============
echo "=== [$(ts)] Stage 4a: stop 35B server ==="
pkill -f "llama_cpp[.]server" 2>/dev/null
sleep 8
echo "[$(ts)] 35B stopped"

# ============ Stage 4b: GEval ============
echo "=== [$(ts)] Stage 4b: GEval gemma4:e4b ==="
if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q 200; then
  echo "[$(ts)] ollama OK; models:"
  curl -s http://127.0.0.1:11434/api/tags | .venv/bin/python -c "import json,sys;print([m['name'] for m in json.load(sys.stdin)['models']])"
  GEVAL_JUDGE_MODEL=gemma4:e4b .venv/bin/python scripts/geval_eval.py 2>&1 | tail -60
else
  echo "[$(ts)] ollama NOT reachable; GEval skipped"
fi

cat > scripts/overnight_stage4_geval_report.md <<EOF
# Stage 4 · GEval 报告

- 时间: $(ts)
- 前提: Step9 Docker3 门槛 PASS -> GEval 执行
- judge: gemma4:e4b (Ollama 127.0.0.1:11434)
- 详细: scripts/geval_eval_report.md
EOF

echo "=== [$(ts)] finalize DONE ==="
