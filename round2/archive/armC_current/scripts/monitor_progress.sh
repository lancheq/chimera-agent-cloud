#!/bin/bash
# ===========================================================================
# 推理进度监控 — 实时显示各 task 完成数、速度、剩余时间
#
# 用法:
#   bash scripts/monitor_progress.sh                              # 实时刷新, 自动检测活跃输出目录
#   bash scripts/monitor_progress.sh 5                            # 5 秒刷新
#   bash scripts/monitor_progress.sh 0                            # 只打印一次
#   bash scripts/monitor_progress.sh 0 <output_dir> [log]         # 指定输出目录/日志
#     例: bash scripts/monitor_progress.sh 0 test/output.pro_v1_20260816 full_run_pro_task2.log
#
# 无参数时自动检测: 从运行中的推理进程命令行提取 paths.output_dir 与 task 编号,
# 从而对准当前真实输出目录 (默认回退 test/output + full_run_continue.log)。
# ===========================================================================
cd /root/shared-nvme/chimera-agent-baseline

INTERVAL="${1:-15}"
OUTPUT_DIR="${2:-}"
LOG="${3:-}"

MANIFEST=".chimera_run.json"
M_OUTPUT_DIR=""; M_DATA_ROOT=""; M_LOG=""; M_TASKS=""; M_PIDS=""
M_LIMIT=""; M_START=""; M_STATUS=""; M_PID=""
ACTIVE_PID=""; ACTIVE_CMD=""; ACTIVE_TASKS=""; ACTIVE_PIDS=""; ACTIVE_LIMIT=""

# 1. 清单优先: run.py 启动时写入 .chimera_run.json, 进程退出后仍存在,
#    始终能定位"当前/最近一次"运行 (停止进程也不会丢失定向).
load_manifest() {
    [ -f "$MANIFEST" ] || return 1
    local line
    line=$(.venv/bin/python -c "
import json,sys
try:
    d=json.load(open('$MANIFEST'))
except Exception:
    sys.exit(1)
# 用 \x1f (单元分隔符, 非空白) 连接字段, 避免空字段被 bash read 折叠错位
sep=chr(31)
print(sep.join([
    str(d.get('output_dir','') or ''),
    str(d.get('data_root','') or ''),
    str(d.get('log_file','') or ''),
    ','.join(str(t) for t in d.get('tasks',[])),
    ','.join(d.get('pids') or []),
    str(d.get('limit') or ''),
    str(d.get('start_time','') or ''),
    str(d.get('status','') or ''),
    str(d.get('pid','') or ''),
]))
" 2>/dev/null) || return 1
    [ -z "$line" ] && return 1
    IFS=$'\x1f' read -r M_OUTPUT_DIR M_DATA_ROOT M_LOG M_TASKS M_PIDS M_LIMIT M_START M_STATUS M_PID <<< "$line"
}

# 2. 活跃进程检测: 仅用于"推理: 运行中"状态行与存活确认 (不再作为唯一定向依据).
detect_active_pid() {
    ACTIVE_PID=$(pgrep -n -f "chimera_agent_baseline.run" 2>/dev/null)
    [ -n "$ACTIVE_PID" ] || return
    ACTIVE_CMD=$(ps -p "$ACTIVE_PID" -o cmd --no-headers 2>/dev/null | tr '\n' ' ')
}

# 解析优先级: CLI 参数 > (活跃进程匹配清单? 清单 : 进程命令行) > 另一来源 > 默认值
# 关键: 当活跃进程 PID 与清单记录的 PID 不一致时, 清单已过期 (新运行刚启动,
#       清单尚未刷新), 应以活跃进程命令行为准 —— 这样新增任务/换 run 都能即时对准.
resolve_target() {
    load_manifest
    detect_active_pid
    # 是否信任活跃进程命令行而非清单 (清单过期 或 无清单 但有进程)
    local trust_proc=0
    if [ -n "$ACTIVE_PID" ]; then
        [ -z "$M_PID" ] && trust_proc=1
        [ "$ACTIVE_PID" != "$M_PID" ] && trust_proc=1
    fi
    # 辅助: 从命令行解析某 override
    _cmd_output_dir() { echo "$ACTIVE_CMD" | grep -oE 'paths\.output_dir=[^ ]+' | head -1 | cut -d= -f2; }
    _cmd_tasks()       { echo "$ACTIVE_CMD" | grep -oE 'agent\.tasks=\[[^]]*\]' | head -1 | tr -d '[]' | cut -d= -f2; }
    _cmd_pids()        { echo "$ACTIVE_CMD" | grep -oE 'agent\.pids=\[[^]]*\]' | head -1 | tr -d '[]' | cut -d= -f2; }
    _cmd_limit()       { echo "$ACTIVE_CMD" | grep -oE 'agent\.limit=[^ ]+' | head -1 | cut -d= -f2; }

    # 输出目录: CLI > (信任进程? 命令行 : 清单) > 另一来源 > 默认
    if [ -z "$OUTPUT_DIR" ]; then
        if [ "$trust_proc" = "1" ]; then OUTPUT_DIR=$(_cmd_output_dir); fi
        [ -z "$OUTPUT_DIR" ] && OUTPUT_DIR="${M_OUTPUT_DIR}"
        [ -z "$OUTPUT_DIR" ] && [ -n "$ACTIVE_PID" ] && OUTPUT_DIR=$(_cmd_output_dir)
        [ -z "$OUTPUT_DIR" ] && OUTPUT_DIR="test/output"
    fi
    # 数据根目录 (count_targets 依赖, 不再硬编码 data/)
    [ -z "$DATA_ROOT" ] && DATA_ROOT="${M_DATA_ROOT}"
    [ -z "$DATA_ROOT" ] && DATA_ROOT="data"
    # 日志
    if [ -z "$LOG" ]; then
        if [ "$trust_proc" = "1" ] && [ -n "$ACTIVE_PID" ]; then
            local real_log
            real_log=$(readlink "/proc/$ACTIVE_PID/fd/1" 2>/dev/null)
            [ -n "$real_log" ] && [ -f "$real_log" ] && LOG=$(basename "$real_log")
        fi
        [ -z "$LOG" ] && LOG="${M_LOG}"
        if [ -z "$LOG" ] && [ -n "$ACTIVE_PID" ]; then
            local real_log
            real_log=$(readlink "/proc/$ACTIVE_PID/fd/1" 2>/dev/null)
            [ -n "$real_log" ] && [ -f "$real_log" ] && LOG=$(basename "$real_log")
        fi
        [ -z "$LOG" ] && LOG="full_run_continue.log"
    fi
    # 任务/子集 (允许乱序: agent.tasks=[3,1] 按声明顺序展示)
    if [ -z "$ACTIVE_TASKS" ]; then
        [ "$trust_proc" = "1" ] && ACTIVE_TASKS=$(_cmd_tasks)
        [ -z "$ACTIVE_TASKS" ] && ACTIVE_TASKS="${M_TASKS}"
        [ -z "$ACTIVE_TASKS" ] && [ -n "$ACTIVE_PID" ] && ACTIVE_TASKS=$(_cmd_tasks)
        [ -z "$ACTIVE_TASKS" ] && ACTIVE_TASKS="1,2,3"
    fi
    if [ -z "$ACTIVE_PIDS" ]; then
        [ "$trust_proc" = "1" ] && ACTIVE_PIDS=$(_cmd_pids)
        [ -z "$ACTIVE_PIDS" ] && ACTIVE_PIDS="${M_PIDS}"
        [ -z "$ACTIVE_PIDS" ] && [ -n "$ACTIVE_PID" ] && ACTIVE_PIDS=$(_cmd_pids)
    fi
    if [ -z "$ACTIVE_LIMIT" ]; then
        [ "$trust_proc" = "1" ] && ACTIVE_LIMIT=$(_cmd_limit)
        [ -z "$ACTIVE_LIMIT" ] && ACTIVE_LIMIT="${M_LIMIT}"
        [ -z "$ACTIVE_LIMIT" ] && [ -n "$ACTIVE_PID" ] && ACTIVE_LIMIT=$(_cmd_limit)
    fi
}

resolve_target

count_predictions() {
    local task=$1
    local dir="$OUTPUT_DIR/task$task"
    # 共享输出目录时, 仅统计属于本运行 pids 的 case, 避免历史预测干扰进度.
    # pid→task 归属通过数据目录判定 (data_root/taskN/agent_input/<cid> 存在),
    # 不依赖 pid 命名约定, 兼容 T3-001 / BX_01 等任意 case_id.
    if [ -n "$ACTIVE_PIDS" ]; then
        local n=0 cid
        for cid in $(echo "$ACTIVE_PIDS" | tr ',' '\n'); do
            [ -d "${DATA_ROOT}/task${task}/agent_input/${cid}" ] && [ -f "$dir/$cid/prediction.json" ] && n=$((n+1))
        done
        echo "$n"
        return
    fi
    [ -d "$dir" ] && find "$dir" -name 'prediction.json' 2>/dev/null | wc -l || echo 0
}

count_targets() {
    local task=$1
    # agent.pids 手选 case → 目标数即属于该 task 的 pid 数 (按数据目录归属归类)
    if [ -n "$ACTIVE_PIDS" ]; then
        local n=0 cid
        for cid in $(echo "$ACTIVE_PIDS" | tr ',' '\n'); do
            [ -d "${DATA_ROOT}/task${task}/agent_input/${cid}" ] && n=$((n+1))
        done
        echo "$n"
        return
    fi
    # 否则取整库数量, 但 agent.limit 时不超过 limit
    local n
    n=$(ls "${DATA_ROOT}/task${task}/agent_input/" 2>/dev/null | wc -l)
    if [ -n "$ACTIVE_LIMIT" ] && [ "$ACTIVE_LIMIT" -gt 0 ] && [ "$n" -gt "$ACTIVE_LIMIT" ]; then
        echo "$ACTIVE_LIMIT"
    else
        echo "$n"
    fi
}

count_failed() {
    grep -cE "case .* FAILED" "$LOG" 2>/dev/null || echo 0
}

print_status() {
    local total_done=0 total_target=0 task_line=""
    # 仅展示当前运行实际涉及的 task (无活跃进程时回退 1,2,3)
    local tasks="${ACTIVE_TASKS:-1,2,3}"
    while IFS= read -r t; do
        [ -z "$t" ] && continue
        local d n
        d=$(count_predictions "$t")
        n=$(count_targets "$t")
        total_done=$((total_done + d))
        total_target=$((total_target + n))
        [ -n "$task_line" ] && task_line+="    "
        task_line+="task$t: $d / $n"
    done < <(echo "$tasks" | tr ',' '\n')

    local failed
    failed=$(count_failed)

    # 推理进程状态: 活跃进程优先; 否则回退到清单状态 (停止进程后仍能区分 完成/失败/未运行)
    local infer_pid infer_etime
    infer_pid="$ACTIVE_PID"
    if [ -n "$infer_pid" ]; then
        infer_etime=$(ps -p "$infer_pid" -o etime --no-headers 2>/dev/null | tr -d ' ')
        infer_status="运行中 (PID $infer_pid, 已运行 $infer_etime)"
    elif [ -n "$M_STATUS" ]; then
        infer_status="已停止 (状态: $M_STATUS, 启动: $M_START)"
    else
        infer_status="未运行"
    fi

    # Qwen 状态
    local qwen_pid
    qwen_pid=$(pgrep -f "llama_cpp.server" | head -1)
    if [ -n "$qwen_pid" ]; then
        qwen_status="在线 (PID $qwen_pid)"
    else
        qwen_status="离线"
    fi

    # GPU
    local gpu_util gpu_mem
    gpu_util=$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null | awk -F', ' '{print $1 " | " $2}')
    [ -z "$gpu_util" ] && gpu_util="N/A"

    # 速度估算: 取日志最近 10 个 case 的时间戳
    local speed="N/A" eta="N/A"
    local recent_times
    recent_times=$(grep -E "processing case" "$LOG" 2>/dev/null | tail -10 | awk '{print $1, $2}' | sed 's/,.*//')
    if [ $(echo "$recent_times" | wc -l) -ge 2 ]; then
        local first_ts last_ts
        first_ts=$(echo "$recent_times" | head -1)
        last_ts=$(echo "$recent_times" | tail -1)
        local first_epoch last_epoch elapsed_cases elapsed_sec
        first_epoch=$(date -d "$first_ts" +%s 2>/dev/null)
        last_epoch=$(date -d "$last_ts" +%s 2>/dev/null)
        elapsed_cases=$(echo "$recent_times" | wc -l)
        if [ -n "$first_epoch" ] && [ -n "$last_epoch" ] && [ "$last_epoch" -gt "$first_epoch" ]; then
            elapsed_sec=$((last_epoch - first_epoch))
            local sec_per_case
            sec_per_case=$(awk "BEGIN {printf \"%.0f\", $elapsed_sec / ($elapsed_cases - 1)}")
            speed="${sec_per_case}s/case"
            local remaining=$((total_target - total_done))
            if [ "$remaining" -gt 0 ] && [ "$sec_per_case" -gt 0 ]; then
                local remaining_sec=$((remaining * sec_per_case))
                local remaining_h=$((remaining_sec / 3600))
                local remaining_m=$(( (remaining_sec % 3600) / 60 ))
                eta="${remaining_h}h${remaining_m}m"
            else
                eta="完成"
            fi
        fi
    fi

    # 当前正在处理的 case
    local current_case
    current_case=$(grep -E "processing case" "$LOG" 2>/dev/null | tail -1 | grep -oE 'case [^ ]+' | awk '{print $2}')
    [ -z "$current_case" ] && current_case="N/A"

    # 进度条
    local pct=0
    if [ "$total_target" -gt 0 ]; then
        pct=$((total_done * 100 / total_target))
    fi
    local bar_len=30
    local filled=$((pct * bar_len / 100))
    local empty=$((bar_len - filled))
    local bar=""
    for i in $(seq 1 $filled 2>/dev/null); do bar+="█"; done
    for i in $(seq 1 $empty 2>/dev/null); do bar+="░"; done

    # 输出
    echo "┌─────────────────────────────────────────────────────┐"
    echo "│ CHIMERA-agent 推理进度  $(date '+%Y-%m-%d %H:%M:%S')          │"
    echo "├─────────────────────────────────────────────────────┤"
    printf "│ %-52s │\n" "目标: $OUTPUT_DIR  ($LOG)"
    printf "│ %-52s │\n" "进度: $total_done / $total_target  ($pct%)"
    printf "│ %-52s │\n" "       [$bar]"
    printf "│ %-52s │\n" "$task_line"
    printf "│ %-52s │\n" "失败: $failed    速度: $speed    剩余: $eta"
    printf "│ %-52s │\n" "当前 case: $current_case"
    printf "│ %-52s │\n" "推理: $infer_status"
    printf "│ %-52s │\n" "Qwen: $qwen_status"
    printf "│ %-52s │\n" "GPU:  $gpu_util"
    echo "└─────────────────────────────────────────────────────┘"

    # 完成提醒
    if [ "$total_done" -ge "$total_target" ] && [ "$total_target" -gt 0 ]; then
        echo "🎉 全部完成！记得归档: cp -r $OUTPUT_DIR $OUTPUT_DIR.\$(date +%Y%m%d_%H%M)"
    elif [ -z "$infer_pid" ] && [ "$total_done" -lt "$total_target" ]; then
        # 进程不在但未完成: 区分 清单标记失败 / 清单标记完成(异常) / 无清单疑似卡死
        if [ "$M_STATUS" = "failed" ]; then
            echo "❌ 上次运行已失败退出 (启动: $M_START), 检查日志: $LOG"
        elif [ "$M_STATUS" = "done" ]; then
            echo "ℹ️  清单标记完成但预测数 < 目标, 可能部分 case 被跳过, 检查: $LOG"
        else
            echo "⚠️  推理进程未运行且未完成！恢复: bash scripts/auto_recover.sh"
        fi
    fi
}

# 单次模式
if [ "$INTERVAL" = "0" ]; then
    print_status
    exit 0
fi

# 实时刷新
echo "每 ${INTERVAL}s 刷新一次 (Ctrl+C 退出)"
echo ""
while true; do
    clear
    print_status
    sleep "$INTERVAL"
done
