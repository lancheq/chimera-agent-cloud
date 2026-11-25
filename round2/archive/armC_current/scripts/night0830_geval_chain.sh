#!/bin/bash
# 0830 夜链 Phase 4: 双 GEval（T2 → T1，gemma4:e4b judge，用毕卸载）
cd /root/shared-nvme/chimera-agent-baseline || exit 1
export GEVAL_JUDGE_MODEL=gemma4:e4b

echo "== T2 GEval =="
.venv/bin/python .geval_tmp/geval_eval_t2full.py --full > night0830_t2_geval_run.log 2>&1

echo "== T1 GEval =="
.venv/bin/python .geval_tmp/geval_eval_t1full2.py --full > night0830_t1_geval_run.log 2>&1

echo "== unload gemma =="
curl -s -m 10 http://127.0.0.1:11434/api/generate -d '{"model":"gemma4:e4b","keep_alive":0}' > /dev/null 2>&1 || true

echo "GEVAL_CHAIN_DONE"
