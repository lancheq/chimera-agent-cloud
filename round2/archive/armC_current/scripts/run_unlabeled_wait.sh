#!/usr/bin/env bash
# Wait for ablation to finish, then start unlabeled run.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p logs

echo "[$(date)] Waiting for ablation to finish..."
while ps aux | grep -q '[r]un_ablation'; do
    B=$(find test/output.ablation_selfrefine -name prediction.json 2>/dev/null | wc -l)
    echo "[$(date)] Ablation still running (B:$B/30), waiting 60s..."
    sleep 60
done
echo "[$(date)] Ablation finished, starting unlabeled run."

echo "==========================================" > logs/unlabeled_20260826.log
echo "Unlabeled case run started: $(date)" >> logs/unlabeled_20260826.log
echo "Output dir: output.unlabeled_20260826" >> logs/unlabeled_20260826.log
echo "==========================================" >> logs/unlabeled_20260826.log

python -m chimera_agent_baseline.run \
    +experiment=qwen_local \
    agent.skip_gt=true \
    paths.output_dir=output.unlabeled_20260826 \
    agent.predictor.decision_override_tasks=[1,2,3] \
    >> logs/unlabeled_20260826.log 2>&1

echo "==========================================" >> logs/unlabeled_20260826.log
echo "Unlabeled case run finished: $(date)" >> logs/unlabeled_20260826.log
echo "==========================================" >> logs/unlabeled_20260826.log
echo "[$(date)] Unlabeled run complete. Check logs/unlabeled_20260826.log"
