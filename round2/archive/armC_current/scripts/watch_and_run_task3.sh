#!/bin/bash
# ===========================================================================
# 监控 task2 进程, 完成后自动启动 task3 (断点续跑, 填充剩余夜间时间)
#
# ⚠️  已弃用: 此脚本与 auto_heal_watcher.sh 功能重叠且使用 > 覆盖日志。
#   请改用 auto_heal_watcher.sh (自动管理 task2→task3, 日志追加模式, 防重复进程):
#     nohup setsid bash scripts/auto_heal_watcher.sh < /dev/null > auto_heal.log 2>&1 &
#
# 用法 (task2 已在跑的前提下):
#   nohup setsid bash scripts/watch_and_run_task3.sh < /dev/null > watch_task3.log 2>&1 &
# ===========================================================================
cd /root/shared-nvme/chimera-agent-baseline

echo "[$(date '+%H:%M:%S')] Watcher started, waiting for task2 (PID 222783) to finish..."

# 等 task2 进程结束 (检测 agent.tasks=[2] 的 python 进程)
while pgrep -f "chimera_agent_baseline.run.*agent.tasks=\[2\]" > /dev/null 2>&1; do
    sleep 60
done

echo "[$(date '+%H:%M:%S')] task2 finished. Starting task3 (76 cases, ~3.2h)..."

# task2 完成后自动启动 task3 (同样的 Pro 配置, 输出到同目录)
# ⚠️  使用 >> 追加模式, 不覆盖之前的日志 (修复日志覆盖问题)
nohup setsid env $(grep -v "^#" .env | xargs) .venv/bin/python -m chimera_agent_baseline.run \
    +experiment=qwen_local \
    agent.tasks=[3] \
    agent.predictor.decision_override_tasks=[] \
    agent.form_fill.self_refine=true \
    agent.form_fill.atomic_check=true \
    paths.output_dir=test/output.pro_v1_20260816 \
    >> full_run_pro_task3.log 2>&1 < /dev/null &

TASK3_PID=$!
echo "[$(date '+%H:%M:%S')] task3 started, PID=$TASK3_PID"
echo "[$(date '+%H:%M:%S')] Watcher done. task1 (192 remaining) 待明晚续跑:"
echo "  nohup setsid env \$(grep -v '^#' .env | xargs) .venv/bin/python -m chimera_agent_baseline.run +experiment=qwen_local agent.tasks=[1] agent.predictor.decision_override_tasks=[] agent.form_fill.self_refine=true agent.form_fill.atomic_check=true paths.output_dir=test/output.pro_v1_20260816 > full_run_pro_task1.log 2>&1 < /dev/null &"
