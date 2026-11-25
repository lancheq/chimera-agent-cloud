#!/bin/bash
cd /home/hedongze/CHIMERA-agent || exit 1
export PYTHONPATH=/home/hedongze/CHIMERA-agent/src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec /home/hedongze/venvs/chimera_access/bin/python scripts/train_predictor.py --loocv-retest --knn-features
