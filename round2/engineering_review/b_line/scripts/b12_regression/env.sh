#!/usr/bin/env bash
set -euo pipefail
cd /home/hedongze/CHIMERA-agent
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="/home/hedongze/CHIMERA-agent:/home/hedongze/CHIMERA-agent/src:/home/hedongze/CHIMERA-agent/tests" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 CUDA_VISIBLE_DEVICES=
exec taskset -c 0 /home/hedongze/venvs/chimera_access/bin/python -B "$@"
