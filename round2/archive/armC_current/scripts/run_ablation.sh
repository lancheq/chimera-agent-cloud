#!/bin/bash
# ===========================================================================
# self_refine 消融实验 — A/B 对比运行脚本
#
# 配置A (baseline):   self_refine=false, atomic_check=false
# 配置B (self_refine): self_refine=true,  atomic_check=false
#
# 同一批 30 case (T1:12 + T2:10 + T3:8), 各自输出到独立目录。
#
# 用法:
#   nohup bash scripts/run_ablation.sh > ablation_run.log 2>&1 &
#   tail -f ablation_run.log
#
# 完成后自动运行 eval + audit + 生成报告。
# ===========================================================================
set -euo pipefail
cd /root/shared-nvme/chimera-agent-baseline
source .venv/bin/activate 2>/dev/null || true

# ── 从 ablation_cases.json 提取 pids 列表 ──
PIDS=$(.venv/bin/python -c "
import json
with open('scripts/ablation_cases.json') as f:
    data = json.load(f)
print(','.join(data['pids']))
")
N_CASES=$(.venv/bin/python -c "
import json
with open('scripts/ablation_cases.json') as f:
    data = json.load(f)
print(len(data['pids']))
")
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sampled $N_CASES case IDs for ablation"

# ── 配置 ──
EXPERIMENT="+experiment=qwen_local"
COMMON_ARGS="$EXPERIMENT agent.predictor.decision_override_tasks=[] agent.form_fill.atomic_check=false agent.tasks=[1,2,3] agent.pids=[$PIDS]"

# ── Config A: baseline (self_refine=false) ──
OUT_A="test/output.ablation_baseline"
export CHIMERA_OUTPUT_DIR="$OUT_A"
echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Config A: baseline (self_refine=false, atomic_check=false)"
echo "  Output: $OUT_A"
echo "============================================================"
.venv/bin/python -m chimera_agent_baseline.run \
    $COMMON_ARGS \
    agent.form_fill.self_refine=false \
    paths.output_dir=$OUT_A \
    >> ablation_config_a.log 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Config A complete."

# ── Config B: self_refine=true ──
OUT_B="test/output.ablation_selfrefine"
export CHIMERA_OUTPUT_DIR="$OUT_B"
echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Config B: self_refine=true (atomic_check=false)"
echo "  Output: $OUT_B"
echo "============================================================"
.venv/bin/python -m chimera_agent_baseline.run \
    $COMMON_ARGS \
    agent.form_fill.self_refine=true \
    paths.output_dir=$OUT_B \
    >> ablation_config_b.log 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Config B complete."

# ── 评估 ──
echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running evaluation..."
echo "============================================================"
.venv/bin/python scripts/eval_train_gt.py --pred-dir "$OUT_A" --report-suffix "_ablation_baseline" || echo "[WARN] eval baseline had issues"
.venv/bin/python scripts/eval_train_gt.py --pred-dir "$OUT_B" --report-suffix "_ablation_selfrefine" || echo "[WARN] eval selfrefine had issues"

# ── Audit traces ──
echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Running audit_traces.py..."
echo "============================================================"
.venv/bin/python scripts/audit_traces.py \
    --baseline-dir "$OUT_A" \
    --selfrefine-dir "$OUT_B" \
    --cases-json scripts/ablation_cases.json \
    --output scripts/ablation_audit.json || echo "[WARN] audit had issues"

# ── 生成消融报告 ──
echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Generating ablation report..."
echo "============================================================"
.venv/bin/python scripts/gen_ablation_report.py \
    --baseline-dir "$OUT_A" \
    --selfrefine-dir "$OUT_B" \
    --cases-json scripts/ablation_cases.json \
    --audit-json scripts/ablation_audit.json \
    --output scripts/ablation_report.md || echo "[WARN] report generation had issues"

echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ablation experiment complete!"
echo "  Report: scripts/ablation_report.md"
echo "============================================================"
