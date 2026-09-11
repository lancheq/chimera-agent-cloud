#!/bin/bash
# ===========================================================================
# Grand Challenge container entrypoint
#
# Mirrors the school-server deployment (llama_cpp.server on 127.0.0.1:8765):
#   1. Start llama_cpp.server on port 8765 serving the GGUF mounted by GC at
#      /opt/ml/model (all weights ship in model.tar.gz).
#   2. Wait until /v1/models answers.
#   3. exec the agent entrypoint (inference.py connects to openai->8765).
# ===========================================================================
set -u

# curl-free readiness probe. The runtime image ships NEITHER curl NOR wget
# (verified on the real image: `command -v curl` -> not found), which made the
# previous curl-based probe fail silently (`curl: command not found`, stderr
# discarded) while the server was in fact already up -- the GC run then burned
# its whole runtime budget in this loop. python3 + urllib are always present.
_models_ready() {
    python3 -c "
import sys, urllib.request
try:
    body = urllib.request.urlopen('http://127.0.0.1:${PORT}/v1/models', timeout=5).read().decode('utf-8', 'replace')
except Exception:
    sys.exit(1)
sys.exit(0 if 'qwen' in body.lower() else 1)
" 2>/dev/null
}

# CUDA libs ship as pip packages under site-packages/nvidia/*/lib in the
# pytorch base image; add them all so llama.cpp can dlopen libcublas etc.
export LD_LIBRARY_PATH="$(ls -d /opt/conda/lib/python3.11/site-packages/nvidia/*/lib 2>/dev/null | tr '\n' ':')/opt/conda/lib/python3.11/site-packages/torch/lib:$LD_LIBRARY_PATH"

MODEL_FILE="${MODEL_FILE:-/opt/ml/model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf}"
PORT="${QWEN_PORT:-8765}"
N_GPU_LAYERS="${N_GPU_LAYERS:--1}"
# GC gives us a single A10G (~22.6 GiB usable). Q4_K_M weights are ~20.6 GiB, so
# what's left has to hold the KV cache + compute buffers. Measured on a 3090
# with this exact GGUF: 21386 MiB total at n_ctx=4096.
#
# This is a hybrid SSM model (qwen35moe, full_attention_interval=4), so only
# 10 of the 40 blocks keep a KV cache: 2 KV heads x (256+256) dims x f16 =
# 2048 B per block per token => 20 KiB/token overall (llama.cpp reports an
# 80.00 MiB KV buffer at n_ctx=4096). n_ctx is therefore nearly free:
# 16384 -> 320 MiB, and the SSD recurrent-state buffer (62.81 MiB) plus the
# compute buffer (248.51 MiB at n_ubatch=256) do not scale with n_ctx. Total at
# 16384 lands around 21.6 GiB, i.e. ~1 GiB of headroom. 16384 is the value the
# agent prompts were developed against, which keeps long ReAct transcripts from
# overflowing. If the loader ever OOMs, step down 16384 -> 8192 -> 4096.
CTX_SIZE="${QWEN_CTX_SIZE:-16384}"

# The embedding model (sentence-transformers, under /opt/ml/model) is started
# as a subprocess by inference.py. rag.py hardcodes device="cpu" for it, so the
# A10G stays reserved for llama.cpp -- the image's ~1 GiB of VRAM headroom could
# not absorb a second model. This export is belt-and-braces only (it is a no-op
# with that code): set it so any future revision that reintroduces the
# CHIMERA_EMBED_DEVICE override still lands on CPU. encode_query is one short
# string per retrieval, so the CPU cost is negligible.
#
# Note: HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE / telemetry switches are set as
# ENV in Dockerfile_Baseline_nb2 -- without them the embedding and reranker
# loads hang forever on a blackholed network call.
export CHIMERA_EMBED_DEVICE="${CHIMERA_EMBED_DEVICE:-cpu}"

if [ ! -f "$MODEL_FILE" ]; then
    echo "FATAL: model not found at ${MODEL_FILE} (is model.tar.gz mounted at /opt/ml/model?)"
    ls -la /opt/ml/model/ 2>/dev/null
    exit 1
fi

echo "=== starting llama_cpp.server ==="
echo "model: $MODEL_FILE"
echo "port : $PORT (n_ctx=${CTX_SIZE}, n_gpu_layers=${N_GPU_LAYERS})"

# --logits_all false is MANDATORY, not an optimisation.
# llama_cpp/server/settings.py:101 defaults it to True, and llama.py:485 then
# allocates a float32 scores array of n_ctx x n_vocab entries *in host memory*:
# n_vocab is ~248320 here, i.e. ~0.95 GiB per token of context. At n_ctx=4096
# that is 4.1 GB and at 16384 it is 16.3 GB -- which alone would exhaust this
# container's 16 GB and kill the server during load. With it off the array
# shrinks to n_batch x n_vocab (~0.5 GB). Nothing in the agent asks for
# logprobs (verified: no "logprobs" anywhere under src/), so this is safe.
# --n_ubatch 256 (default 512) halves the compute buffer, 497 -> 248.5 MiB, and
# matches the configuration the 3090 fit test above was measured with.
LLAMA_API_KEY="${LLAMA_API_KEY:-no-key}" \
python3 -m llama_cpp.server \
    --model "$MODEL_FILE" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --n_gpu_layers "$N_GPU_LAYERS" \
    --n_ctx "$CTX_SIZE" \
    --n_batch 512 \
    --n_ubatch 256 \
    --logits_all false \
    --flash_attn true \
    --chat_template_kwargs '{"enable_thinking": false}' \
    > /tmp/qwen_server.log 2>&1 &
SERVER_PID=$!

echo "server pid ${SERVER_PID}, waiting for /v1/models ..."
READY=0
for i in $(seq 1 120); do
    if _models_ready; then
        echo "server ready on ${PORT} (${i}0s)"
        READY=1
        break
    fi
    # The server writes to /tmp/qwen_server.log; mirror it to stdout so the GC
    # run log shows *why* startup is stuck (slow disk read vs. CUDA OOM) instead
    # of the total silence that made the last failure undiagnosable.
    if [ $((i % 6)) -eq 0 ]; then
        echo "--- still loading (${i}0s) ---"
        tail -3 /tmp/qwen_server.log 2>/dev/null
    fi
    # Bail out immediately when the loader died, instead of idling away the
    # whole runtime budget waiting for a process that is already gone.
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "FATAL: llama_cpp.server exited during load; log tail:"
        tail -40 /tmp/qwen_server.log
        exit 1
    fi
    sleep 10
done

if [ "$READY" -ne 1 ]; then
    echo "FATAL: server never answered /v1/models; log tail:"
    tail -40 /tmp/qwen_server.log
    exit 1
fi

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "FATAL: llama_cpp.server exited early; log tail:"
    tail -40 /tmp/qwen_server.log
    exit 1
fi

_models_ready || {
    echo "FATAL: server up but /v1/models did not list the model; log tail:"
    tail -40 /tmp/qwen_server.log
    exit 1
}

echo "=== launching agent entrypoint ==="
exec python3 inference.py