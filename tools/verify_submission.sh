#!/usr/bin/env bash
# =============================================================================
# verify_submission.sh — one-command pre-submission verification
#
# Runs every check this project accumulated, against the tarball that is about
# to be uploaded (not just the local build cache), and prints a single verdict.
#
#   bash tools/verify_submission.sh [path/to/image.tar.gz] [image-tag]
#
# Identity defaults name the released artifact (v13). A locally rebuilt image
# cannot be byte-identical, so a mismatch is reported as a NOTE unless you ask
# for strictness:
#
#   STRICT_IDENTITY=1 bash tools/verify_submission.sh ...
#
# Suites that need challenge material (annotated ground truth, archived per-case
# outputs, model weights) are SKIPPED unless you point the matching variables at
# your own copies — that material is deliberately not redistributed here:
#
#   CHIMERA_CASES_DIR=<dir mounted at /cases>
#   CHIMERA_MODEL_DIR=<dir mounted at /opt/ml/model>
#   CHIMERA_ARCHIVE_DIR / CHIMERA_ALIGN_MAPPING_DIR / CHIMERA_TRAIN_RELEASE
# =============================================================================
set -u

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
PROBES="$PROJ/tools/probes"
TARBALL="${1:-$PROJ/chimera-agent.tar.gz}"
TAG="${2:-chimera-agent:verify}"

# Released artifact = v13 (v12 + task-2 active_treatment prior correction, τ=0.30).
EXPECTED_ID="${EXPECTED_ID:-sha256:59e50698615b92a5392118a30bd2190bbd2e61b8d52fda8d7d098a7210bd7a16}"
EXPECTED_MD5="${EXPECTED_MD5:-d67b8064d3a29ac3102a4979720c2a79}"
STRICT_IDENTITY="${STRICT_IDENTITY:-0}"

CASES_DIR="${CHIMERA_CASES_DIR:-}"
MODEL_DIR="${CHIMERA_MODEL_DIR:-}"

PASS=0; FAIL=0; SKIP=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
skip() { printf '  \033[33mSKIP\033[0m %s\n' "$1"; SKIP=$((SKIP+1)); }
note() { printf '       %s\n' "$1"; }

echo "=============================================================="
echo " CHIMERA-agent pre-submission verification"
echo " tarball : $TARBALL"
echo " tag     : $TAG"
echo "=============================================================="

# --- 1. artifact identity ---------------------------------------------------
echo
echo "[1] artifact identity"
if [ ! -f "$TARBALL" ]; then bad "tarball missing: $TARBALL"; echo; echo "VERDICT: 1 FAILED"; exit 1; fi
ok "tarball exists ($(stat -f %z "$TARBALL" 2>/dev/null || stat -c %s "$TARBALL") bytes)"
MD5="$(md5 -q "$TARBALL" 2>/dev/null || md5sum "$TARBALL" | cut -d' ' -f1)"
if [ "$MD5" = "$EXPECTED_MD5" ]; then
  ok "md5 = $MD5 (released artifact)"
elif [ "$STRICT_IDENTITY" = "1" ]; then
  bad "md5 = $MD5 (expected $EXPECTED_MD5)"
else
  note "md5 = $MD5 — not the released build (expected $EXPECTED_MD5); this is normal for a rebuild"
fi

# --- 2. load the artifact itself -------------------------------------------
echo
echo "[2] docker load from the artifact (not the build cache)"
if pigz -dc "$TARBALL" 2>/dev/null | docker load >/tmp/_load.out 2>&1 \
   || gunzip -c "$TARBALL" 2>/dev/null | docker load >/tmp/_load.out 2>&1; then
  ok "loaded ($(grep -o 'Loaded image:.*' /tmp/_load.out | head -1))"
else
  bad "docker load failed"; tail -3 /tmp/_load.out | while read -r l; do note "$l"; done
  echo; echo "VERDICT: $FAIL FAILED"; exit 1
fi
# The tarball carries its build-time tag, so re-tag by the expected digest to
# keep the rest of the script independent of how the image was built.
docker tag "$EXPECTED_ID" "$TAG" >/dev/null 2>&1
ID="$(docker inspect --format '{{.Id}}' "$TAG" 2>/dev/null)"
if [ "$ID" = "$EXPECTED_ID" ]; then
  ok "image id = $ID"
elif [ "$STRICT_IDENTITY" = "1" ]; then
  bad "image id = $ID (expected $EXPECTED_ID)"
else
  note "image id = $ID — not the released build; this is normal for a rebuild"
fi

# --- 3. static image checks -------------------------------------------------
echo
echo "[3] static image checks"
CFG="$(docker run --rm --entrypoint bash "$TAG" -lc 'ls -1 /opt/app/configs/' 2>/dev/null | tr -d '\r')"
[ "$CFG" = "config.yaml" ] && ok "configs/ carries only config.yaml" || bad "configs/ = [$CFG]"
CPU="$(docker run --rm --entrypoint bash "$TAG" -lc 'stat -c %s /opt/conda/lib/python3.11/site-packages/llama_cpp/lib/libggml-cpu.so.0.20.0' 2>/dev/null | tr -d '\r')"
[ "$CPU" = "1067088" ] && ok "clean CPU lib (no AVX512) = $CPU B" || bad "CPU lib = $CPU B (expected 1067088)"
ENV_N="$(docker run --rm --entrypoint bash "$TAG" -lc 'env | grep -cE "HF_HUB_OFFLINE|TRANSFORMERS_OFFLINE|HF_HUB_DISABLE_TELEMETRY|HF_HUB_DISABLE_PROGRESS_BARS|ANONYMIZED_TELEMETRY|DO_NOT_TRACK|TOKENIZERS_PARALLELISM"' 2>/dev/null | tr -d '\r')"
[ "$ENV_N" = "8" ] && ok "offline ENV present (8)" || bad "offline ENV count = $ENV_N (expected 8)"
ALIGN="$(docker run --rm --entrypoint bash "$TAG" -lc 'test -f /opt/app/reasoning_align.py && echo yes' 2>/dev/null | tr -d '\r')"
[ "$ALIGN" = "yes" ] && ok "reasoning_align.py shipped into /opt/app" || bad "reasoning_align.py MISSING from image"

# --- 4. functional suites ---------------------------------------------------
run_suite() { # name, expected-substring, command...
  local name="$1"; shift
  local out; out="$("$@" 2>&1 | grep -v '^WARNING: The requested')"
  if echo "$out" | grep -q "RESULT: ALL PASS"; then ok "$name"; else
    bad "$name"; echo "$out" | grep -E "FAIL|Error|Traceback" | head -3 | while read -r l; do note "$l"; done
  fi
}
echo
echo "[4] functional suites (probes in tools/probes/, mounted at /mnt)"
# These four need nothing but the image and the probes.
run_suite "socket contract" docker run --rm --network=none \
  -v "$PROBES":/mnt:ro --entrypoint python3 "$TAG" /mnt/socket_contract_test.py
run_suite "reasoning schema" docker run --rm --network=none \
  -v "$PROBES":/mnt:ro --entrypoint python3 "$TAG" /mnt/reasoning_schema_test.py
run_suite "socket E2E (hostile inputs)" docker run --rm --network=none \
  -v "$PROBES":/mnt:ro --entrypoint python3 "$TAG" /mnt/reasoning_socket_e2e_test.py
# Container-conditions contract test.  The suites above all run with a writable
# CWD, so none of them can see "writes to a path that is only unwritable inside
# the container".  This runs as the image's real non-root user from its real WORKDIR.
run_suite "container conditions" docker run --rm --platform=linux/amd64 --network=none \
  -v "$PROBES":/mnt:ro --entrypoint python3 "$TAG" /mnt/container_conditions_test.py

# These three additionally need challenge material or model weights.
if [ -n "$CASES_DIR" ] && [ -d "$CASES_DIR" ]; then
  run_suite "mcp case data" docker run --rm --network=none \
    -v "$CASES_DIR":/cases:ro -v "$PROBES":/mnt:ro \
    --entrypoint python3 "$TAG" /mnt/mcp_case_data_test.py
else
  skip "mcp case data (set CHIMERA_CASES_DIR to a directory holding the case inputs)"
fi

ALIGN_MAPPING_DIR="${CHIMERA_ALIGN_MAPPING_DIR:-}"
ALIGN_ARCHIVE_DIR="${CHIMERA_ARCHIVE_DIR:-}"
if [ -n "$CASES_DIR" ] && [ -n "$ALIGN_MAPPING_DIR" ] && [ -n "$ALIGN_ARCHIVE_DIR" ] \
   && [ -d "$ALIGN_MAPPING_DIR" ] && [ -d "$ALIGN_ARCHIVE_DIR" ]; then
  run_suite "reasoning alignment" docker run --rm --network=none \
    -e ALIGN_SECTION_MAPPING=/mapping/section_variable_mapping.json \
    -v "$PROBES":/mnt:ro -v "$CASES_DIR":/cases:ro \
    -v "$ALIGN_ARCHIVE_DIR":/archroot:ro -v "$ALIGN_MAPPING_DIR":/mapping:ro \
    --entrypoint python3 "$TAG" /mnt/reasoning_alignment_test.py
else
  skip "reasoning alignment (needs CHIMERA_CASES_DIR + CHIMERA_ALIGN_MAPPING_DIR + CHIMERA_ARCHIVE_DIR)"
fi

if [ -n "$MODEL_DIR" ] && [ -d "$MODEL_DIR" ]; then
  run_suite "predictor cross-version fix" docker run --rm --network=none \
    -v "$CASES_DIR":/cases:ro -v "$MODEL_DIR":/opt/ml/model:ro -v "$PROBES":/mnt:ro \
    --entrypoint python3 "$TAG" /mnt/predictor_fix_verify.py
else
  skip "predictor cross-version fix (set CHIMERA_MODEL_DIR to the model mount)"
fi

# --- 5. the offline score regression (no docker needed) --------------------
echo
echo "[5] offline score regression (official evaluate.py semantics)"
REGRESS="$PROJ/tools/reasoning_alignment_regress.py"
if [ -z "${CHIMERA_TRAIN_RELEASE:-}" ] || [ ! -d "${CHIMERA_TRAIN_RELEASE:-}" ] \
   || [ -z "${CHIMERA_ARCHIVE_DIR:-}" ] || [ ! -d "${CHIMERA_ARCHIVE_DIR:-}" ]; then
  skip "reasoning_alignment_regress.py (needs CHIMERA_TRAIN_RELEASE + CHIMERA_ARCHIVE_DIR)"
elif python3 "$REGRESS" > /tmp/_regress.out 2>&1; then
  ok "reasoning_alignment_regress.py ran"
  grep -E "var_weight=|section_grounding on" /tmp/_regress.out | sed 's/^/       /'
else
  bad "reasoning_alignment_regress.py failed"; tail -3 /tmp/_regress.out | while read -r l; do note "$l"; done
fi

# --- verdict ----------------------------------------------------------------
echo
echo "=============================================================="
if [ "$FAIL" -eq 0 ]; then
  echo " VERDICT: ALL $PASS CHECKS PASSED ($SKIP skipped) — safe to upload $TARBALL"
  echo " platform digest to expect: $EXPECTED_ID"
else
  echo " VERDICT: $FAIL FAILED / $PASS passed / $SKIP skipped — DO NOT UPLOAD"
fi
echo "=============================================================="
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
