#!/bin/bash
# Sequential C-arm smoke runner: one case at a time, same interpreter and
# switches for every case, so per-case cost is comparable across arms.
set -u
cd /home/hedongze/CHIMERA-armC || exit 1
export PYTHONPATH=/home/hedongze/CHIMERA-armC/src
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 LLAMA_API_KEY=bench
export CHIMERA_WEIGHT_TABLE=0
PY=/home/hedongze/venvs/chimera_access/bin/python
# Bind the process that actually owns the listening socket, not a pgrep match.
PID=$(ss -H -ltnp 'sport = :8765' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
echo "endpoint pid=$PID"
for spec in "1 PT-pseudo_48fb60dfd22f" "1 PT-pseudo_152344add0f1" "2 T2-001" "2 T2-008" "3 T3-034" "3 T3-021"; do
  set -- $spec
  task=$1; case=$2
  work="/home/hedongze/CHIMERA-armC/output/bench_work/task$task/$case"
  rm -rf "$work/sockets" "$work/private"
  echo "=== task$task $case ==="
  $PY bench/run_case.py run --task "$task" --case "$case" --pid "$PID" \
      --manifest "/home/hedongze/CHIMERA-armC/output/bench/cases_t$task.json" \
      --run-id "adaptive-bench-t$task-$(date +%H%M%S)" 2>&1 | tail -3
done
