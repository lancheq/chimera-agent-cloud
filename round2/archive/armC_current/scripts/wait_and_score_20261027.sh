#!/bin/bash
# 2026-10-27：B3 / EN 两条跑批的自动收尾
#   1) 等 B3 结束 → 恢复被隐藏的 reranker_model → 评分 B3 vs B
#   2) 等 EN 结束 → 评分 EN vs B
# 用法（服务器）：setsid nohup bash scripts/wait_and_score_20261027.sh > /tmp/wait_score.log 2>&1 < /dev/null &
set -u
ROOT="$HOME/CHIMERA-agent"
EN_ROOT="$HOME/CHIMERA-agent-ab_en"
PY="$HOME/miniconda3/envs/chimera/bin/python"
OUT="$ROOT/reports/rag_en_arm_20261027"
mkdir -p "$OUT"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$OUT/watcher.log"; }

count() { ls "$1"/task1 2>/dev/null | wc -l | tr -d ' '; }

log "watcher start (pid $$)"

# ---------- 1) B3 ----------
while pgrep -f "chimera_agent_baseline.run.*output.b3_norr" > /dev/null 2>&1; do sleep 60; done
sleep 10
log "B3 ended: $(count "$ROOT/test/output.b3_norr")/197  (fail=$(grep -ac FAILED /tmp/b3_full.log 2>/dev/null))"

if [ -d "$ROOT/resources/reranker_model.hidden_b3" ]; then
    mv "$ROOT/resources/reranker_model.hidden_b3" "$ROOT/resources/reranker_model" \
        && log "reranker_model restored"
else
    log "WARN reranker_model.hidden_b3 not found - nothing to restore"
fi

OMP_NUM_THREADS=2 "$PY" "$ROOT/tools/ab_score_rag.py" \
    --gt "$ROOT/data/task1/agent_input" \
    --arm B="$ROOT/test/output.ab_new/task1" \
    --arm B3="$ROOT/test/output.b3_norr/task1" \
    > "$OUT/b3_vs_b.txt" 2>&1
log "b3 scoring done rc=$? -> $OUT/b3_vs_b.txt"

# ---------- 2) EN ----------
while pgrep -f "chimera_agent_baseline.run.*output.ab_en" > /dev/null 2>&1; do sleep 60; done
sleep 10
log "EN ended: $(count "$EN_ROOT/test/output.ab_en")/197  (fail=$(grep -ac FAILED /tmp/ab_en_full.log 2>/dev/null))"

OMP_NUM_THREADS=2 "$PY" "$ROOT/tools/ab_score_rag.py" \
    --gt "$ROOT/data/task1/agent_input" \
    --arm B="$ROOT/test/output.ab_new/task1" \
    --arm EN="$EN_ROOT/test/output.ab_en/task1" \
    > "$OUT/en_vs_b.txt" 2>&1
log "en scoring done rc=$? -> $OUT/en_vs_b.txt"

log "DONE"
