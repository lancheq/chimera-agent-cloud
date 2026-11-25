#!/bin/bash
# 重启 GT v3 断点续跑 — 从 .hydra/overrides.yaml 恢复原始 Hydra 参数
set -e
cd /root/shared-nvme/chimera-agent-baseline

# 激活虚拟环境
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

# 确认 LLM server 在运行
if ! curl -s http://127.0.0.1:8765/v1/models | grep -q model; then
    echo "ERROR: LLM server not running on port 8765"
    exit 1
fi
echo "LLM server OK"

# 从 overrides.yaml 构建命令参数
OVERRIDES_FILE=".hydra/overrides.yaml"
if [ ! -f "$OVERRIDES_FILE" ]; then
    echo "ERROR: overrides.yaml not found"
    exit 1
fi

# 将每行 override 转为命令行参数
ARGS=()
while IFS= read -r line; do
    # 去掉前导 "- " 和引号
    arg="${line#- }"
    arg="${arg//\"/}"
    ARGS+=("$arg")
done < "$OVERRIDES_FILE"

echo "Starting GT v3 resume with ${#ARGS[@]} Hydra overrides..."
echo "Args: ${ARGS[*]:0:8}... (pids omitted for brevity)"

# 后台启动，日志追加到同一个文件
nohup python -m chimera_agent_baseline.run "${ARGS[@]}" \
    > full_run_gt_v3_20260823.log 2>&1 &

RUN_PID=$!
echo "run.py started (PID=$RUN_PID)"
echo "Log: full_run_gt_v3_20260823.log"
sleep 3
if kill -0 $RUN_PID 2>/dev/null; then
    echo "Process alive, checking first log lines..."
    head -5 full_run_gt_v3_20260823.log
else
    echo "ERROR: Process died immediately!"
    tail -20 full_run_gt_v3_20260823.log
    exit 1
fi
