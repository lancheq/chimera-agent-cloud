#!/bin/bash
# N1-P2 · T2 全量重跑（P6 验证口径）— TASK-0831-FULL
# 用法: bash scripts/run_t2_full_p6.sh
# 前置: 8765 35B server 就绪（GPU 由用户终端启动）
set -e
cd /root/shared-nvme/chimera-agent-baseline

OUT=test/output.t2_full_p6_20260831
mkdir -p "$OUT"
export CHIMERA_OUTPUT_DIR="/root/shared-nvme/chimera-agent-baseline/$OUT"

PIDS=$(.venv/bin/python -c "import json; print('['+','.join(json.load(open('scripts/t2_full_72_gt_pids.json')))+']')")
echo "72 pids: $PIDS"

nohup .venv/bin/python -m chimera_agent_baseline.run \
  '+experiment=qwen_local' \
  'agent.tasks=[2]' \
  "agent.pids=$PIDS" \
  'agent.predictor.enabled=false' \
  'agent.predictor.decision_override_tasks=[]' \
  'agent.form_fill.self_refine=false' \
  'agent.form_fill.atomic_check=false' \
  "paths.output_dir=$OUT" \
  > t2_full_p6_run.log 2>&1 &

echo "T2 P6 run launched pid $!"
echo "log: t2_full_p6_run.log"
