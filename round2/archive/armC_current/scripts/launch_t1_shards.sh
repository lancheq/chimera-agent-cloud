#!/bin/bash
# T1 W9 四路并行分片跑：共用同一 llama-server（--parallel 4），每进程独立 embedding socket
set -uo pipefail
cd "$(dirname "$0")/.." 2>/dev/null || cd /home/hedongze/CHIMERA-agent

PY="$HOME/miniconda3/envs/chimera/bin/python"
OUT="test/output.t1_w9_20261030"
N=4

mapfile -t ALL < <(ls -d data/task1/agent_input/*/ | xargs -n1 basename | sort)
echo "总计 ${#ALL[@]} 例，分 $N 片"

export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHON="$PY"
export CHIMERA_WEIGHT_TABLE=1
export CHIMERA_T1_TOOL_DENY=get_pathology_report,get_family_history
export CHIMERA_REVEAL_DENY=section_s3-path,section_s3-fh
export CHIMERA_WEIGHT_TABLE_KEEP=bx

for i in $(seq 0 $((N-1))); do
  LIST=""
  for ((j=i; j<${#ALL[@]}; j+=N)); do
    LIST="${LIST:+$LIST,}${ALL[$j]}"
  done
  CNT=$(echo "$LIST" | tr ',' '\n' | wc -l)
  CHIMERA_EMBED_SOCKET="/tmp/chimera_embed_shard$i.sock" \
    nohup "$PY" -m chimera_agent_baseline.run \
      +experiment=qwen_local agent.tasks=[1] "agent.pids=[$LIST]" \
      paths.output_dir="$OUT" > "/tmp/t1_w9_shard$i.log" 2>&1 &
  echo "shard$i PID=$! 例数=$CNT"
  sleep 3
done

echo "全部已启动"
