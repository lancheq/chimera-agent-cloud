#!/bin/bash
cd /root/shared-nvme/chimera-agent-baseline
echo ==LOG==
tail -8 /tmp/night_fullchain.log
echo ==PROC==
ps aux | grep -E "night_fullchain|chimera_agent|llama_cpp|trace_monitor" | grep -v grep | wc -l
echo ==HARD==
grep -E "HARD" /tmp/trace_monitor_t1full.log 2>/dev/null | tail -3
echo ==GPU==
nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader
if grep -q "ALL DONE" /tmp/night_fullchain.log; then
  echo STATUS=DONE
elif grep -q "ABORT" /tmp/night_fullchain.log; then
  echo STATUS=ABORT
else
  echo STATUS=RUNNING
fi
