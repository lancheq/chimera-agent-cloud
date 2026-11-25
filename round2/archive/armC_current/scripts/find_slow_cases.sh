#!/bin/bash
# ===========================================================================
# 从推理日志中找出耗时异常的 case
#
# 原理:
#   1. 从日志中提取每个 case 的 "processing" 和 "done/FAILED" 时间戳
#   2. 计算每个 case 的实际耗时
#   3. 找出耗时超过阈值的异常 case
#   4. 同时检测日志中的 Qwen 重启事件 (大段无产出间隙)
#
# 用法:
#   bash scripts/find_slow_cases.sh full_run_pro_task3.log 3 300
#     ↑ 日志文件                    ↑ task号 ↑ 阈值秒数(默认300=5min)
#
#   bash scripts/find_slow_cases.sh full_run_pro_task2.log 2 600
#
# 输出:
#   - 异常 case 列表 (可直接传给 diag_rerun.sh)
#   - Qwen 重启事件时间线
#   - 每个 case 的耗时统计
# ===========================================================================
LOG="${1:-full_run_pro_task3.log}"
TASK="${2:-3}"
THRESHOLD="${3:-300}"

cd /root/shared-nvme/chimera-agent-baseline

if [ ! -f "$LOG" ]; then
    echo "错误: 日志文件不存在: $LOG"
    echo "可用日志:"
    ls -1 *.log 2>/dev/null | head -20
    exit 1
fi

echo "=================================================================="
echo "分析日志: $LOG (task=$TASK, 阈值=${THRESHOLD}s)"
echo "=================================================================="
echo ""

# ── 1. 提取 case 处理时间线 ──
# 从日志中提取 "processing case X" 和 "case X done in Ns" / "case X FAILED after Ns"
# 如果没有 "done in" (旧日志格式), 则用下一个 case 的 processing 时间减去当前 case 的

echo "── 异常 case (耗时 > ${THRESHOLD}s) ──"
echo ""

# 方法1: 从新格式日志提取 (case X done in Ns)
SLOW_CASES=""
grep -E "case.*done in|case.*FAILED after" "$LOG" | grep "Task $TASK" | while read -r line; do
    # 提取 case_id 和 耗时
    case_id=$(echo "$line" | grep -oE 'case [^ ]+' | awk '{print $2}')
    elapsed=$(echo "$line" | grep -oE '[0-9]+\.[0-9]+s|[0-9]+s' | head -1 | tr -d 's')
    status="done"
    echo "$line" | grep -q "FAILED" && status="FAILED"

    if [ -n "$elapsed" ]; then
        elapsed_int=$(echo "$elapsed" | awk '{printf "%.0f", $1}')
        if [ "$elapsed_int" -gt "$THRESHOLD" ]; then
            echo "  ⚠️  $case_id: ${elapsed}s ($status)"
        fi
    fi
done

# 方法2: 从旧格式日志提取 (processing case 时间间隔)
echo ""
echo "── case 间间隙分析 (旧格式日志, 通过时间戳推算) ──"
echo ""

# 提取所有 processing case 时间戳
grep -E "Task $TASK: processing case" "$LOG" | awk -F'[ ,]' '{
    ts = $1 " " $2;
    gsub(/,/, "", ts);
    case_id = $NF;
    print ts, case_id;
}' | while read -r ts case_id; do
    echo "$ts $case_id"
done > /tmp/case_timeline_$$

# 计算相邻 case 之间的时间差
prev_ts=""
prev_case=""
while read -r ts case_id; do
    if [ -n "$prev_ts" ]; then
        # 计算时间差
        t1=$(date -d "$prev_ts" +%s 2>/dev/null)
        t2=$(date -d "$ts" +%s 2>/dev/null)
        if [ -n "$t1" ] && [ -n "$t2" ]; then
            gap=$((t2 - t1))
            if [ "$gap" -gt "$THRESHOLD" ]; then
                echo "  ⚠️  $prev_case → $case_id: 间隙 ${gap}s ($((gap/60))m$((gap%60))s)"
            fi
        fi
    fi
    prev_ts="$ts"
    prev_case="$case_id"
done < /tmp/case_timeline_$$
rm -f /tmp/case_timeline_$$

# ── 2. Qwen 重启/连接错误事件 ──
echo ""
echo "── Qwen 连接错误/重启事件 ──"
echo ""
conn_errors=$(grep -cE "Connection error|APIConnectionError|Retrying request" "$LOG" 2>/dev/null || echo 0)
echo "  连接错误次数: $conn_errors"
if [ "$conn_errors" -gt 0 ]; then
    grep -E "Connection error|APIConnectionError|Retrying request" "$LOG" | head -10
fi

# ── 3. 失败 case 列表 ──
echo ""
echo "── 失败 case ──"
echo ""
grep -E "case.*FAILED" "$LOG" | grep "Task $TASK" | tail -20

# ── 4. 汇总统计 ──
echo ""
echo "── 汇总统计 ──"
echo ""
total_cases=$(grep -cE "Task $TASK: processing case" "$LOG" 2>/dev/null || echo 0)
done_cases=$(grep -cE "case.*done in" "$LOG" | grep "Task $TASK" 2>/dev/null || echo 0)
failed_cases=$(grep -cE "case.*FAILED" "$LOG" | grep "Task $TASK" 2>/dev/null || echo 0)
echo "  总 case 数: $total_cases"
echo "  成功: $done_cases"
echo "  失败: $failed_cases"

# ── 5. 输出可用于 diag_rerun.sh 的 case 列表 ──
echo ""
echo "── 可用于重跑的异常 case 列表 (逗号分隔) ──"
echo ""
echo "  bash scripts/diag_rerun.sh \"<case_list>\" $TASK"
echo ""
echo "  提示: 从上面的异常 case 中选择需要重跑的, 用逗号分隔"
