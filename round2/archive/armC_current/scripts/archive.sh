#!/bin/bash
# ===========================================================================
# 过程文件归档与清理脚本
#
# 设计原则:
#   - 溯源不丢失: 历史日志/输出快照归档到 data_backup/ 下，按日期分目录
#   - 根目录清爽: 只保留当前运行中的活跃文件
#   - 幂等可重复: 多次执行不报错，不重复归档
#   - 安全不误删: 只移动 .log / output.* 等明确的过程文件，不动源码和配置
#
# 用法:
#   bash scripts/archive.sh              # 归档日志 + 输出快照
#   bash scripts/archive.sh --clean     # 同时清理 __pycache__ 和 .pyc
#   bash scripts/archive.sh --purge      # 删除超过 30 天的归档 (慎用)
# ===========================================================================
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")
cd "$PROJECT_DIR"

ARCHIVE_DIR="data_backup"
LOGS_ARCHIVE="$ARCHIVE_DIR/logs_archive"
OUTPUT_ARCHIVE="$ARCHIVE_DIR/output_snapshots"
STAMP=$(date +%Y%m%d_%H%M)

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
ok()   { log "✅ $*"; }
skip() { log "⏭️  $*"; }

# ── 运行日志归档 ──────────────────────────────────────────────────────────────
# 活跃日志 (当前运行中，保留在根目录):
#   - full_run_continue.log   当前推理日志
#   - qwen_server.log         Qwen 服务日志
#   - auto_recover.log        自恢复日志
#   - watchdog.log            看门狗日志
#
# 非活跃日志 (一次性下载/构建/旧运行，归档):
#   - aria2*, hf_download, modelscope_download, qwen_download, embedding_download
#   - llama_cpp_cuda_rebuild, deploy_cloud
#   - full_run*, smoke*, run.log, monitor_run.log, auto_monitor.log
ARCHIVE_PATTERNS=(
    "aria2" "aria2_stdout" "hf_download" "modelscope_download"
    "qwen_download" "embedding_download" "llama_cpp_cuda_rebuild"
    "deploy_cloud" "full_run.log" "full_run_official" "smoke_test"
    "smoke_continue" "run.log" "auto_monitor" "monitor_run"
    "qwen_server_cuda"
)

mkdir -p "$LOGS_ARCHIVE"

log "=== 归档开始 ==="

# 归档非活跃日志
for pattern in "${ARCHIVE_PATTERNS[@]}"; do
    for f in ${pattern}*.log; do
        [ -f "$f" ] || continue
        # 跳过符号链接
        [ -L "$f" ] && continue
        mv "$f" "$LOGS_ARCHIVE/"
        ok "归档日志: $f → $LOGS_ARCHIVE/"
    done
done

# ── 输出快照归档 ──────────────────────────────────────────────────────────────
# test/output.YYYYMMDD_HHMM/ → data_backup/output_snapshots/output.YYYYMMDD_HHMM/
mkdir -p "$OUTPUT_ARCHIVE"
for d in test/output.*/; do
    [ -d "$d" ] || continue
    dirname=$(basename "$d")
    if [ -d "$OUTPUT_ARCHIVE/$dirname" ]; then
        skip "已存在: $OUTPUT_ARCHIVE/$dirname"
        continue
    fi
    mv "$d" "$OUTPUT_ARCHIVE/$dirname"
    ok "归档输出快照: $d → $OUTPUT_ARCHIVE/$dirname"
done

# ── 可选: 清理 Python 缓存 ────────────────────────────────────────────────────
if [[ "${1:-}" == "--clean" || "${1:-}" == "--purge" ]]; then
    count=$(find src/ -type d -name "__pycache__" 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        find src/ -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
        ok "清理 __pycache__: $count 个目录"
    else
        skip "无 __pycache__ 需清理"
    fi

    count=$(find src/ -name "*.pyc" 2>/dev/null | wc -l)
    if [ "$count" -gt 0 ]; then
        find src/ -name "*.pyc" -delete 2>/dev/null
        ok "清理 .pyc: $count 个文件"
    else
        skip "无 .pyc 需清理"
    fi
fi

# ── 可选: 清理超过 30 天的归档 ────────────────────────────────────────────────
if [[ "${1:-}" == "--purge" ]]; then
    log "检查 30 天以上的归档..."
    purged=0
    for d in "$LOGS_ARCHIVE" "$OUTPUT_ARCHIVE"; do
        if [ -d "$d" ]; then
            while IFS= read -r -d '' old_file; do
                rm -f "$old_file"
                purged=$((purged + 1))
            done < <(find "$d" -type f -mtime +30 -print0 2>/dev/null)
        fi
    done
    if [ "$purged" -gt 0 ]; then
        ok "清理 30 天以上归档: $purged 个文件"
    else
        skip "无 30 天以上归档需清理"
    fi
fi

# ── 状态汇总 ────────────────────────────────────────────────────────────────────
log "=== 归档完成 ==="
log "日志归档目录: $LOGS_ARCHIVE/ ($(find "$LOGS_ARCHIVE" -type f 2>/dev/null | wc -l) 文件, $(du -sh "$LOGS_ARCHIVE" 2>/dev/null | awk '{print $1}'))"
log "输出快照目录: $OUTPUT_ARCHIVE/ ($(find "$OUTPUT_ARCHIVE" -type f 2>/dev/null | wc -l) 文件, $(du -sh "$OUTPUT_ARCHIVE" 2>/dev/null | awk '{print $1}'))"
log "根目录剩余日志:"
for f in *.log; do
    [ -f "$f" ] && log "  $f ($(du -h "$f" | awk '{print $1}'))"
done
log "提示: bash scripts/archive.sh --clean  可同时清理 __pycache__"
log "      bash scripts/archive.sh --purge 可同时清理 30 天以上归档"
