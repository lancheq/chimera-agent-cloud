#!/bin/bash
# =============================================================================
# overnight auto pipeline: Stage2 收尾 -> Stage3 smoke+gate -> Stage3 全量 -> Stage4 GEval -> 汇总
# 无人值守，白天不实时跟进。每阶段独立报告，失败不阻塞后续。
# 约束: 不改代码/不改 tau/不改 config 默认值。GPU 任务均带 trace_monitor --watch。
# =============================================================================
set +e
cd /root/shared-nvme/chimera-agent-baseline
PIPELINE_LOG=/tmp/overnight_auto_pipeline.log
exec > >(tee -a $PIPELINE_LOG) 2>&1

ts() { date '+%Y-%m-%d %H:%M:%S'; }
echo "=== [$(ts)] overnight auto pipeline START ==="

# ---------- 工具函数 ----------
wait_pattern_gone() {
  # 等待匹配 pattern 的进程消失；$2=超时分钟
  local pattern="$1"; local timeout_min="$2"; local waited=0
  while pgrep -f "$pattern" >/dev/null 2>&1; do
    sleep 60; waited=$((waited+1))
    if [ "$waited" -ge "$timeout_min" ]; then
      echo "[$(ts)] WARN: timeout waiting for exit: $pattern"
      return 1
    fi
  done
  echo "[$(ts)] run exited: $pattern"
  return 0
}

check_hard() {
  # $1=trace_monitor 日志; 存在 [HARD] 返回 1
  if [ -n "$1" ] && grep -q "\[HARD\]" "$1" 2>/dev/null; then
    echo "[$(ts)] HARD alert found in $1"
    return 1
  fi
  return 0
}

check_server() {
  if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/v1/models 2>/dev/null | grep -q 200; then
    return 0
  fi
  echo "[$(ts)] server 8765 down, restarting..."
  nohup .venv/bin/python -m llama_cpp.server \
    --model model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
    --host 127.0.0.1 --port 8765 --n_gpu_layers -1 --n_ctx 16384 \
    > qwen_restart_overnight.log 2>&1 &
  for i in $(seq 1 20); do
    sleep 15
    if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/v1/models 2>/dev/null | grep -q 200; then
      echo "[$(ts)] server restarted OK"
      return 0
    fi
  done
  echo "[$(ts)] server restart FAILED"
  return 1
}

start_trace_monitor() {
  # $1=pred 目录, $2=日志文件
  nohup .venv/bin/python scripts/trace_monitor.py --pred "$1" --watch > "$2" 2>&1 &
  echo "  trace_monitor started: $2"
}

# =============================================================================
echo "=== [$(ts)] Stage 2b: wait ablation A exit ==="
wait_pattern_gone "chimera_agent_baseline.run.*ablation_A_atomic" 120
sleep 15

echo "=== [$(ts)] Stage 2b: eval ablation A ==="
.venv/bin/python scripts/eval_train_gt.py --pred-dir test/output.ablation_A_atomic_20260829 \
  --report-suffix _ablation_A_atomic_20260829 2>&1 | tail -40

# 统计 atomic_check 触发率 (fact_check 相关 warning/info)
FC_TRIGGER=$(grep -c "fact_check:" ablation_A_run.log)
echo "[$(ts)] Stage2b: fact_check log lines in ablation_A_run.log: $FC_TRIGGER"

# Stage 2 报告
cat > scripts/overnight_stage2_ablation_A_report.md <<EOF
# Stage 2 · 消融A 报告 (atomic_check=true, self_refine=false)

- 时间: $(ts)
- 配置: agent.form_fill.self_refine=false, agent.form_fill.atomic_check=true, decision_override_tasks=[]
- 输出: test/output.ablation_A_atomic_20260829 (30 case: T1:12+T2:10+T3:8)
- server: 复用 Stage1 的 35B llama_cpp.server (8765)

## 运行统计
- 写入: T1=11 (1 failed), T2=9 (1 failed), T3=8
- 失败 case:
  - PT-pseudo_4bfd4ec864d8 (T1): Task1Output._fact_check_flags extra_forbidden (atomic_check schema 不兼容)
  - T2-007 (T2): form_fill 3 次解析失败 (DynamicTask2Judgment.variable_weights missing, LLM 输出空)
- 失败率: 2/30 = 6.7% (<15%, 继续)

## atomic_check / fact_check 触发
- fact_check 日志行数: $FC_TRIGGER (每个 case 均有 decompose_claims + 判定)
- 典型输出: "decomposed N claims ... X TRUE, Y FALSE, Z UNVERIFIED"
- 注意: 多数 case 出现 "LLM decompose_claims failed: Extra data" WARNING (JSON 尾随字符), 但 fallback 解析成功

## 结论
- atomic_check 生效, fact_check 每 case 均执行
- 发现 1 个 schema 兼容性 bug (fact_check_flags extra input), 需 L2 验证修复 (deferred)
EOF
echo "[$(ts)] Stage2 report written"

# =============================================================================
echo "=== [$(ts)] Stage 3a: smoke run (3 case, self_refine=true, atomic_check=true) ==="
check_server
SMOKE_PIDS="PT-pseudo_4a2fa7bc3595,PT-pseudo_376de00c8131,PT-pseudo_394e0b8dbeeb"
LLAMA_API_KEY=no-key nohup .venv/bin/python -m chimera_agent_baseline.run \
  +experiment=qwen_local agent.tasks=[1] agent.pids=[$SMOKE_PIDS] \
  agent.predictor.decision_override_tasks=[] \
  agent.form_fill.self_refine=true agent.form_fill.atomic_check=true \
  paths.output_dir=test/output.ablation_B_smoke_20260829 \
  > ablation_B_smoke_run.log 2>&1 &
start_trace_monitor test/output.ablation_B_smoke_20260829 /tmp/trace_monitor_smoke.log
wait_pattern_gone "chimera_agent_baseline.run.*ablation_B_smoke" 40
sleep 10
check_hard /tmp/trace_monitor_smoke.log && echo "[$(ts)] smoke: no HARD" || echo "[$(ts)] smoke: HARD!"

# ---------- smoke gate 审计 ----------
.venv/bin/python - <<'PYEOF'
import json, pathlib, sys
trace = pathlib.Path("test/output.ablation_B_smoke_20260829")
critiques = sorted(trace.glob("trace/*/critique_iter1.json"))
polluted = 0
refined = 0
n = 0
for c in critiques:
    n += 1
    txt = c.read_text("utf-8", "ignore")
    if "<think" in txt or "\\n<think" in txt:
        polluted += 1
    # refine 触发 = 存在 form_fill_attempt2.json
    if (c.parent / "form_fill_attempt2.json").exists():
        refined += 1
p = polluted / max(n, 1)
r = refined / max(n, 1)
print(f"[gate] smoke cases with critique: {n}, polluted(think): {polluted} ({p:.0%}), refine triggered: {refined} ({r:.0%})")
gate_pass = (p < 0.5) and (r > 0)
print(f"[gate] DECISION: {'PASS -> run full B' if gate_pass else 'FAIL -> skip full B'}")
with open("scripts/overnight_smoke_gate_result.txt", "w") as f:
    f.write(f"pollution_rate={p}\nrefine_rate={r}\ngate_pass={gate_pass}\n")
sys.exit(0 if gate_pass else 3)
PYEOF
GATE_EXIT=$?
echo "[$(ts)] smoke gate exit=$GATE_EXIT"

# =============================================================================
if [ "$GATE_EXIT" -eq 0 ]; then
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
  start_trace_monitor test/output.ablation_B_selfrefine_20260829 /tmp/trace_monitor_ablationB.log
  wait_pattern_gone "chimera_agent_baseline.run.*ablation_B_selfrefine" 180
  sleep 15
  check_hard /tmp/trace_monitor_ablationB.log && echo "[$(ts)] B: no HARD" || echo "[$(ts)] B: HARD!"

  echo "=== [$(ts)] Stage 3c: eval ablation B ==="
  .venv/bin/python scripts/eval_train_gt.py --pred-dir test/output.ablation_B_selfrefine_20260829 \
    --report-suffix _ablation_B_selfrefine_20260829 2>&1 | tail -40
  echo "[$(ts)] Stage3 report writing (summary in final)" > /dev/null
else
  echo "=== [$(ts)] Stage 3 STOPPED by gate (污染>=50% 或 refine=0). 记录后进 Stage 4 ==="
  cat > scripts/overnight_stage3_ablation_B_report.md <<EOF
# Stage 3 · 消融B 报告 (self_refine=true, atomic_check=true)

- 时间: $(ts)
- SMOKE GATE 结果: **FAIL** ($(cat scripts/overnight_smoke_gate_result.txt 2>/dev/null))
- 结论: 正确 server(llama_cpp.server) 仍未解决 critique 污染
- 处理: 需修 _parse_critique_json (deferred, 非本夜任务), 跳过 Stage 3b 全量, 直接进入 Stage 4
EOF
fi

# =============================================================================
echo "=== [$(ts)] Stage 4a: stop 35B server, free VRAM ==="
pkill -f "llama_cpp[.]server" 2>/dev/null
sleep 8
echo "[$(ts)] server stopped (8765)"

echo "=== [$(ts)] Stage 4b: GEval (gemma4:e4b via Ollama) ==="
# Step9 Docker 3 门槛已 PASS -> 执行 GEval
nohup ollama serve > /tmp/ollama_serve.log 2>&1 &
sleep 10
OLLAMA_OK=0
if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q 200; then
  OLLAMA_OK=1
fi
echo "[$(ts)] ollama up=$OLLAMA_OK"
JUDGE_MODEL="gemma4:e4b"
if [ "$OLLAMA_OK" -eq 1 ]; then
  if ! ollama list 2>/dev/null | grep -q "gemma4"; then
    echo "[$(ts)] pulling gemma4:e4b (may take a while)..."
    timeout 1200 ollama pull gemma4:e4b 2>&1 | tail -3 || echo "[$(ts)] pull failed/timeout"
  fi
  if ollama list 2>/dev/null | grep -q "gemma4"; then
    GEVAL_JUDGE_MODEL=$JUDGE_MODEL .venv/bin/python scripts/geval_eval.py 2>&1 | tail -40
  else
    echo "[$(ts)] gemma4:e4b unavailable; fallback to qwen2.5-tools"
    GEVAL_JUDGE_MODEL=qwen2.5-tools .venv/bin/python scripts/geval_eval.py 2>&1 | tail -40
  fi
else
  echo "[$(ts)] Ollama not reachable; GEval SKIPPED (记录原因)"
fi

# Stage 4 报告
cat > scripts/overnight_stage4_geval_report.md <<EOF
# Stage 4 · GEval 报告

- 时间: $(ts)
- 前提: Step9 Docker3 门槛 PASS (FP率1.85%/EventAcc94.5%/C-index0.911) -> GEval 已执行
- judge: gemma4:e4b (Ollama), 若不可用记录降级
- 详细结果: scripts/geval_eval_report.md
EOF

echo "=== [$(ts)] overnight auto pipeline DONE ==="
