#!/bin/bash
# Detached launcher: survives SSH disconnect because it runs in its own session.
cd /home/hedongze/CHIMERA-armC || exit 1
echo $$ > output/bench/driver.pid
exec /home/hedongze/venvs/chimera_access/bin/python bench/run_full.py
