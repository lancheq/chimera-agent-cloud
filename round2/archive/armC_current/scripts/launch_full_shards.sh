#!/bin/bash
# =============================================================================
# launch_full_shards.sh —— 全量三任务分片跑（共用 llama-server --parallel 4）
#
# 分配：TASK_SHARDS 里每个 "任务:片数" 一组，总片数应等于 llama-server 的 slots。
#       默认 1:2 2:1 3:1 = 4 片，全部并行，总墙钟 = 最慢的那片。
#
# 与 launch_t1_shards.sh 的区别：
#   1. 支持多任务；2. 每片独立 CHIMERA_EMBED_SOCKET（防互杀，见 10-30 事故）；
#   3. 不逐片跑 gate —— 全量跑完统一体检一次（逐片跑会在半成品目录上误判）。
#
# 用法：
#   bash scripts/launch_full_shards.sh                 # 正式跑
#   DRY=1 bash scripts/launch_full_shards.sh           # 只打印分配
#   OUT=test/output.xxx bash scripts/launch_full_shards.sh
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PY="${PYTHON:-$HOME/miniconda3/envs/chimera/bin/python}"
OUT="${OUT:-test/output.full_20261031}"
TASK_SHARDS="${TASK_SHARDS:-1:2 2:2 3:1}"
DRY="${DRY:-0}"

export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHON="$PY"

echo "输出目录: $OUT"
echo "分片分配: $TASK_SHARDS"

PIDS=()
LOGS=()
TOTAL=0
for pair in $TASK_SHARDS; do
  T="${pair%%:*}"
  K="${pair##*:}"
  DIR="data/task${T}/agent_input"
  if [ ! -d "$DIR" ]; then echo "跳过 T$T（无目录）"; continue; fi

  mapfile -t ALL < <(ls -d "$DIR"/*/ 2>/dev/null | xargs -n1 basename | sort)
  N=${#ALL[@]}
  TOTAL=$((TOTAL + N))
  echo "T$T: $N 例 → $K 片"

  for ((i=0; i<K; i++)); do
    LIST=""
    CNT=0
    for ((j=i; j<N; j+=K)); do
      LIST="${LIST:+$LIST,}${ALL[$j]}"
      CNT=$((CNT+1))
    done
    [ "$CNT" -eq 0 ] && continue
    LOG="/tmp/fullrun_t${T}_s${i}.log"
    LOGS+=("$LOG")
    if [ "$DRY" = "1" ]; then
      echo "  [dry] T$T shard$i 例数=$CNT 日志=$LOG"
      continue
    fi
    CHIMERA_EMBED_SOCKET="/tmp/chimera_embed_full_t${T}s${i}.sock" \
      nohup "$PY" -m chimera_agent_baseline.run \
        +experiment=qwen_local "agent.tasks=[$T]" "agent.pids=[$LIST]" \
        paths.output_dir="$OUT" > "$LOG" 2>&1 &
    PIDS+=($!)
    echo "  T$T shard$i PID=$! 例数=$CNT"
    sleep 2
  done
done

echo "合计 $TOTAL 例，启动 ${#PIDS[@]} 个进程"
[ "$DRY" = "1" ] && exit 0

# 等全部结束（最多 8 小时）
for pid in "${PIDS[@]}"; do wait "$pid" 2>/dev/null || true; done

echo "全部进程结束，合并日志并统一体检"
ALL_LOG=/tmp/fullrun_all.log
: > "$ALL_LOG"
for f in "${LOGS[@]}"; do [ -f "$f" ] && cat "$f" >> "$ALL_LOG"; done

N_PRED=$(ls "$OUT"/task*/*/prediction.json 2>/dev/null | wc -l)
N_FAIL=$(ls "$OUT"/task*/*/.failed 2>/dev/null | wc -l)
echo "产出 prediction.json: $N_PRED | .failed: $N_FAIL"

GATE_ONLY=1 OUT="$OUT" LOG="$ALL_LOG" bash scripts/run_with_gate.sh
