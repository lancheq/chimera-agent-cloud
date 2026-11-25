#!/usr/bin/env bash
# 集B 步骤4-GPU：T1/T2 override vs 纯LLM 对比（串行，GPU 单卡互斥）
set -u
cd /root/shared-nvme/chimera-agent-baseline

run_one() {
  local tag="$1"; local exp="$2"; local task="$3"; local odir="$4"
  echo "[$(date '+%F %T')] START $tag (exp=$exp task=$task odir=$odir)"
  rm -rf "$odir"
  if [ "$exp" = "none" ]; then
    /usr/bin/python3 -m chimera_agent_baseline.run +experiment=qwen_local \
      "agent.tasks=[$task]" agent.limit=30 "paths.output_dir=$odir" \
      > "run.s4_${tag}.log" 2>&1
  else
    /usr/bin/python3 -m chimera_agent_baseline.run "+experiment=$exp" \
      "agent.tasks=[$task]" agent.limit=30 "paths.output_dir=$odir" \
      > "run.s4_${tag}.log" 2>&1
  fi
  local rc=$?
  echo "[$(date '+%F %T')] END $tag rc=$rc"
  echo "$tag $rc" >> run.s4_serial.status
}

: > run.s4_serial.status
run_one t1_ovr  t1_override 1 test/output.s4_t1_ovr
run_one t1_llm  none       1 test/output.s4_t1_llm
run_one t2_ovr  t2_override 2 test/output.s4_t2_ovr
run_one t2_llm  none       2 test/output.s4_t2_llm
echo "[$(date '+%F %T')] ALL_S4_DONE"
