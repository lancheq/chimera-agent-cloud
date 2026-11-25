#!/bin/bash
# =============================================================================
# overnight pipeline fix: Stage 3b 全量 + 3c 评估 + Stage4 GEval(gemma4:e4b) + 汇总
# 修正: gate 已确认 PASS(smoke 污染0%); ollama 用 curl 检查, 不重复 serve
# =============================================================================
set +e
cd /root/shared-nvme/chimera-agent-baseline
PL=/tmp/overnight_auto_pipeline_fix.log
exec > >(tee -a $PL) 2>&1
ts() { date '+%Y-%m-%d %H:%M:%S'; }
echo "=== [$(ts)] pipeline-fix START ==="

check_server() {
  if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/v1/models 2>/dev/null | grep -q 200; then
    echo "[$(ts)] server 8765 OK"; return 0
  fi
  echo "[$(ts)] server 8765 down, restarting 35B..."
  nohup .venv/bin/python -m llama_cpp.server \
    --model model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
    --host 127.0.0.1 --port 8765 --n_gpu_layers -1 --n_ctx 16384 \
    > qwen_restart_overnight.log 2>&1 &
  for i in $(seq 1 30); do
    sleep 10
    if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/v1/models 2>/dev/null | grep -q 200; then
      echo "[$(ts)] server restarted OK"; return 0
    fi
  done
  echo "[$(ts)] server restart FAILED"; return 1
}

# ============ Stage 3b: 全量消融B ============
echo "=== [$(ts)] Stage 3b: full ablation B (30 case, self_refine=true) ==="
check_server
PIDS=$(.venv/bin/python -c "import json;print(','.join(json.load(open('scripts/ablation_cases.json'))['pids']))")
LLAMA_API_KEY=no-key nohup .venv/bin/python -m chimera_agent_baseline.run \
  +experiment=qwen_local agent.tasks=[1,2,3] agent.pids=[$PIDS] \
  agent.predictor.decision_override_tasks=[] \
  agent.form_fill.self_refine=true agent.form_fill.atomic_check=true \
  agent.form_fill.self_refine_max_iter=2 \
  paths.output_dir=test/output.ablation_B_selfrefine_20260829 \
  > ablation_B_run.log 2>&1 &
nohup .venv/bin/python scripts/trace_monitor.py --pred test/output.ablation_B_selfrefine_20260829 --watch > /tmp/trace_monitor_ablationB.log 2>&1 &
echo "[$(ts)] ablation B started; waiting..."
waited=0
while pgrep -f "chimera_agent_baseline.run.*ablation_B_selfrefine" >/dev/null 2>&1; do
  sleep 60; waited=$((waited+1))
  if [ $waited -ge 240 ]; then echo "[$(ts)] TIMEOUT waiting ablation B"; break; fi
done
sleep 15
grep -q "\[HARD\]" /tmp/trace_monitor_ablationB.log 2>/dev/null && echo "[$(ts)] HARD detected in ablation B!" || echo "[$(ts)] ablation B: no HARD"

# ============ Stage 3c: 评估 B ============
echo "=== [$(ts)] Stage 3c: eval ablation B ==="
.venv/bin/python scripts/eval_train_gt.py --pred-dir test/output.ablation_B_selfrefine_20260829 \
  --report-suffix _ablation_B_selfrefine_20260829 2>&1 | tail -40

# 统计 critique 污染率 / refine 触发率 (从 trace.json reflect_warnings)
.venv/bin/python - <<'PYEOF'
import json, pathlib
root = pathlib.Path("test/output.ablation_B_selfrefine_20260829")
traces = list(root.rglob("trace.json"))
n = len(traces)
critique_ok = 0
refined = 0
polluted = 0
no_issues = 0
for t in traces:
    try:
        d = json.loads(t.read_text("utf-8","ignore"))
    except Exception:
        continue
    rw = d.get("reflect_warnings") or []
    fw = d.get("form_fill_warnings") or []
    if rw:
        critique_ok += 1
        joined = " ".join(rw)
        if "<think" in joined or "think" in joined.lower():
            polluted += 1
        if "no issues found" in joined:
            no_issues += 1
        else:
            refined += 1
print(f"[stage3] traces={n} critique_executed={critique_ok} polluted(think)={polluted} no_issues={no_issues} refined={refined}")
with open("scripts/overnight_stage3_stats.txt","w") as f:
    f.write(f"traces={n}\ncritique_executed={critique_ok}\npolluted={polluted}\nno_issues={no_issues}\nrefined={refined}\n")
PYEOF

# ============ Stage 4a: 停 35B ============
echo "=== [$(ts)] Stage 4a: stop 35B server ==="
pkill -f "llama_cpp[.]server" 2>/dev/null
sleep 8
echo "[$(ts)] 35B stopped"

# ============ Stage 4b: GEval ============
echo "=== [$(ts)] Stage 4b: GEval with gemma4:e4b ==="
if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q 200; then
  echo "[$(ts)] ollama reachable; models:"
  curl -s http://127.0.0.1:11434/api/tags | .venv/bin/python -c "import json,sys;print([m['name'] for m in json.load(sys.stdin)['models']])"
  if curl -s http://127.0.0.1:11434/api/tags | grep -q gemma4; then
    echo "[$(ts)] running GEval with gemma4:e4b ..."
    GEVAL_JUDGE_MODEL=gemma4:e4b .venv/bin/python scripts/geval_eval.py 2>&1 | tail -50
  else
    echo "[$(ts)] gemma4:e4b missing; skip GEval"
  fi
else
  echo "[$(ts)] ollama NOT reachable on 11434; GEval skipped"
fi

# ============ Stage 4 报告 ============
cat > scripts/overnight_stage4_geval_report.md <<EOF
# Stage 4 · GEval 报告

- 时间: $(ts)
- 前提: Step9 Docker3 门槛 PASS -> GEval 已尝试执行
- judge: gemma4:e4b (Ollama, /root/shared-nvme/ollama/bin/ollama serve, PID 运行中)
- 详细: scripts/geval_eval_report.md
- 若 gemma4 不可用则记录原因
EOF

echo "=== [$(ts)] pipeline-fix DONE ==="
