#!/bin/bash
# N1-P3 · 官方 evaluate.py 三任务完整评分（gemma4:e4b judge）
set -e
cd /root/shared-nvme/chimera-agent-baseline
EV=data_backup/upload_evaluation_official/CHIMERA-agent/evaluation
OUT=test/eval_official_20260831
mkdir -p "$OUT"

export OLLAMA_BASE_URL=http://127.0.0.1:11434
export JUDGE_MODEL=gemma4:e4b
export USE_RATIONALE_JUDGE=1
export ALLOW_MODEL_PULL=0

run_task () {
  T=$1; GT=$2; PRED=$3
  mkdir -p "$OUT/$T"
  (cd "$EV" && TASK_ID="$T" \
    GROUND_TRUTH_DIR="/root/shared-nvme/chimera-agent-baseline/test/official_gt_20260831/$GT" \
    PREDICTIONS_FILE="/root/shared-nvme/chimera-agent-baseline/$PRED/predictions.json" \
    PK_CASE_MAP_FILE="/root/shared-nvme/chimera-agent-baseline/$PRED/pk_hash_to_case_map.json" \
    SECTION_MAPPING_FILE="/root/shared-nvme/chimera-agent-baseline/test/official_gt_20260831/section_variable_mapping.json" \
    EVAL_OUTPUT_DIR="/root/shared-nvme/chimera-agent-baseline/$OUT/$T" \
    /root/shared-nvme/chimera-agent-baseline/test/eval_venv/bin/python evaluate.py \
    > "/root/shared-nvme/chimera-agent-baseline/$OUT/${T}_run.log" 2>&1)
  echo "== $T done =="
}

run_task task1 task1 test/input_gc_t1p6
run_task task2 task2 test/input_gc_t2p6
run_task task3 task3 test/input_gc_t3p6

# gemma 用毕卸载
curl -s -m 10 http://127.0.0.1:11434/api/generate -d '{"model":"gemma4:e4b","keep_alive":0}' > /dev/null 2>&1 || true
echo "ALL_TASKS_DONE"
