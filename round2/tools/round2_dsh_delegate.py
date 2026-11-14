#!/usr/bin/env python3
"""Invoke the separately installed DSH headless profile on a prepared workspace."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
BASE = Path.home() / '.local/share'
DSH = BASE / 'codex-dsh/node_modules/.bin/dsh'
BIN = BASE / 'codex-dsh-node/node_modules/.bin'
DSH_HOME = BASE / 'codex-dsh-home'


def main():
    parser = argparse.ArgumentParser(description='Delegate a task to DSH in a separately prepared workspace; task is read from stdin.')
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--mode', choices=('read-only', 'workspace-write'), default='read-only')
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    if not workspace.is_dir() or workspace == REPO or REPO in workspace.parents or workspace in REPO.parents:
        parser.error('Use a separate prepared directory, outside the project and its ancestors')
    task = sys.stdin.read()
    if not task.strip():
        parser.error('Supply the task on stdin')
    if not DSH.exists() or not (BIN / 'node').exists():
        parser.error('DSH or its Node runtime is not installed at the expected location')
    # Reduce inherited credentials, but this is not an OS sandbox: processes run as the user.
    env = {'HOME': str(DSH_HOME / 'runtime-home'), 'DSH_HOME': str(DSH_HOME),
           'PATH': str(BIN) + ':/usr/bin:/bin'}
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy'):
        if key in os.environ:
            env[key] = os.environ[key]
    Path(env['HOME']).mkdir(mode=0o700, parents=True, exist_ok=True)
    result = subprocess.run([str(DSH), '--profile', 'headless', '--permission-mode', args.mode, '-'],
                            input=task, text=True, cwd=workspace, env=env, check=False)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
