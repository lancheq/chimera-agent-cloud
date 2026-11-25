#!/bin/bash
# ===========================================================================
# Watchdog — 监控推理进程健康，卡死自动重启 + 写报警日志
#
# 原理:
#   - 每 60s 检查推理日志最后一条时间戳
#   - 超过 5 分钟无新日志 → 判定卡死
#   - 自动杀掉卡死的 Qwen + 推理进程，重启恢复
#   - 报警写入 watchdog.log + 终端输出
#
# 用法:
#   nohup bash scripts/watchdog.sh > watchdog.log 2>&1 &
#   tail -f watchdog.log
#
# 参数 (环境变量):
#   WATCHDOG_INTERVAL  检查间隔秒数 (默认 60)
#   WATCHDOG_TIMEOUT   卡死判定阈值秒数 (默认 300 = 5min)
#   LOG_FILE           要监控的推理日志文件 (默认 full_run_continue.log)
# ===========================================================================
cd /root/shared-nvme/chimera-agent-baseline

INTERVAL="${WATCHDOG_INTERVAL:-60}"
TIMEOUT="${WATCHDOG_TIMEOUT:-300}"
QWEN_LOG="qwen_server.log"
PID_FILE="watchdog.pid"
CMDLINE_FILE=".chimera_infer_cmd.txt"

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
    LOG_FILE_DEF=$(echo "$m" | sed -n '2p')
    TOTAL_TARGET=$(echo "$m" | sed -n '3p')
}
read_manifest
LOG="${LOG_FILE:-$LOG_FILE_DEF}"

# 防止多实例
if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog 已在运行 (PID $(cat $PID_FILE)), 退出"
    exit 0
fi
echo $$ > "$PID_FILE"
trap "rm -f $PID_FILE; exit 0" EXIT INT TERM

log()   { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
alert() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚨 $*"; }

log "Watchdog 启动 (间隔=${INTERVAL}s, 卡死阈值=${TIMEOUT}s)"
log "监控日志: $LOG"

restart_all() {
    local reason="$1"
    alert "检测到卡死: $reason"
    alert "正在重启..."

    # 杀推理进程 (先保存原始命令行, 重启时原样回放)
    local infer_pid
    infer_pid=$(pgrep -f "chimera_agent_baseline.run" | head -1)
    if [ -n "$infer_pid" ]; then
        if [ -r "/proc/$infer_pid/cmdline" ]; then
            tr '\0' ' ' < "/proc/$infer_pid/cmdline" > "$CMDLINE_FILE"
        fi
        alert "杀推理进程 PID $infer_pid"
        kill -9 "$infer_pid" 2>/dev/null
    fi

    # 杀 Qwen 进程
    local qwen_pid
    qwen_pid=$(pgrep -f "llama_cpp.server" | head -1)
    if [ -n "$qwen_pid" ]; then
        alert "杀 Qwen 进程 PID $qwen_pid"
        kill -9 "$qwen_pid" 2>/dev/null
    fi

    sleep 5

    # 确认已清理
    if pgrep -f "llama_cpp.server" >/dev/null 2>&1; then
        alert "Qwen 进程残留, 强制清理"
        pkill -9 -f "llama_cpp.server" 2>/dev/null
        sleep 3
    fi

    # 重启 Qwen
    source .venv/bin/activate
    log "启动 Qwen 服务..."
    nohup python -m llama_cpp.server \
        --model model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
        --host 127.0.0.1 --port 8765 \
        --n_gpu_layers -1 --n_ctx 16384 \
        > "$QWEN_LOG" 2>&1 &
    local new_qwen_pid=$!

    # 等待 Qwen 就绪
    local waited=0
    while [ "$waited" -lt 120 ]; do
        if curl -s --max-time 10 http://127.0.0.1:8765/v1/models | grep -q '"object":"list"'; then
            log "Qwen 就绪 (PID $new_qwen_pid, ${waited}s)"
            break
        fi
        if ! kill -0 "$new_qwen_pid" 2>/dev/null; then
            alert "Qwen 启动失败! 查看 $QWEN_LOG"
            return 1
        fi
        sleep 2
        waited=$((waited + 2))
    done

    if [ "$waited" -ge 120 ]; then
        alert "Qwen 启动超时!"
        return 1
    fi

    # 重启推理: 优先用原始命令行, 回退默认
    log "启动推理进程..."
    if [ -s "$CMDLINE_FILE" ]; then
        local infer_cmd
        infer_cmd=$(cat "$CMDLINE_FILE")
        log "回放原始命令: $infer_cmd"
        nohup $infer_cmd > "$LOG" 2>&1 &
    else
        log "无保存的命令行, 用默认配置"
        nohup python -m chimera_agent_baseline.run \
            +experiment=qwen_local agent.tasks='[1,2,3]' \
            > "$LOG" 2>&1 &
    fi
    local new_infer_pid=$!
    log "推理已启动 (PID $new_infer_pid), resume 会跳过已完成 case"
    alert "恢复完成: Qwen=$new_qwen_pid, 推理=$new_infer_pid"

    # 等待推理初始化 (embedding 加载等)
    sleep 30
}

while true; do
    # ── 检查推理进程是否在运行 ──
    infer_pid=$(pgrep -f "chimera_agent_baseline.run" | head -1)
    # 持续保存推理命令行 (进程在时更新, 崩溃后仍有最后一份可用)
    if [ -n "$infer_pid" ] && [ -r "/proc/$infer_pid/cmdline" ]; then
        tr '\0' ' ' < "/proc/$infer_pid/cmdline" > "$CMDLINE_FILE"
    fi
    if [ -z "$infer_pid" ]; then
        # 进程不在 → 可能正常完成或异常退出
        total_done=0
        for t in 1 2 3; do
            d=$(find "$OUTPUT_DIR/task$t" -name 'prediction.json' 2>/dev/null | wc -l)
            total_done=$((total_done + d))
        done
        if [ "$total_done" -ge "$TOTAL_TARGET" ]; then
            log "推理已完成 ($total_done/$TOTAL_TARGET), Watchdog 退出"
            break
        else
            alert "推理进程不存在但仅完成 $total_done/$TOTAL_TARGET, 尝试重启..."
            restart_all "推理进程消失 (完成 $total_done/$TOTAL_TARGET)"
            sleep 60
            continue
        fi
    fi

    # ── 检查 Qwen 进程 ──
    qwen_pid=$(pgrep -f "llama_cpp.server" | head -1)
    if [ -z "$qwen_pid" ]; then
        alert "Qwen 进程消失, 重启..."
        restart_all "Qwen 进程不存在"
        sleep 60
        continue
    fi

    # ── 检查日志时间戳 ──
    # 只追踪有意义的日志行 (processing/form_fill/wrote/loaded 等), 忽略 retry 日志
    # 否则 OpenAI 客户端每 5min 的 retry 日志会重置计时器导致永远不触发
    last_log_line=$(grep -E "^[0-9]{4}-[0-9]{2}-[0-9]{2}" "$LOG" 2>/dev/null | grep -vE "Retrying request|_base_client" | tail -1)
    if [ -z "$last_log_line" ]; then
        log "日志为空或无有意义时间戳, 等待..."
        sleep "$INTERVAL"
        continue
    fi

    # 提取时间戳 (格式: 2026-08-12 14:19:51,104 ...)
    last_ts=$(echo "$last_log_line" | awk '{print $1, $2}' | sed 's/,.*//')
    last_epoch=$(date -d "$last_ts" +%s 2>/dev/null)
    now_epoch=$(date +%s)

    if [ -z "$last_epoch" ]; then
        log "无法解析时间戳: $last_ts, 跳过"
        sleep "$INTERVAL"
        continue
    fi

    idle_sec=$((now_epoch - last_epoch))

    # 当前进度
    done_count=0
    for t in 1 2 3; do
        d=$(find "$OUTPUT_DIR/task$t" -name 'prediction.json' 2>/dev/null | wc -l)
        done_count=$((done_count + d))
    done

    # 当前 case
    current_case=$(echo "$last_log_line" | grep -oE 'case [TTP][^ ]+' | awk '{print $2}')
    [ -z "$current_case" ] && current_case="?"

    log "进度: ${done_count}/${TOTAL_TARGET} | 当前: $current_case | 无活动: ${idle_sec}s / ${TIMEOUT}s"

    # ── 卡死判定 ──
    if [ "$idle_sec" -gt "$TIMEOUT" ]; then
        alert "日志无更新 ${idle_sec}s (超过阈值 ${TIMEOUT}s), 最后 case: $current_case"
        alert "最后日志: $last_log_line"
        restart_all "日志卡住 ${idle_sec}s (case=$current_case)"
        sleep 60
        continue
    fi

    sleep "$INTERVAL"
done

log "Watchdog 退出"
rm -f "$PID_FILE"
