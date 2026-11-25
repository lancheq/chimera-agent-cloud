#!/bin/bash
# ===========================================================================
# 下载并部署官方 Gemma-4-E4B-it Q4_K_M 作为 GEval judge (gemma4:e4b)
#
# 来源: hf-mirror (unsloth/gemma-4-E4B-it-GGUF, Q4_K_M, ~4.98GB)
# 与官方 evaluate.py 默认 JUDGE_MODEL=gemma4:e4b 判分对齐
#
# 用法:
#   后台运行: nohup bash scripts/deploy_gemma4_judge.sh > gemma4_download.log 2>&1 &
#   状态:     tail -f gemma4_download.log
#   仅下载:  OLLAMA_CREATE=0 bash scripts/deploy_gemma4_judge.sh
# ===========================================================================
set -e

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
PROJECT_DIR=$(dirname "$SCRIPT_DIR")
cd "$PROJECT_DIR"

OLLAMA_BIN="${OLLAMA_BIN:-/root/shared-nvme/ollama/bin/ollama}"
OLLAMA_MODELS="${OLLAMA_MODELS:-/root/shared-nvme/ollama/models}"
MODEL_GGUF="gemma-4-E4B-it-Q4_K_M.gguf"
DOWNLOAD_URL="https://hf-mirror.com/unsloth/gemma-4-E4B-it-GGUF/resolve/main/${MODEL_GGUF}"
DL_DIR="${DL_DIR:-/root/shared-nvme/ollama/models/lib}"
MODEL_FILE="$DL_DIR/$MODEL_GGUF"
MODEL_TAG="gemma4:e4b"
OLLAMA_CREATE="${OLLAMA_CREATE:-1}"

echo ""
echo "=================================================="
echo " 部署 Gemma-4-E4B-it Q4_K_M -> gemma4:e4b"
echo "  源:    $DOWNLOAD_URL"
echo "  权重:  $MODEL_FILE"
echo "  Ollama: $OLLAMA_BIN (models in $OLLAMA_MODELS)"
echo "=================================================="

mkdir -p "$DL_DIR"

if [ ! -f "$MODEL_FILE" ] || [ ! -s "$MODEL_FILE" ]; then
    echo "[$(date '+%F %T')] 开始下载 (约4.98GB, 当前带宽~600KB/s, 预计1.5-2.5小时)"
    curl -L --fail --retry 5 --retry-delay 10 --continue-at - \
        -o "$MODEL_FILE.tmp" "$DOWNLOAD_URL" \
        -C - 2>&1 | tail -5 || true
    # 下载完成重命名
    if [ -f "$MODEL_FILE.tmp" ] && [ -s "$MODEL_FILE.tmp" ]; then
        mv "$MODEL_FILE.tmp" "$MODEL_FILE"
    fi
else
    echo "[$(date '+%F %T')] 模型已存在: $MODEL_FILE"
fi

/bin/ls -lh "$MODEL_FILE"

if [ "$OLLAMA_CREATE" = "1" ]; then
    echo "[$(date '+%F %T')] 创建 ollama 模型 $MODEL_TAG ..."
    export OLLAMA_MODELS="$OLLAMA_MODELS"
    export OLLAMA_HOST="127.0.0.1:11434"
    MODELEFILE="$DL_DIR/gemma4_e4b.Modelfile"
    cat > "$MODELEFILE" <<EOF
FROM $MODEL_FILE
PARAMETER temperature 1
PARAMETER top_k 64
PARAMETER top_p 0.95
EOF
    "$OLLAMA_BIN" create "$MODEL_TAG" -f "$MODELEFILE"
    echo "[$(date '+%F %T')] 创建完成，验证:"
    "$OLLAMA_BIN" list | grep -E "gemma4" || "$OLLAMA_BIN" list
    curl -s -m 8 "http://127.0.0.1:11434/api/tags"
    echo
    echo "部署完成: $MODEL_TAG"
fi
echo "[$(date '+%F %T')] 全部完成"