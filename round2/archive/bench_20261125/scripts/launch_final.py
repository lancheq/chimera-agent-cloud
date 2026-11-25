#!/bin/bash
cd /home/hedongze/CHIMERA-armC || exit 1
echo $$ > output/bench/finalizer.pid
exec /home/hedongze/venvs/chimera_access/bin/python bench/finalize.py
