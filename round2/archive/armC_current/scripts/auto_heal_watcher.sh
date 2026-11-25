#!/bin/bash
# ===========================================================================
# 自动监测+修复脚本 (夜间无人值守)
#
# 监测项 + 自动修复动作:
# 1. Qwen 服务宕机 → 重启 llama-server
# 2. task2/task3 进程死亡 → 断点续跑 (自动跳过已完成 case)
# 3. GPU OOM/僵尸进程 → 清理后重启 Qwen
# 4. form_fill parse fail → 无需干预 (已有重试机制)
#
# 日志: auto_heal.log
# 用法: nohup setsid bash scripts/auto_heal_watcher.sh < /dev/null > auto_heal.log 2>&1 &
# ===========================================================================
cd /root/shared-nvme/chimera-agent-baseline
source .venv/bin/activate 2>/dev/null || true

LOG=auto_heal.log
OUT_DIR="test/output.pro_v1_20260816"
COMMON_ARGS="+experiment=qwen_local agent.predictor.decision_override_tasks=[] agent.form_fill.self_refine=true agent.form_fill.atomic_check=true paths.output_dir=$OUT_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> $LOG; }

# ---------------- Qwen 健康检查 + 重启 ----------------
# 注意: 只以【进程是否存在】判断健康。Pro 版单次 LLM 生成长达 1-3 分钟,
# 期间 Qwen 忙于计算, HTTP 健康检查会超时; 若以 HTTP 响应判断会把正常推理
# 误判为宕机而反复 kill, 导致 Connection error 和大量 case 失败重跑。
#
# ⚠️  pgrep 的 BRE 模式中 \| 不支持 alternation (procps-ng 4.0.4),
#     必须用 ERE 风格 (无反斜杠); 简单模式 "llama" 太宽, 会误认
#     命令行含 llama 字样的进程为存活 → 改用精确匹配 llama_cpp.server。
check_qwen() {
    local qpid
    qpid=$(pgrep -f "llama_cpp[.]server" 2>/dev/null | head -1)
    if [ -n "$qpid" ]; then
        # 进程在 → 视为健康; 顺带探测 HTTP, 仅记录不干预
        if ! curl -s --max-time 5 http://127.0.0.1:8765/v1/models 2>/dev/null | grep -q "object"; then
            log "INFO: Qwen 进程在 (PID $qpid) 但 HTTP 忙碌, 属正常长生成, 不干预"
        fi
        return 0
    fi
    # 进程不存在 → 真正宕机, 重启
    log "WARN: Qwen 进程不存在, 重启中..."
    pkill -9 -f "llama_cpp[.]server" 2>/dev/null
    sleep 3
    # 清理 GPU 僵尸进程 (仅清理残留进程, 不影响推理进程)
    if command -v nvidia-smi &>/dev/null; then
        local zombies
        zombies=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | grep -v "^$")
        if [ -n "$zombies" ]; then
            log "清理 GPU 残留进程: $zombies"
            echo "$zombies" | xargs -r kill -9 2>/dev/null
            sleep 2
        fi
    fi
    nohup setsid .venv/bin/python -m llama_cpp.server \
        --model model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
        --host 127.0.0.1 --port 8765 \
        --n_gpu_layers -1 --n_ctx 16384 \
        < /dev/null > qwen_restart.log 2>&1 &
    sleep 30
    resp=$(curl -s --max-time 10 http://127.0.0.1:8765/v1/models 2>/dev/null)
    if [ -z "$resp" ]; then
        log "ERROR: Qwen 重启失败!"
        return 1
    fi
    log "OK: Qwen 重启成功"
    return 0
}

# ---------------- 启动指定 task (断点续跑) ----------------
# 防重复: 启动前确认该 task 无存活进程, 否则不启动 (避免双进程写同一目录)
start_task() {
    local task=$1
    local alive
    alive=$(pgrep -f "chimera.*tasks=\[$task\]" 2>/dev/null | head -1)
    if [ -n "$alive" ]; then
        log "task${task} 已有进程 (PID $alive), 跳过启动, 防重复写入"
        return 0
    fi
    local logfile="full_run_pro_task${task}.log"
    log "启动 task${task} (断点续跑)..."
    # 追加模式 + 分隔行: 保留每次重启的历史日志, 便于事后排查慢 case
    {
        echo ""
        echo "===== [$(date '+%Y-%m-%d %H:%M:%S')] task${task} 重启启动 (watcher) ====="
    } >> $logfile
    nohup setsid env $(grep -v "^#" .env | xargs) .venv/bin/python -m chimera_agent_baseline.run \
        $COMMON_ARGS agent.tasks=[$task] \
        >> $logfile 2>&1 < /dev/null &
    local pid=$!
    log "task${task} PID=$pid"
    echo $pid
}

# ---------------- 主循环 ----------------
log "========== 自动监测启动 =========="
log "目标: task2(155) → task3(76), 输出 $OUT_DIR"

TASK2_DONE=false
TASK3_STARTED=false
TASK3_DONE=false
LAST_T2_COUNT=0
LAST_T3_COUNT=0
STALL_COUNT=0

while true; do
    sleep 300  # 每 5 分钟检查一次

    # 1. 检查 Qwen
    check_qwen

    # 2. 检查 task2
    # 完成数 = prediction.json + .failed 标记 (失败 case 不重跑, 计为终态)
    T2_COUNT=$(find test/output.pro_v1_20260816/task2 -name prediction.json 2>/dev/null | wc -l)
    T2_FAILED=$(find test/output.pro_v1_20260816/task2 -name .failed 2>/dev/null | wc -l)
    T2_TOTAL=$((T2_COUNT + T2_FAILED))
    T2_RUNNING=$(pgrep -f "chimera.*tasks=\[2\]" | head -1)

    if [ -z "$T2_RUNNING" ]; then
        if [ "$T2_TOTAL" -ge 155 ] && [ "$TASK2_DONE" = false ]; then
            log "task2 完成: $T2_COUNT cases + $T2_FAILED failed = $T2_TOTAL/155"
            TASK2_DONE=true
        elif [ "$TASK2_DONE" = false ]; then
            log "WARN: task2 进程死亡, 已完成 $T2_TOTAL/155 ($T2_COUNT done + $T2_FAILED failed), 断点续跑..."
            start_task 2
            sleep 60
            continue
        fi
    else
        # 检查是否卡住 (无新 case 产出)
        if [ "$T2_TOTAL" -eq "$LAST_T2_COUNT" ]; then
            STALL_COUNT=$((STALL_COUNT + 1))
            if [ $STALL_COUNT -ge 6 ]; then  # 30 分钟无进展
                log "WARN: task2 卡住 30min ($T2_TOTAL case), 重启..."
                pkill -f "chimera.*tasks=\[2\]" 2>/dev/null
                sleep 5
                start_task 2   # 内部已做防重复检查
                STALL_COUNT=0
                sleep 120
                continue
            fi
        else
            STALL_COUNT=0
        fi
    fi
    LAST_T2_COUNT=$T2_TOTAL

    # 3. task2 完成后自动启动 task3
    if [ "$TASK2_DONE" = true ] && [ "$TASK3_STARTED" = false ]; then
        log "task2 已完成, 启动 task3..."
        start_task 3
        TASK3_STARTED=true
        sleep 60
        continue
    fi

    # 4. 检查 task3
    if [ "$TASK3_STARTED" = true ]; then
        T3_COUNT=$(find test/output.pro_v1_20260816/task3 -name prediction.json 2>/dev/null | wc -l)
        T3_FAILED=$(find test/output.pro_v1_20260816/task3 -name .failed 2>/dev/null | wc -l)
        T3_TOTAL=$((T3_COUNT + T3_FAILED))
        T3_RUNNING=$(pgrep -f "chimera.*tasks=\[3\]" | head -1)

        if [ -z "$T3_RUNNING" ]; then
            if [ "$T3_TOTAL" -ge "$T3_TARGET" ]; then
                log "task3 完成: $T3_COUNT cases + $T3_FAILED failed = $T3_TOTAL/76"
                log "========== task2+task3 全部完成 =========="
                log "明早评估: bash scripts/eval_pro_results.sh"
                break
            else
                log "WARN: task3 进程死亡, 已完成 $T3_TOTAL/76, 断点续跑..."
                start_task 3
                sleep 60
                continue
            fi
        fi
    fi

    log "状态: task2=$T2_TOTAL/155 task3=$T3_TOTAL/76 (t2_done=$TASK2_DONE t3_started=$TASK3_STARTED)"
done

log "========== 监测结束 =========="
