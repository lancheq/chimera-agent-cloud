#!/usr/bin/env bash
set -euo pipefail
cd /home/hedongze/CHIMERA-agent
out=output/b12_regression_20261122
[[ ${1:-} == --check && $# -eq 1 ]]
umask 077
env_runner="$out/env.sh"
bash "$env_runner" "$out/review_saved.py"
scratch=$(mktemp -d "$out/recheck.XXXXXXXX")
rc_baseline=0
rc_candidate=0
rc_matrix=0
bash "$env_runner" "$out/formal.py" baseline "$scratch/baseline" > "$scratch/formal_baseline.log" 2>&1 || rc_baseline=$?
bash "$env_runner" "$out/formal.py" candidate "$scratch/candidate" > "$scratch/formal_candidate.log" 2>&1 || rc_candidate=$?
bash "$env_runner" "$out/matrix.py" "$scratch/matrix" > "$scratch/matrix.log" 2>&1 || rc_matrix=$?
bash "$env_runner" "$out/compare_formal.py" "$scratch/baseline" "$scratch/candidate" > "$scratch/baseline_delta.json"
bash "$env_runner" "$out/review_saved.py" "$scratch" "$rc_baseline" "$rc_candidate" "$rc_matrix"
printf 'Audit artifacts: %s\n' "$scratch"
