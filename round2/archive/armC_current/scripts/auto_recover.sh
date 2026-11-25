#!/bin/bash
# ===========================================================================
# 开机自恢复脚本 — 实例重建/重启后一键拉起 Qwen 服务 + 续跑推理
#
# 原理:
#   - shared-nvme 持久盘不丢, 代码/模型/已有结果都在
#   - prediction.json 每 case 独立落盘, run.py 自带 resume (跳过已有结果)
#   - 本脚本检测各组件状态, 缺什么补什么, 幂等可重复执行
#
# 用法:
#   手动恢复:  bash scripts/auto_recover.sh
#   开机自启:  (crontab -l 2>/dev/null; echo "@reboot /root/shared-nvme/chimera-agent-baseline/scripts/auto_recover.sh >> /root/shared-nvme/chimera-agent-baseline/auto_recover.log 2>&1") | crontab -
#   查看日志:  tail -f auto_recover.log
#
# 注意:
#   - 不含心跳保活 (用户关机省钱, 不需要防 idle)
#   - 推理用 nohup 后台跑, SSH 断连不影响
# ===========================================================================
set -euo pipefail

# ── 路径与配置 ──────────────────────────────────────────────────────────────
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")
cd "$PROJECT_DIR"

QWEN_PORT="${QWEN_PORT:-8765}"
QWEN_MODEL="model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
QWEN_LOG="qwen_server.log"
VENV=".venv/bin/activate"
MAX_WAIT_QWEN=120    # Qwen 模型加载最大等待秒数
MAX_WAIT_GPU=30      # GPU NVML 就绪等待秒数

# 从 .chimera_run.json 自动检测输出目录/日志/目标数
read_manifest() {
    local m
    m=$(.venv/bin/python -c "
import json, os
try:
    d=json.load(open('.chimera_run.json'))
    print(d.get('output_dir','test/output'))
    print(d.get('log_file','full_run_continue.log'))
    p=d.get('pids')
    if p:
        print(len(p))
    else:
        total=sum(len(os.listdir(f'data/task{t}/agent_input')) for t in [1,2,3] if os.path.isdir(f'data/task{t}/agent_input'))
        print(total if total else 425)
except Exception:
    print('test/output'); print('full_run_continue.log'); print('425')
" 2>/dev/null)
    OUTPUT_DIR=$(echo "$m" | sed -n '1p')
    RUN_LOG=$(echo "$m" | sed -n '2p')
    TOTAL_TARGET=$(echo "$m" | sed -n '3p')
}
read_manifest

log()   { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
ok()    { log "✅ $*"; }
warn()  { log "⚠️  $*"; }
fail()  { log "❌ $*"; }

# ── Step 0: 前置检查 ────────────────────────────────────────────────────────
log "=== 自恢复开始 ==="

# 激活 venv
if [ -f "$VENV" ]; then
    source "$VENV"
    ok "venv 已激活"
else
    fail "venv 不存在: $VENV"
    exit 1
fi

# 检查模型文件
if [ ! -f "$QWEN_MODEL" ]; then
    fail "模型不存在: $QWEN_MODEL — 持久盘可能未挂载"
    exit 1
fi
ok "模型文件就位: $(ls -lh "$QWEN_MODEL" | awk '{print $5}')"

# 等待 GPU NVML 就绪 (容器启动瞬间可能未就绪)
log "等待 GPU 就绪..."
for i in $(seq 1 "$MAX_WAIT_GPU"); do
    if nvidia-smi --query-gpu=name --format=csv,noheader &>/dev/null; then
        ok "GPU 就绪: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
        break
    fi
    [ "$i" -eq "$MAX_WAIT_GPU" ] && { fail "GPU 等待超时"; exit 1; }
    sleep 1
done

# ── Step 1: 拉起 Qwen 服务 ──────────────────────────────────────────────────
# 健康检查: 15s 超时 (推理中 Qwen 可能正忙, 短超时会误判为离线)
qwen_healthy() {
    curl -s --max-time 15 "http://127.0.0.1:${QWEN_PORT}/v1/models" 2>/dev/null | grep -q '"object":"list"'
}

# 进程检查: 判断 llama_cpp.server 是否在运行
qwen_running() {
    pgrep -f "llama_cpp\.server.*${QWEN_PORT}" > /dev/null 2>&1
}

if qwen_healthy; then
    ok "Qwen 服务已在线 (端口 ${QWEN_PORT})"
elif qwen_running; then
    # 进程在但健康检查超时 → 可能正在推理中, 不杀不动
    warn "Qwen 进程在运行但健康检查超时 (可能正忙于推理), 跳过重启避免中断"
    ok "Qwen 服务保留运行 (不干预)"
else
    warn "Qwen 服务未运行, 正在启动..."
    # 杀残留 (端口占用等异常情况)
    pkill -f "llama_cpp.server.*${QWEN_PORT}" 2>/dev/null || true
    sleep 2

    nohup python -m llama_cpp.server \
        --model "$QWEN_MODEL" \
        --host 127.0.0.1 \
        --port "$QWEN_PORT" \
        --n_gpu_layers -1 \
        --n_ctx 16384 \
        > "$QWEN_LOG" 2>&1 &
    QWEN_PID=$!
    log "Qwen 启动中 (PID ${QWEN_PID}), 等待模型加载 (最多 ${MAX_WAIT_QWEN}s)..."

    for i in $(seq 1 "$MAX_WAIT_QWEN"); do
        if qwen_healthy; then
            ok "Qwen 服务就绪 (${i}s)"
            break
        fi
        # 检查进程是否意外退出
        if ! ps -p "$QWEN_PID" > /dev/null 2>&1; then
            fail "Qwen 进程退出, 查看 $QWEN_LOG:"
            tail -10 "$QWEN_LOG" 2>/dev/null
            exit 1
        fi
        [ "$i" -eq "$MAX_WAIT_QWEN" ] && { fail "Qwen 启动超时"; tail -10 "$QWEN_LOG"; exit 1; }
        sleep 1
    done
fi

# ── Step 2: 检查推理进度 ────────────────────────────────────────────────────
log "--- 当前推理进度 ---"
TOTAL_DONE=0
for t in 1 2 3; do
    if [ -d "$OUTPUT_DIR/task${t}" ]; then
        DONE=$(find "$OUTPUT_DIR/task${t}" -name 'prediction.json' 2>/dev/null | wc -l)
    else
        DONE=0
    fi
    TOTAL_DONE=$((TOTAL_DONE + DONE))
    TASK_TARGET=$(ls "data/task${t}/agent_input/" 2>/dev/null | wc -l)
    [ -z "$TASK_TARGET" ] && TASK_TARGET=0
    log "  task${t}: ${DONE} / ${TASK_TARGET}"
done
log "  总计: ${TOTAL_DONE} / ${TOTAL_TARGET}"

# ── Step 3: 续跑推理 (如果未完成且有进程没在跑) ─────────────────────────────
INFER_RUNNING=$(ps aux | grep -E "chimera_agent_baseline\.run" | grep -v grep | head -1)
if [ -n "$INFER_RUNNING" ]; then
    ok "推理进程已在运行, 无需重启:"
    echo "$INFER_RUNNING" | awk '{print "  PID="$2, "CPU="$3"%", "MEM="$4"%"}'
else
    if [ "$TOTAL_DONE" -ge "$TOTAL_TARGET" ]; then
        ok "全部 ${TOTAL_TARGET} cases 已完成, 无需续跑"
    else
        warn "推理未在运行 (已完成 ${TOTAL_DONE}/${TOTAL_TARGET}), 启动续跑..."
        CMDLINE_FILE=".chimera_infer_cmd.txt"
        if [ -s "$CMDLINE_FILE" ]; then
            infer_cmd=$(cat "$CMDLINE_FILE")
            log "回放原始命令: $infer_cmd"
            nohup $infer_cmd > "$RUN_LOG" 2>&1 &
        else
            nohup python -m chimera_agent_baseline.run \
                +experiment=qwen_local \
                agent.tasks='[1,2,3]' \
                > "$RUN_LOG" 2>&1 &
        fi
        RUN_PID=$!
        ok "续跑已启动 (PID ${RUN_PID}), 日志: $RUN_LOG"
        log "  resume 机制会自动跳过已完成的 case"
    fi
fi

# ── Step 3.5: 启动 Watchdog (卡死自动重启) ─────────────────────────────────
WATCHDOG_RUNNING=$(pgrep -f "scripts/watchdog.sh" | head -1)
if [ -n "$WATCHDOG_RUNNING" ]; then
    ok "Watchdog 已在运行 (PID ${WATCHDOG_RUNNING})"
elif [ "$TOTAL_DONE" -lt "$TOTAL_TARGET" ]; then
    nohup bash scripts/watchdog.sh > watchdog.log 2>&1 &
    ok "Watchdog 已启动 (PID $!), 卡死 5min 自动重启"
else
    log "推理已完成, 无需启动 Watchdog"
fi

# ── Step 4: 状态汇总 ────────────────────────────────────────────────────────
log "=== 自恢复完成 ==="
log "Qwen:     $(qwen_running && echo '进程运行中' || echo '未运行')"
log "推理:     ${INFER_RUNNING:+运行中}${INFER_RUNNING:-未运行} (${TOTAL_DONE}/${TOTAL_TARGET})"
log "Watchdog: ${WATCHDOG_RUNNING:+运行中}${WATCHDOG_RUNNING:-已启动}"
log "查看推理日志:   tail -f $RUN_LOG"
log "查看Qwen日志:   tail -f $QWEN_LOG"
log "查看Watchdog:   tail -f watchdog.log"
