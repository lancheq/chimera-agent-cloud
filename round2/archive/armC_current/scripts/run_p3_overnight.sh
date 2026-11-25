#!/usr/bin/env bash
# =============================================================================
# P3 端到端 A/B 链（2026-10-16）
#
# 目的：把本次全部改动（P0-2b 落盘 + P1-2 置信度链 + P3 v2 去共线决策层）
#       做一次同代码、同批次的端到端对照。
#
# 臂：
#   A 对照  CHIMERA_T1_DECISION_LAYER=1   CHIMERA_CONF_DOWNGRADE=legacy
#   B 处理  CHIMERA_T1_DECISION_LAYER=v2  CHIMERA_CONF_DOWNGRADE=off
#   C 处理+去锚定  B 再叠加 CHIMERA_PREDICTOR_NOTE=0
#
# 三段式：预检 → 3 例冒烟（不过就中止）→ 逐臂跑 + 时间闸门 → 收尾自检
# 可 detached：setsid nohup bash scripts/run_p3_overnight.sh > /tmp/p3_chain.log 2>&1 &
# =============================================================================
set -u
cd "$(dirname "$0")/.."
PY=~/miniconda3/envs/chimera/bin/python
export PYTHONPATH="$PWD/src"
export LLAMA_API_KEY=no-key
# ★ 关键：本服务器出网受白名单限制，SentenceTransformer 启动时会尝试访问
#   HuggingFace 并**挂死**（实测：不加此开关 → embedding 子进程卡在网络上、
#   父进程死等 'ready'，整条链静默卡住）。加离线开关后加载仅 19.7s。
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
BASE=( '+experiment=qwen_local' 'agent.tasks=[1]' )
say() { echo "[$(date '+%F %T')] $*"; }
START=$(date +%s)

say "=== P3 链启动（pid $$） ==="

# ---------------- 0) 预检 ----------------
curl -s -m 5 http://127.0.0.1:8765/v1/models > /dev/null 2>&1 \
  || { say "❌ llama-server(8765) 不可达 → 中止"; exit 1; }
for f in resources/t1_decision_layer.json resources/t1_decision_layer_v2.json; do
  [ -f "$f" ] || { say "❌ 缺系数文件 $f → 中止"; exit 1; }
done
say "预检通过（服务可达 + 两个系数文件在位）"

# ---------------- 1) 冒烟 3 例（v2） ----------------
rm -rf test/output.p3_smoke
CHIMERA_T1_DECISION_LAYER=v2 CHIMERA_CONF_DOWNGRADE=off \
  $PY -m chimera_agent_baseline.run "${BASE[@]}" agent.limit=3 \
  paths.output_dir=test/output.p3_smoke > /tmp/p3_smoke.log 2>&1
RC=$?
NSMOKE=$(ls test/output.p3_smoke/task1/*/trace.json 2>/dev/null | wc -l | tr -d ' ')
NLINES=$(grep -c "decision layer v2:" /tmp/p3_smoke.log 2>/dev/null || echo 0)
NBLK=$(grep -l '"predictor"' test/output.p3_smoke/task1/*/trace.json 2>/dev/null | wc -l | tr -d ' ')
say "冒烟：rc=$RC  trace=$NSMOKE  v2 日志行=$NLINES  含 predictor 块=$NBLK"
if [ "$RC" -ne 0 ] || [ "$NSMOKE" -lt 3 ]; then
  say "❌ 冒烟未通过 → 中止（详见 /tmp/p3_smoke.log）"; exit 1
fi
if [ "$NBLK" -lt 3 ]; then
  say "⚠️ 冒烟有 trace 缺 predictor 块 → 中止（P0-1 落盘异常）"; exit 1
fi
say "✓ 冒烟通过"

# ---------------- 臂执行函数 ----------------
run_arm() {   # $1=臂名 $2=输出目录 $3...=环境变量
  local name="$1" out="$2"; shift 2
  rm -rf "$out"
  say "----- 臂 $name 开始（$*） -----"
  env "$@" $PY -m chimera_agent_baseline.run "${BASE[@]}" paths.output_dir="$out" \
    > "/tmp/p3_arm_${name}.log" 2>&1
  local rc=$?
  local n=$(ls "$out"/task1/*/prediction.json 2>/dev/null | wc -l | tr -d ' ')
  local blk=$(grep -l '"predictor"' "$out"/task1/*/trace.json 2>/dev/null | wc -l | tr -d ' ')
  local dl=$(grep -c "decision layer v2:" "/tmp/p3_arm_${name}.log" 2>/dev/null || echo 0)
  say "----- 臂 $name 结束 rc=$rc  prediction=$n  含 predictor 块=$blk  v2 日志行=$dl -----"
}

# ---------------- 2) 臂 A：对照（完全历史行为） ----------------
run_arm A test/output.p3_A CHIMERA_T1_DECISION_LAYER=1 CHIMERA_CONF_DOWNGRADE=legacy

# ---------------- 3) 臂 B：本次全部改动 ----------------
run_arm B test/output.p3_B CHIMERA_T1_DECISION_LAYER=v2 CHIMERA_CONF_DOWNGRADE=off

# ---------------- 4) 臂 C：B + 去锚定（时间闸门：仅当已跑 < 7h） ----------------
ELAPSED=$(( $(date +%s) - START ))
if [ "$ELAPSED" -lt 25200 ]; then
  run_arm C test/output.p3_C CHIMERA_T1_DECISION_LAYER=v2 CHIMERA_CONF_DOWNGRADE=off CHIMERA_PREDICTOR_NOTE=0
else
  say "臂 C 被时间闸门跳过（已跑 $((ELAPSED/3600))h）"
fi

# ---------------- 5) 收尾自检 ----------------
say "=== 收尾自检 ==="
for a in A B C; do
  d="test/output.p3_${a}/task1"
  [ -d "$d" ] || continue
  n=$(ls "$d"/*/prediction.json 2>/dev/null | wc -l | tr -d ' ')
  blk=$(grep -l '"predictor"' "$d"/*/trace.json 2>/dev/null | wc -l | tr -d ' ')
  cf=$(grep -l "confidence: .* kept (downgrade chain OFF" "$d"/*/trace.json 2>/dev/null | wc -l | tr -d ' ')
  say "臂 $a：prediction=$n / 含 predictor 块=$blk / 置信度链 OFF 记录=$cf"
done
say "=== P3 链结束，总耗时 $(( ($(date +%s) - START)/60 )) 分钟 ==="
