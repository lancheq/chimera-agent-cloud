#!/bin/bash
# ===========================================================================
# task1 专用守护脚本 (Pro v1 断点续跑)
#
# 监测项 + 自动修复动作:
# 1. Qwen 服务宕机 → 重启 llama-server
# 2. task1 进程死亡 → 断点续跑 (自动跳过已完成 case)
# 3. GPU OOM/僵尸进程 → 清理后重启 Qwen
#
# 日志: auto_heal_task1.log
# 用法: nohup setsid bash scripts/auto_heal_task1.sh < /dev/null > auto_heal_task1.log 2>&1 &
# ===========================================================================
cd /root/shared-nvme/chimera-agent-baseline
source .venv/bin/activate 2>/dev/null || true

LOG=auto_heal_task1.log
# 输出目录从 .chimera_run.json 动态读取（无 manifest 时回退 test/output）
OUT_DIR=$(.venv/bin/python -c "
import json
try:
    d = json.load(open('.chimera_run.json'))
    print(d.get('output_dir', 'test/output'))
except Exception:
    print('test/output')" 2>/dev/null || echo "test/output")

# 每 task 目标数: 优先 manifest pids 按前缀计数, 回退数据目录计数
task_target() {
    local n
    n=$(.venv/bin/python -c "
import json
try:
    p = [str(x) for x in (json.load(open('.chimera_run.json')).get('pids') or [])]
    pref = {'1': 'PT-', '2': 'T2-', '3': 'T3-'}.get('$1', '@@')
    print(sum(1 for x in p if x.startswith(pref)))
except Exception:
    print(0)" 2>/dev/null || echo 0)
    if [ "$n" -gt 0 ]; then echo "$n"; else ls "data/task$1/agent_input" 2>/dev/null | wc -l; fi
}
TARGET=$(task_target 1)   # task1 总 case 数

# 注: 已移除 self_refine=true / atomic_check=true —— Pro v1 时代配置,
# 与 config.yaml 当前默认(均 false)矛盾, 续跑会导致混合配置输出
COMMON_ARGS="+experiment=qwen_local agent.predictor.decision_override_tasks=[] paths.output_dir=$OUT_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> $LOG; }

# ---------------- Qwen 健康检查 + 重启 ----------------
# 只以【进程是否存在】判断健康。Pro 版单次 LLM 生成长达 1-3 分钟,
# 期间 HTTP 健康检查会超时; 若以 HTTP 响应判断会误杀正常推理。
check_qwen() {
    local qpid
    # 精确匹配 llama_cpp.server 进程, 避免宽匹配 "llama" 误认含字样进程为存活
    qpid=$(pgrep -f "llama_cpp[.]server" 2>/dev/null | head -1)
    if [ -n "$qpid" ]; then
        if ! curl -s --max-time 5 http://127.0.0.1:8765/v1/models 2>/dev/null | grep -q "object"; then
            log "INFO: Qwen 进程在 (PID $qpid) 但 HTTP 忙碌, 属正常长生成, 不干预"
        fi
        return 0
    fi
    log "WARN: Qwen 进程不存在, 重启中..."
    pkill -9 -f "llama_cpp[.]server" 2>/dev/null
    sleep 3
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

# ---------------- 启动 task1 (断点续跑) ----------------
# 防重复: 启动前确认 task1 无存活进程
start_task() {
    local alive
    alive=$(pgrep -f "chimera.*tasks=[[]1[]]" 2>/dev/null | head -1)
    if [ -n "$alive" ]; then
        log "task1 已有进程 (PID $alive), 跳过启动, 防重复写入"
        return 0
    fi
    local logfile="full_run_pro_task1.log"
    log "启动 task1 (断点续跑)..."
    {
        echo ""
        echo "===== [$(date '+%Y-%m-%d %H:%M:%S')] task1 重启启动 (watcher) ====="
    } >> $logfile
    # 从 manifest 取该 task 的 pids —— 保住原 run 的 case 范围,
    # 防止 pids=null 遍历全池(含无GT/mock/无标注 case)重演 0828 覆盖事故
    local pids_arg=""
    local tp
    tp=$(.venv/bin/python -c "
import json
try:
    p = [str(x) for x in (json.load(open('.chimera_run.json')).get('pids') or [])]
    pref = {'1': 'PT-', '2': 'T2-', '3': 'T3-'}.get('1', '@@')
    print(','.join(x for x in p if x.startswith(pref)))
except Exception:
    print('')" 2>/dev/null)
    if [ -n "$tp" ]; then
        pids_arg="agent.pids=[$tp]"
    fi
    nohup setsid env $(grep -v "^#" .env | xargs) .venv/bin/python -m chimera_agent_baseline.run \
        $COMMON_ARGS agent.tasks=[1] $pids_arg \
        >> $logfile 2>&1 < /dev/null &
    local pid=$!
    log "task1 PID=$pid 重启参数: tasks=[1] ${pids_arg:0:60}"
    echo $pid
}

# ---------------- 主循环 ----------------
log "========== task1 守护启动 =========="
log "目标: task1($TARGET cases), 输出 $OUT_DIR"

LAST_T1_COUNT=0
STALL_COUNT=0

while true; do
    sleep 300  # 每 5 分钟检查一次

    # 1. 检查 Qwen
    check_qwen

    # 2. 检查 task1
    T1_COUNT=$(find $OUT_DIR/task1 -name prediction.json 2>/dev/null | wc -l)
    T1_FAILED=$(find $OUT_DIR/task1 -name .failed 2>/dev/null | wc -l)
    T1_TOTAL=$((T1_COUNT + T1_FAILED))
    T1_RUNNING=$(pgrep -f "chimera.*tasks=\[1\]" | head -1)

    if [ -z "$T1_RUNNING" ]; then
        if [ "$T1_TOTAL" -ge $TARGET ]; then
            log "task1 完成: $T1_COUNT cases + $T1_FAILED failed = $T1_TOTAL/$TARGET"
            log "========== task1 全部完成 =========="
            break
        else
            log "WARN: task1 进程死亡, 已完成 $T1_TOTAL/$TARGET ($T1_COUNT done + $T1_FAILED failed), 断点续跑..."
            start_task
            sleep 60
            continue
        fi
    else
        # 检查是否卡住 (无新 case 产出)
        if [ "$T1_TOTAL" -eq "$LAST_T1_COUNT" ]; then
            STALL_COUNT=$((STALL_COUNT + 1))
            if [ $STALL_COUNT -ge 6 ]; then  # 30 分钟无进展
                log "WARN: task1 卡住 30min ($T1_TOTAL case), 重启..."
                pkill -f "chimera.*tasks=\[1\]" 2>/dev/null
                sleep 5
                start_task   # 内部已做防重复检查
                STALL_COUNT=0
                sleep 120
                continue
            fi
        else
            STALL_COUNT=0
        fi
    fi
    LAST_T1_COUNT=$T1_TOTAL

    log "状态: task1=$T1_TOTAL/$TARGET"
done

log "========== 监测结束 =========="
