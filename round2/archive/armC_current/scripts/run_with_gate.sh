#!/bin/bash
# ===========================================================================
# run_with_gate.sh —— 带连通性体检门的运行包装
#
# 目的：把「运行 → 体检 → 标记」串成一条链，让体检结果**自动生效**，
#       而不是靠人事后想起来去查报告。
#
# 历史动因：08-19 就正确诊断出 fact_check 护栏不触发，写进了"恶化项"，
#           却搁置 39 天才修；09-12 写下"禁用自测下结论"的铁律，
#           09-26 同类问题照旧复发。**报告是一次性文档、铁律靠人记，都不是 gate。**
#
# 用法：
#   bash scripts/run_with_gate.sh +experiment=qwen_local agent.tasks=[1] \
#        agent.pids=[a,b] paths.output_dir=test/output.myrun
#
#   # 只体检已有产物（不跑 agent）：
#   GATE_ONLY=1 OUT=test/output.night_t1_base LOG=/tmp/t1_base.log \
#        bash scripts/run_with_gate.sh
#
# 行为：
#   1. 正常跑 agent（参数原样透传，日志同时落盘）
#   2. 收尾自动执行 connectivity_gate.py all
#   3. 体检不通过 → 在输出目录写 `_UNVERIFIED_GATE_FAILED` 标记，
#      明确声明**本批不可引用**（退出码仍取 agent 的，不掩盖运行失败）
# ===========================================================================
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PY="${PYTHON:-python}"
GATE="tools/connectivity_gate.py"

# 2026-10-30 事故：并发跑批时，后启动的进程会 unlink 并重建 /tmp/chimera_embed.sock，
# 先启动的批次从此丢失向量检索（_dense_search 静默返回 []，只剩 BM25），而日志里
# 只有一条 FileNotFoundError。T2 表批次因此报废 139/154 例。
# 修法：每进程独立 socket；rag.py 已支持 CHIMERA_EMBED_SOCKET。
export CHIMERA_EMBED_SOCKET="${CHIMERA_EMBED_SOCKET:-/tmp/chimera_embed_$$.sock}"

# ---- 只体检模式 ----
if [ "${GATE_ONLY:-0}" = "1" ]; then
    OUT="${OUT:-}"
    LOG="${LOG:-}"
    if [ -z "$OUT" ]; then echo "GATE_ONLY=1 需指定 OUT=<out_dir>"; exit 2; fi
    "$PY" "$GATE" all "$OUT" --log "$LOG" --src src
    rc=$?
    # 通过则清掉历史标记（否则旧标记会一直贴在已修的批次上骗人）
    if [ $rc -ne 0 ]; then touch "$OUT/_UNVERIFIED_GATE_FAILED"; else rm -f "$OUT/_UNVERIFIED_GATE_FAILED"; fi
    exit $rc
fi

# ---- 从参数中提取输出目录与是否显式传了不可信开关 ----
OUT=""
for a in "$@"; do
    case "$a" in
        paths.output_dir=*) OUT="${a#paths.output_dir=}" ;;
    esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG="${GATE_LOG:-/tmp/run_with_gate_${STAMP}.log}"

echo "[run_with_gate] 启动 agent，日志 → $LOG"
"$PY" -m chimera_agent_baseline.run "$@" 2>&1 | tee "$LOG"
RUN_RC=${PIPESTATUS[0]}
echo "[run_with_gate] agent 退出码 $RUN_RC"

if [ -z "$OUT" ]; then
    echo "[run_with_gate] ⚠️ 未提供 paths.output_dir，无法体检（建议总是显式指定）"
    exit $RUN_RC
fi

# 向量检索守卫（2026-10-30）：_dense_search 失败会静默返回 []，产物看不出异常，
# 只能从日志抓。命中即标记，本批不得与干净批次配对比较。
N_DENSE_FAIL=$(grep -c "Dense search failed" "$LOG" 2>/dev/null || true)
if [ "${N_DENSE_FAIL:-0}" -gt 0 ]; then
    echo "[run_with_gate] ✗ 向量检索失效 $N_DENSE_FAIL 次 → 本批 RAG 退化为 BM25-only"
    touch "$OUT/_INVALID_DENSE_FAIL" 2>/dev/null || true
fi

# ---- 收尾体检（即使 agent 部分失败也照跑，以便暴露问题）----
echo
echo "=================== 连通性体检门 ==================="
"$PY" "$GATE" all "$OUT" --log "$LOG" --src src
GATE_RC=$?

if [ $GATE_RC -ne 0 ]; then
    touch "$OUT/_UNVERIFIED_GATE_FAILED"
    echo
    echo "**********************************************************************"
    echo "* 体检未通过：本批已标记 UNVERIFIED（$OUT/_UNVERIFIED_GATE_FAILED）"
    echo "* 按项目纪律：**不得据本批的任何数字下结论**，含消融结论。"
    echo "* 常见原因：机制 0 次触发（未接通）/ 输出悬空 / 派生数据无分折键。"
    echo "**********************************************************************"
else
    rm -f "$OUT/_UNVERIFIED_GATE_FAILED"
    echo "[run_with_gate] 体检通过"
fi

exit $RUN_RC