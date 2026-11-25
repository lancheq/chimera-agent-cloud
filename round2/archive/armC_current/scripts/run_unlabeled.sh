#!/usr/bin/env bash
# =============================================================================
# Run the full agent pipeline on unlabelled (non-GT) cases for pseudo-label
# generation.
#
# - Skips GT cases, mock cases, and official cases (agent.skip_gt=true)
# - Enables decision_override for ALL tasks so form_fill_warnings record
#   LLM vs predictor disagreements
# - Outputs to output.unlabeled_20260826/
# - Resume: cases with existing prediction.json or .failed are skipped
#
# Usage:
#   nohup bash scripts/run_unlabeled.sh > logs/unlabeled_20260826.log 2>&1 &
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p logs

echo "=========================================="
echo "Unlabeled case run started: $(date)"
echo "Output dir: output.unlabeled_20260826"
echo "=========================================="

python -m chimera_agent_baseline.run \
    +experiment=qwen_local \
    agent.skip_gt=true \
    paths.output_dir=output.unlabeled_20260826 \
    agent.predictor.decision_override_tasks=[1,2,3]

echo "=========================================="
echo "Unlabeled case run finished: $(date)"
echo "=========================================="
