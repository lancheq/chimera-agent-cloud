#!/bin/bash
# S2 · 修复后有效消融（per-task prompt + P5 + P6 + S1 代码下）— 顺序执行双臂
# Arm A: self_refine=false, atomic_check=true
# Arm B: self_refine=true,  atomic_check=true, self_refine_max_iter=2
# 双关闭 predictor；各臂独立 output 目录 + CHIMERA_OUTPUT_DIR
# 顺序执行（双臂并行会与 35B 冲突，违反项目铁律）
set -e
cd /root/shared-nvme/chimera-agent-baseline

PIDS=$(.venv/bin/python -c "import json; print('['+','.join(json.load(open('scripts/ablation_cases.json'))['pids'])+']')")
echo "30 pids: $PIDS"

run_arm () {
  ARM=$1; SELF_REFINE=$2; MAX_ITER=$3
  OUT=test/output.s2_$ARM
  mkdir -p "$OUT"
  CHIMERA_OUTPUT_DIR="/root/shared-nvme/chimera-agent-baseline/$OUT" \
  .venv/bin/python -m chimera_agent_baseline.run \
    '+experiment=qwen_local' \
    'agent.tasks=[1,2,3]' \
    "agent.pids=$PIDS" \
    'agent.predictor.enabled=false' \
    'agent.predictor.decision_override_tasks=[]' \
    "agent.form_fill.self_refine=$SELF_REFINE" \
    "agent.form_fill.self_refine_max_iter=$MAX_ITER" \
    'agent.form_fill.atomic_check=true' \
    "paths.output_dir=$OUT" \
    > s2_${ARM}_run.log 2>&1
  echo "== arm $ARM done ($(date +%T)) =="
}

echo "== Arm A start $(date +%T) =="
run_arm A false 2
echo "== Arm B start $(date +%T) =="
run_arm B true 2
echo "ALL_ARMS_DONE"
