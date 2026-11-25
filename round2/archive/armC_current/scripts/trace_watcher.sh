#!/bin/bash
# ===========================================================================
# Trace Watcher — 持续 tail 日志，提取正在运行 case 的关键事件，打时间戳标记问题
#
# 用法:
#   bash scripts/trace_watcher.sh [log_file]
#   bash scripts/trace_watcher.sh full_run_pro_task1.log
#   bash scripts/trace_watcher.sh full_run_pro_task3.log
#
# 监测内容:
#   - 新 case 开始/完成
#   - plan 生成 (字数)
#   - 工具调用
#   - critique 发现 issues (检测 thinking process 污染)
#   - refine 完成
#   - confidence 降级
#   - fact_check FALSE 检出
#   - form_fill JSON 解析失败
#   - called_tools=[] 未调用工具
# ===========================================================================
cd "$(dirname "$0")/.." || exit 1

LOG="${1:-full_run_pro_task1.log}"
TRACE_DIR="${2:-${CHIMERA_TRACE_DIR:-test/output.pro_v1_20260816/trace}}"

echo "=== Trace Watcher 启动 === 监测: $LOG"
echo "Trace 输出目录: $TRACE_DIR"
echo "---"

tail -f "$LOG" | while read -r line; do
    ts=$(echo "$line" | grep -oE '^[0-9-]+ [0-9:,]+' | head -1)
    case_id=$(echo "$line" | grep -oE '(processing case|plan_node:|self_refine:|reflect_node:|form_fill:|fact_check:)[^ ]+ [A-Za-z0-9_-]+' | grep -oE '[A-Za-z0-9_-]+$')

    # 关键事件标记
    if echo "$line" | grep -q "processing case"; then
        echo "[$ts] >>> 新 case 开始: $case_id"
    elif echo "$line" | grep -q "plan generated"; then
        chars=$(echo "$line" | grep -oE '[0-9]+ chars' | grep -oE '[0-9]+')
        echo "[$ts] [PLAN] $case_id 计划生成 (${chars}字)"
    elif echo "$line" | grep -q "executed.*tools:"; then
        tools=$(echo "$line" | grep -oE "\[.*?\]")
        echo "[$ts] [TOOLS] $case_id 调用工具 $tools"
    elif echo "$line" | grep -q "executed get_"; then
        tool=$(echo "$line" | grep -oE 'get_[a-z_]+' | head -1)
        chars=$(echo "$line" | grep -oE '[0-9]+ chars' | grep -oE '[0-9]+')
        echo "[$ts] [TOOL] $case_id → $tool (${chars}字)"
    elif echo "$line" | grep -q "critique found issues"; then
        # 检查是否是 thinking process 污染
        if echo "$line" | grep -qi "thinking process\|Analyze User Input\|Here's a thinking"; then
            echo "[$ts] ⚠️ [CRITIQUE污染] $case_id critique 输出 thinking process 而非 JSON issues"
        else
            echo "[$ts] [CRITIQUE] $case_id 发现 issues"
        fi
    elif echo "$line" | grep -q "no issues found"; then
        echo "[$ts] [CRITIQUE] $case_id 无问题"
    elif echo "$line" | grep -q "refined successfully"; then
        echo "[$ts] [REFINE] $case_id refine 完成"
    elif echo "$line" | grep -q "confidence downgraded"; then
        echo "[$ts] ✅ [CALIBRATE] $case_id confidence 自动降级"
    elif echo "$line" | grep -q "fact_check.*FALSE"; then
        n=$(echo "$line" | grep -oE '[0-9]+ FALSE' | grep -oE '[0-9]+')
        if [ "$n" -gt "0" ] 2>/dev/null; then
            echo "[$ts] ❌ [FACT_CHECK] $case_id 检出 $n 个 FALSE claim"
        fi
    elif echo "$line" | grep -q "fact_check.*decomposed"; then
        n=$(echo "$line" | grep -oE 'decomposed [0-9]+' | grep -oE '[0-9]+')
        echo "[$ts] [FACT_CHECK] $case_id 分解 $n 个 claims"
    elif echo "$line" | grep -q "parse failed"; then
        echo "[$ts] ⚠️ [PARSE_FAIL] $case_id form_fill JSON 解析失败"
    elif echo "$line" | grep -q "called_tools=\[\]"; then
        echo "[$ts] ⚠️ [NO_TOOLS] $case_id 未调用任何工具 → reveal_sequence 空 → grounding 归零风险"
    elif echo "$line" | grep -q "case.*done in"; then
        secs=$(echo "$line" | grep -oE '[0-9.]+s' | head -1)
        echo "[$ts] <<< case 完成: $case_id 耗时 $secs"
        echo "---"
    fi
done
