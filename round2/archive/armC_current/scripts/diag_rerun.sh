#!/bin/bash
# ===========================================================================
# 诊断重跑 — 对问题 case 以 DEBUG 级别完整记录全链路过程
#
# 目的: 分析 form_fill 反复失败 / 单 case 卡死 / 耗时异常问题。
#   1. DEBUG 日志: 记录 plan/self_refine/reflect/form_fill/fact_check 每节点
#   2. trace.json: 每个 case 保存完整中间状态 (messages, disease_plan,
#      refined_transcript, reflect_warnings, form_fill_warnings, timing)
#   3. GPU + 进程 30s 快照: 还原推理期间的算力状态
#   4. Qwen 健康检查: 记录 Qwen 进程状态变化
#   5. 独立输出目录: 不影响正式结果 (test/output.diag_*)
#   6. 结束后输出各 case 耗时统计 + 异常分析
#
# 用法:
#   bash scripts/diag_rerun.sh "T2-027,T2-028,T2-042" 2
#   bash scripts/diag_rerun.sh "T3-001,T3-008" 3
#   bash scripts/diag_rerun.sh "T3-001" 3 --no-qwen-check  # 跳过 Qwen 检查
# ===========================================================================
cd /root/shared-nvme/chimera-agent-baseline
source .venv/bin/activate 2>/dev/null || true

CASES="${1:-T2-027,T2-028,T2-042}"
TASK="${2:-2}"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT_DIR="test/output.diag_${STAMP}"
DIAG_LOG="full_run_diag_${STAMP}.log"
QWEN_CHECK=true

# 解析第三个参数
if [ "$3" = "--no-qwen-check" ]; then
    QWEN_CHECK=false
fi

echo "=================================================================="
echo "诊断重跑开始: $(date '+%Y-%m-%d %H:%M:%S')"
echo "cases=$CASES  task=$TASK"
echo "输出目录: $OUT_DIR"
echo "诊断日志: $DIAG_LOG"
echo "trace 文件: $OUT_DIR/task$TASK/<case>/trace.json"
echo "Qwen 健康检查: $QWEN_CHECK"
echo "=================================================================="

START_EPOCH=$(date +%s)

# ── 确保 Qwen 服务在运行 (否则诊断无意义) ──
if [ "$QWEN_CHECK" = true ]; then
    QWEN_PID=$(pgrep -f "llama_cpp[.]server" 2>/dev/null | head -1)
    if [ -z "$QWEN_PID" ]; then
        echo "[$(date '+%H:%M:%S')] ⚠️  Qwen 未运行, 正在启动..."
        nohup setsid .venv/bin/python -m llama_cpp.server \
            --model model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
            --host 127.0.0.1 --port 8765 \
            --n_gpu_layers -1 --n_ctx 16384 \
            < /dev/null > qwen_restart.log 2>&1 &
        QWEN_START_PID=$!
        # 等待 Qwen 就绪 (最多 120s)
        waited=0
        while [ "$waited" -lt 120 ]; do
            if curl -s --max-time 10 http://127.0.0.1:8765/v1/models 2>/dev/null | grep -q "object"; then
                echo "[$(date '+%H:%M:%S')] ✅ Qwen 就绪 (${waited}s)"
                break
            fi
            sleep 3
            waited=$((waited + 3))
        done
        if [ "$waited" -ge 120 ]; then
            echo "[$(date '+%H:%M:%S')] ❌ Qwen 启动失败! 查看 qwen_server.log"
            exit 1
        fi
    else
        echo "[$(date '+%H:%M:%S')] ✅ Qwen 已在运行 (PID $QWEN_PID)"
    fi
fi

# ── 后台监控: GPU / 进程 / Qwen 30s 快照 ──
MONITOR_LOG="${DIAG_LOG}.monitor"
nohup bash -c '
    while true; do
        ts=$(date "+%H:%M:%S")
        gpu=$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null | tr -d "\n")
        infer=$(pgrep -c -f "chimera_agent_baseline.run" 2>/dev/null || echo 0)
        qwen=$(pgrep -c -f "llama" 2>/dev/null || echo 0)
        qwen_http=$(curl -s --max-time 3 http://127.0.0.1:8765/v1/models 2>/dev/null | head -c 50)
        echo "[$ts] SNAPSHOT GPU=$gpu infer=$infer qwen=$qwen qwen_http=$qwen_http"
        sleep 15
    done
' >> "$MONITOR_LOG" 2>&1 &
MON_PID=$!

# ── DEBUG 级别运行推理 (追加到诊断日志) ──
# trace.json 会自动保存到每个 case 的输出目录
echo "===== [$(date '+%Y-%m-%d %H:%M:%S')] 诊断重跑开始 cases=$CASES task=$TASK =====" >> "$DIAG_LOG"

env $(grep -v "^#" .env | xargs) .venv/bin/python -m chimera_agent_baseline.run \
    +experiment=qwen_local \
    agent.tasks=[$TASK] \
    agent.pids=[$CASES] \
    agent.form_fill.self_refine=true \
    agent.form_fill.atomic_check=true \
    agent.form_fill.max_retries=3 \
    paths.output_dir=$OUT_DIR \
    logging.level=DEBUG \
    >> "$DIAG_LOG" 2>&1
RUN_EXIT=$?

kill $MON_PID 2>/dev/null
END_EPOCH=$(date +%s)
ELAPSED=$((END_EPOCH - START_EPOCH))

echo "=================================================================="
echo "诊断重跑结束: $(date '+%Y-%m-%d %H:%M:%S')  退出码=$RUN_EXIT"
echo "总耗时: ${ELAPSED}s ($((ELAPSED/60))m$((ELAPSED%60))s)"
echo "=================================================================="

# ── 输出各 case 耗时统计 ──
echo ""
echo "── 各 case 处理时间线 (从日志提取) ──"
grep -E "processing case|case.*done in|case.*FAILED after" "$DIAG_LOG" | grep "Task $TASK" | tail -30

echo ""
echo "── trace.json 摘要 ──"
for case_dir in $OUT_DIR/task$TASK/*/; do
    if [ -f "$case_dir/trace.json" ]; then
        case_id=$(basename "$case_dir")
        status=$(python3 -c "import json; d=json.load(open('$case_dir/trace.json')); print(f\"{d['status']:8s} {d['elapsed_seconds']:7.1f}s  msgs={len(d.get('messages',[]))}  warnings={len(d.get('reflect_warnings',[]))}\")" 2>/dev/null || echo "parse error")
        echo "  $case_id: $status"
    fi
done

echo ""
echo "── form_fill 失败重试记录 ──"
grep -E "parse failed|all .* attempts failed" "$DIAG_LOG" | tail -20

echo ""
echo "── Qwen 连接错误记录 ──"
grep -iE "Connection error|APIConnectionError|Retrying request" "$DIAG_LOG" | tail -20

echo ""
echo "── GPU/进程监控摘要 (异常快照) ──"
grep -E "SNAPSHOT" "$MONITOR_LOG" | awk '
{
    for(i=1;i<=NF;i++) {
        if($i ~ /qwen=0/) { print "⚠️  " $0; next }
    }
}
{
    # 检查 GPU 利用率异常低 (< 10%)
    match($0, /([0-9]+)%/, gpu_util)
    if (gpu_util[1] != "" && gpu_util[1]+0 < 10 && gpu_util[1]+0 >= 0) {
        print "💤 GPU 空闲: " $0
    }
}' | head -20

echo ""
echo "── 完整日志 ──"
echo "  诊断日志: $DIAG_LOG"
echo "  监控日志: $MONITOR_LOG"
echo "  trace 文件: $OUT_DIR/task$TASK/<case>/trace.json"
echo ""
echo "查看单个 case 的完整中间数据:"
echo "  python3 -m json.tool $OUT_DIR/task$TASK/T3-001/trace.json | less"
echo ""
echo "从 trace.json 提取关键信息:"
echo "  python3 -c \"import json; d=json.load(open('$OUT_DIR/task$TASK/T3-001/trace.json')); print(d.get('refined_transcript','')[:500]); print(d.get('form_fill_warnings',[]))\""
