#!/bin/bash
# P10 端到端 A/B：T1 91 例对照臂 → T1 91 例决策层臂
#
# 目的：把离线的「决策层 Δ/SE=+3.31」坐实到**真实管线**
#   臂 1 对照：T1 现行（决策层 OFF）→ test/output.p10_t1_ctl
#              ★ 兼任此前缺失的「T1 当前代码基线」（层1c/1d 生效后的同代码对照）
#   臂 2 处理：T1 + 决策层（CHIMERA_T1_DECISION_LAYER=1）→ test/output.p10_t1_layer
#   两臂唯一差异 = 决策层开关（其余含 predictor note、reflect 关闭、层1c/1d 全同）
#
# 用法（服务器 ~/CHIMERA-agent）：
#   setsid nohup bash scripts/run_p10_overnight.sh > /tmp/p10_overnight.log 2>&1 < /dev/null &
set -u
cd "$HOME/CHIMERA-agent" || exit 1
PY="$HOME/miniconda3/envs/chimera/bin/python"
CUTOFF_H=22          # 22:00 后不再开新臂

# 只匹配"以解释器开头"的真实运行进程（避免自匹配，P9 踩过）
busy() { ps -eo args= | grep -q "^$PY -m chimera_agent_baseline.run"; }
room() { [ "$(date +%H)" -lt "$CUTOFF_H" ]; }

PIDS=$($PY -c "import json;print('['+','.join(json.load(open('scripts/t1_full_91_gt_pids.json')))+']')")
COMMON=( '+experiment=qwen_local' 'agent.tasks=[1]' "agent.pids=$PIDS" )

echo "===== P10 START $(date '+%F %T') ====="

# ---------------------------------------------------------------- 预检
echo ""
echo "----- 预检 1：等待已有运行结束（最多 60 轮 × 30s） -----"
i=0
while busy; do
  i=$((i+1)); [ "$i" -ge 60 ] && { echo ">>> ✗ 预检超时 → 中止"; exit 2; }
  sleep 30
done
echo "预检 1 通过 $(date '+%T')"

echo "----- 预检 2：决策层系数与正对照 -----"
$PY tools/verify_t1_decision_layer.py > /tmp/p10_preflight.log 2>&1
PRC=$?
grep -E "^[✓✗]|==>" /tmp/p10_preflight.log | tail -7
if [ "$PRC" -ne 0 ]; then
  echo ">>> ✗ 决策层正对照未通过 → 中止（不浪费两臂算力）"
  exit 3
fi
echo "预检 2 通过"

# ---------------------------------------------------------------- 臂 1
echo ""
if room; then
  echo "----- 臂 1/2：T1 对照（决策层 OFF）$(date '+%T') -----"
  rm -rf test/output.p10_t1_ctl
  $PY -m chimera_agent_baseline.run "${COMMON[@]}" \
    'paths.output_dir=test/output.p10_t1_ctl' > /tmp/p10_arm1.log 2>&1
  echo "臂 1 rc=$? $(date '+%T')"
else
  echo "----- 臂 1 被时间闸门跳过 -----"
fi

# ---------------------------------------------------------------- 臂 2
echo ""
if room; then
  echo "----- 臂 2/2：T1 + 决策层（CHIMERA_T1_DECISION_LAYER=1）$(date '+%T') -----"
  rm -rf test/output.p10_t1_layer
  CHIMERA_T1_DECISION_LAYER=1 $PY -m chimera_agent_baseline.run "${COMMON[@]}" \
    'paths.output_dir=test/output.p10_t1_layer' > /tmp/p10_arm2.log 2>&1
  echo "臂 2 rc=$? $(date '+%T')"
else
  echo "----- 臂 2 被时间闸门跳过 -----"
fi

# ---------------------------------------------------------------- 收尾
echo ""
echo "===== 收尾：连通性口检 ====="
for pair in "test/output.p10_t1_ctl:/tmp/p10_arm1.log" \
            "test/output.p10_t1_layer:/tmp/p10_arm2.log"; do
  d="${pair%%:*}"; lg="${pair##*:}"
  [ -d "$d" ] || continue
  echo "### $d"
  $PY tools/connectivity_gate.py run "$d" --src src --log "$lg" 2>&1 | head -18
done

echo ""
echo "===== 关键自检 ====="
echo "[臂1] 完成例数（应 91）:        $(ls -d test/output.p10_t1_ctl/task1/*/ 2>/dev/null | wc -l)"
echo "[臂2] 完成例数（应 91）:        $(ls -d test/output.p10_t1_layer/task1/*/ 2>/dev/null | wc -l)"
echo "[臂2] 决策层日志行（应 >0）:    $(grep -c 'T1 decision layer:' /tmp/p10_arm2.log || true)"
echo "[臂2] 决策层改判行（应 >0）:    $(grep -c 'decision layer:.*改判' /tmp/p10_arm2.log || true)"
echo "[臂1] 不应出现决策层日志（应 0）: $(grep -c 'T1 decision layer:' /tmp/p10_arm1.log || true)"
echo "[臂2] 决策层 SKIPPED/FAILED（应 0）: $(grep -cE 'decision layer: (SKIPPED|LOAD FAILED)' /tmp/p10_arm2.log || true)"

echo ""
echo "===== P10 DONE $(date '+%F %T') ====="
echo "判读：对两臂跑 tools/t1_ab_eval.py（决策对比 + 官方评估器分量）"