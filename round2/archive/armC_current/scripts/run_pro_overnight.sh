#!/bin/bash
# ===========================================================================
# Pro 架构过夜运行脚本 (峰谷电价优化)
#
# 策略: 顺序执行 task2 → task3, 输出到同一目录支持断点续跑
# - task2: 154 case, ~6.4h (最难, DAG=0.264, 验证 Pro self_refine 价值)
# - task3: 76 case,  ~3.2h (months 回归, 自动续跑填充剩余时间)
# - task1: 已跑 5 case, 明晚续跑 (192 case remaining)
#
# 关键: decision_override_tasks=[] 关闭 predictor 覆盖, 让 Pro LLM 决策生效
#
# ⚠️  对于无人值守运行, 推荐使用 auto_heal_watcher.sh 代替此脚本:
#   nohup setsid bash scripts/auto_heal_watcher.sh < /dev/null > auto_heal.log 2>&1 &
# auto_heal_watcher.sh 会自动管理 task2→task3 切换、断点续跑、Qwen 健康检查。
#
# 用法:
#   nohup bash scripts/run_pro_overnight.sh > full_run_pro_overnight.log 2>&1 &
#   tail -f full_run_pro_overnight.log
#
# 明早评估:
#   bash scripts/eval_pro_task2.sh
# ===========================================================================
cd /root/shared-nvme/chimera-agent-baseline
source .venv/bin/activate 2>/dev/null || true

OUT_DIR="test/output.pro_v1_20260816"
export CHIMERA_OUTPUT_DIR="$OUT_DIR"
COMMON_ARGS="+experiment=qwen_local agent.predictor.decision_override_tasks=[] agent.form_fill.self_refine=true agent.form_fill.atomic_check=true paths.output_dir=$OUT_DIR"

echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Phase 1: task2 (154 cases, ~6.4h)"
echo "============================================================"
# 使用 >> 追加模式, 保留每次重启的历史日志
.venv/bin/python -m chimera_agent_baseline.run $COMMON_ARGS agent.tasks=[2] \
    >> full_run_pro_task2.log 2>&1

echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Phase 2: task3 (76 cases, ~3.2h) - 自动续跑"
echo "============================================================"
# 使用 >> 追加模式, 保留每次重启的历史日志
.venv/bin/python -m chimera_agent_baseline.run $COMMON_ARGS agent.tasks=[3] \
    >> full_run_pro_task3.log 2>&1

echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] task2+task3 完成. task1 待明晚续跑."
echo "============================================================"
echo "明晚续跑 task1 (自动跳过已完成 5 case):"
echo "  nohup .venv/bin/python -m chimera_agent_baseline.run $COMMON_ARGS agent.tasks=[1] > full_run_pro_task1.log 2>&1 &"
