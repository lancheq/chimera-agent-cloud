#!/bin/bash
cd /home/hedongze/CHIMERA-armC || exit 1
echo $$ > output/bench/catchup.pid
exec /home/hedongze/venvs/chimera_access/bin/python bench/a_catchup.py 2
