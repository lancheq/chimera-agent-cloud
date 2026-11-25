#!/bin/bash
# P9b 过夜跑批（续）：Arm A 已在跑 → 等它收尾归位 → Arm B → Arm C（时间闸门）
#
# 背景：原 P9 链在「预检 1」因 pgrep 匹配到自身命令行而超时中止（未被发现的自匹配 bug）。
#       同时 CHIMERA_FORMFILL_SELF_REFINE=1 的 1 例冒烟实际全量跑了起来
#       （agent.limit 未生效），配置与 Arm A 完全一致 → 直接作为 Arm A 使用。
#
# 用法（服务器 ~/CHIMERA-agent）：
#   setsid nohup bash scripts/run_p9b_overnight.sh > /tmp/p9b_overnight.log 2>&1 < /dev/null &
set -u
cd "$HOME/CHIMERA-agent" || exit 1
PY="$HOME/miniconda3/envs/chimera/bin/python"
CUTOFF_H=5          # 到 05:00 之后不再开新臂，避免跑进白天

# ★ 只匹配"以解释器开头"的真实运行进程；不匹配 bash -c / ssh 包装（修掉上一版的自匹配）
busy() { ps -eo args= | grep -q "^$PY -m chimera_agent_baseline.run"; }
room()  { [ "$(date +%H)" -lt "$CUTOFF_H" ]; }

echo "===== P9b START $(date '+%F %T') ====="

# ============================================================ 收尾 Arm A
echo ""
echo "----- 等 Arm A（/tmp/smoke_a）结束 -----"
i=0
while busy; do
  i=$((i+1))
  [ "$i" -ge 200 ] && { echo ">>> ✗ 等待超时（>100min）→ 中止"; exit 2; }
  sleep 30
done
echo "Arm A 结束于 $(date '+%T')"

if [ -d /tmp/smoke_a/task2 ]; then
  rm -rf test/output.night_t2_sref
  mv /tmp/smoke_a test/output.night_t2_sref && echo "已归位 → test/output.night_t2_sref"
fi
cp -f /tmp/smoke_a.log /tmp/p9_arm_a.log 2>/dev/null || true

A_TRUE=$(grep -c "self_refine=True" /tmp/p9_arm_a.log || true)
A_CASES=$(ls -d test/output.night_t2_sref/task2/*/ 2>/dev/null | wc -l)
echo "Arm A 自检：self_refine=True x$A_TRUE ; 完成例数 $A_CASES（应 72）"
if [ "$A_TRUE" -lt 1 ]; then
  echo ">>> ✗ Arm A 开关未生效 → 中止，不浪费后续臂"
  exit 3
fi

T2COMMON=( '+experiment=qwen_local' 'agent.tasks=[2]'
           "agent.pids=$($PY -c "import json;print('['+','.join(json.load(open('scripts/t2_full_72_gt_pids.json')))+']')")" )
T1COMMON=( '+experiment=qwen_local' 'agent.tasks=[1]'
           "agent.pids=$($PY -c "import json;print('['+','.join(json.load(open('scripts/t1_full_91_gt_pids.json')))+']')")" )

# ============================================================ Arm B
# 变量：关锚定（T2）。其余与基线一致（reflect ON）。
echo ""
if room; then
  echo "----- Arm B: T2 关锚定 (CHIMERA_PREDICTOR_NOTE=0) $(date '+%T') -----"
  rm -rf test/output.night_t2_nohint
  CHIMERA_PREDICTOR_NOTE=0 $PY -m chimera_agent_baseline.run \
    "${T2COMMON[@]}" \
    'paths.output_dir=test/output.night_t2_nohint' > /tmp/p9_arm_b.log 2>&1
  echo "Arm B rc=$? $(date '+%T')"
else
  echo "----- Arm B 被时间闸门跳过（已过 ${CUTOFF_H}:00） -----"
fi

# ============================================================ Arm C
# 变量：关锚定（T1）。一石二鸟：
#   ① 测锚定对 T1 决策的影响（T1 决策不被 override，就是 LLM 自己的决策 → 影响直接）
#      —— 与旧批 night_t1_base 比决策是干净的（层1c/1d 只改权重，不碰 biopsy_decision）
#   ② 顺便用门验证层1c/1d 在 T1 上到底触发多少（旧批是改动前产物，只有 0 次）
echo ""
if room; then
  echo "----- Arm C: T1 关锚定 (CHIMERA_PREDICTOR_NOTE=0, 91 例) $(date '+%T') -----"
  rm -rf test/output.p9_t1_nohint
  CHIMERA_PREDICTOR_NOTE=0 $PY -m chimera_agent_baseline.run \
    "${T1COMMON[@]}" \
    'paths.output_dir=test/output.p9_t1_nohint' > /tmp/p9_arm_c.log 2>&1
  echo "Arm C rc=$? $(date '+%T')"
else
  echo "----- Arm C 被时间闸门跳过（已过 ${CUTOFF_H}:00） -----"
fi

# ============================================================ Arm D（可选）
# 变量：T1 当前代码 + note ON —— 层1c/1d 生效后的 T1「当前代码基线」
echo ""
if room; then
  echo "----- Arm D: T1 当前代码基线 (note ON, 91 例) $(date '+%T') -----"
  rm -rf test/output.p9_t1_curr
  $PY -m chimera_agent_baseline.run \
    "${T1COMMON[@]}" \
    'paths.output_dir=test/output.p9_t1_curr' > /tmp/p9_arm_d.log 2>&1
  echo "Arm D rc=$? $(date '+%T')"
else
  echo "----- Arm D 被时间闸门跳过（已过 ${CUTOFF_H}:00） -----"
fi

# ============================================================ 收尾
echo ""
echo "===== 收尾：连通性口检 ====="
for pair in "test/output.night_t2_sref:/tmp/p9_arm_a.log" \
            "test/output.night_t2_nohint:/tmp/p9_arm_b.log" \
            "test/output.p9_t1_nohint:/tmp/p9_arm_c.log" \
            "test/output.p9_t1_curr:/tmp/p9_arm_d.log"; do
  d="${pair%%:*}"; lg="${pair##*:}"
  [ -d "$d" ] || continue
  echo "### $d"
  $PY tools/connectivity_gate.py run "$d" --src src --log "$lg" 2>&1 | head -20
done

echo ""
echo "===== 关键自检 ====="
for pair in "night_t2_sref:test/output.night_t2_sref:72" \
            "night_t2_nohint:test/output.night_t2_nohint:72" \
            "p9_t1_nohint:test/output.p9_t1_nohint:91" \
            "p9_t1_curr:test/output.p9_t1_curr:91"; do
  nm="${pair%%:*}"; rest="${pair#*:}"; d="${rest%%:*}"; exp="${rest##*:}"
  if [ -d "$d" ]; then
    echo "$nm: 完成例数 $(ls -d $d/task*/*/ 2>/dev/null | wc -l)（应 $exp）"
  fi
done

echo ""
echo "===== P9b DONE $(date '+%F %T') ====="
echo "判读见 docs/P9_过夜跑批计划_20260927.md §6"