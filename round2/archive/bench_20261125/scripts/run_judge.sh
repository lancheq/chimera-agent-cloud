#!/bin/bash
# Full rationale-judge pass over both arms. Judge is the local qwen2.5:7b because
# gemma4:e4b is not in the local Ollama store; both arms use the same judge, so the
# comparison stays internally valid even though absolute scores are not leaderboard-comparable.
set -u
G=/home/hedongze/CHIMERA-armC/output/bench/gc
cd /home/hedongze/CHIMERA-agent/CHIMERA-agent/evaluation || exit 1
echo $$ > /home/hedongze/CHIMERA-armC/output/bench/judge.pid
for arm in A C; do
  for tk in 1 2; do
    T=task$tk
    echo "=== judge arm$arm $T $(date +%H:%M:%S) ==="
    PYTHONPATH=/tmp/ollamapkg TASK_ID=$T \
      GROUND_TRUTH_DIR=$G/gt/$T \
      PREDICTIONS_FILE=$G/arm$arm/$T/predictions.json \
      PK_CASE_MAP_FILE=$G/arm$arm/$T/pk_hash_to_case_map.json \
      EVAL_OUTPUT_DIR=$G/eval_judge/arm$arm/$T \
      USE_RATIONALE_JUDGE=1 JUDGE_MODEL=qwen2.5:7b OLLAMA_BASE_URL=http://127.0.0.1:11434 \
      /home/hedongze/venvs/chimera_access/bin/python evaluate.py 2>&1 | grep -E "Mean rationale|Final case score|Mean case score"
  done
done
echo "=== judge done $(date +%H:%M:%S) ==="
