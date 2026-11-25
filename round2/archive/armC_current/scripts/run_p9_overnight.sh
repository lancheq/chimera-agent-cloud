#!/bin/bash
# P9 过夜跑批：预检 → 顺序跑两条臂（每条只改一个变量）→ 收尾口检
#
# 用法（服务器 ~/CHIMERA-agent）：
#   setsid nohup bash scripts/run_p9_overnight.sh > /tmp/p9_overnight.log 2>&1 < /dev/null &
#
# 前置：8765 的 35B llama-server 在线。
# 设计见 docs/P9_过夜跑批计划_20260927.md
set -u
cd "$HOME/CHIMERA-agent" || exit 1
PY="$HOME/miniconda3/envs/chimera/bin/python"

PIDS=$($PY -c "import json;print('['+','.join(json.load(open('scripts/t2_full_72_gt_pids.json')))+']')")
COMMON=( '+experiment=qwen_local' 'agent.tasks=[2]' "agent.pids=$PIDS" )

echo "===== P9 START $(date '+%F %T') ====="

# ============================================================ 预检
echo ""
echo "----- 预检 1：等待已有运行结束（最多 60 轮 × 15s） -----"
i=0
while pgrep -af "chimera_agent_baseline.run" 2>/dev/null | grep -v "bash -c" | grep -q .; do
  i=$((i+1))
  [ "$i" -ge 60 ] && { echo ">>> ✗ 预检超时（仍有运行在跑）→ 中止"; exit 2; }
  sleep 15
done
echo "预检 1 通过：无并发运行 $(date '+%T')"

echo ""
echo "----- 预检 2：验证开关 CHIMERA_FORMFILL_SELF_REFINE 真的生效 -----"
if [ -f /tmp/smoke_a.log ]; then
  N_TRUE=$(grep -c "self_refine=True" /tmp/smoke_a.log || true)
  N_FALSE=$(grep -c "self_refine=False" /tmp/smoke_a.log || true)
  N_REF=$(grep -c "judgment refined successfully" /tmp/smoke_a.log || true)
  echo "  smoke: self_refine=True x$N_TRUE / self_refine=False x$N_FALSE / refined x$N_REF"
  if [ "$N_TRUE" -lt 1 ]; then
    echo ">>> ✗ 开关未生效（未出现 self_refine=True）→ 中止，不浪费一夜"
    echo ">>> smoke 尾部："; tail -8 /tmp/smoke_a.log
    exit 3
  fi
  echo "预检 2 通过：接线开关已生效"
else
  echo "  （无 /tmp/smoke_a.log，跳过该预检）"
fi

# ============================================================ Arm A
# 变量：self_refine「接线」——关掉产出被丢弃的 reflect 节点，打开产出被消费的内部那套
echo ""
echo "----- Arm A: self_refine 接线 (CHIMERA_FORMFILL_SELF_REFINE=1, reflect=off) $(date '+%T') -----"
rm -rf test/output.night_t2_sref
CHIMERA_FORMFILL_SELF_REFINE=1 $PY -m chimera_agent_baseline.run \
  "${COMMON[@]}" 'agent.form_fill.self_refine=false' \
  'paths.output_dir=test/output.night_t2_sref' > /tmp/p9_arm_a.log 2>&1
echo "Arm A rc=$? $(date '+%T')"

# ============================================================ Arm B
# 变量：关锚定——不把 predictor 决策注入 LLM prompt。其余与基线一致（reflect ON）。
echo ""
echo "----- Arm B: 关锚定 (CHIMERA_PREDICTOR_NOTE=0) $(date '+%T') -----"
rm -rf test/output.night_t2_nohint
CHIMERA_PREDICTOR_NOTE=0 $PY -m chimera_agent_baseline.run \
  "${COMMON[@]}" \
  'paths.output_dir=test/output.night_t2_nohint' > /tmp/p9_arm_b.log 2>&1
echo "Arm B rc=$? $(date '+%T')"

# ============================================================ 收尾
echo ""
echo "===== 收尾：连通性口检 ====="
$PY tools/connectivity_gate.py run test/output.night_t2_sref --src src --log /tmp/p9_arm_a.log
echo "  (Arm A gate rc=$?)"
$PY tools/connectivity_gate.py run test/output.night_t2_nohint --src src --log /tmp/p9_arm_b.log
echo "  (Arm B gate rc=$?)"

echo ""
echo "===== 关键自检 ====="
echo "[A] self_refine=True 出现次数（应 >0，否则整臂作废）: $(grep -c 'self_refine=True' /tmp/p9_arm_a.log || true)"
echo "[A] 'judgment refined successfully' 次数（应 >0）:     $(grep -c 'judgment refined successfully' /tmp/p9_arm_a.log || true)"
echo "[A] 完成例数（应为 72）:                                $(ls -d test/output.night_t2_sref/task2/*/ 2>/dev/null | wc -l)"
echo "[B] 完成例数（应为 72）:                                $(ls -d test/output.night_t2_nohint/task2/*/ 2>/dev/null | wc -l)"
echo "[B] 残留锚定提示（应为 0，非 0 则开关没生效）:          $(grep -c 'Clinical prediction reference' /tmp/p9_arm_b.log || true)"

echo ""
echo "===== P9 DONE $(date '+%F %T') ====="
echo "下一步（本地/交互）：对两臂跑官方评估器与 t2_arbiter_headroom.py 复算，见计划文档 §6。"