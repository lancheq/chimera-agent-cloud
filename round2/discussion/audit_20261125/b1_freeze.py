"""B1 版本冻结：SHA256 全清单。只读源码与配置，不触碰患者数据。"""
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join("discussion", "audit_20261125")


def run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def sha(p):
    if not os.path.exists(p):
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


FILES = [
    "src/chimera_agent_baseline/agent/self_refine.py",
    "src/chimera_agent_baseline/agent/graph.py",
    "src/chimera_agent_baseline/agent/form_fill.py",
    "src/chimera_agent_baseline/agent/reflection_contract.py",
    "src/chimera_agent_baseline/agent/prompts.py",
    "src/chimera_agent_baseline/agent/predictor_node.py",
    "src/chimera_agent_baseline/run.py",
    "inference.py",
    "configs/config.yaml",
]

manifest = {
    "frozen_at": run("date", "-Iseconds"),
    "host": run("hostname"),
    "git": {
        "head": run("git", "rev-parse", "HEAD"),
        "head_subject": run("git", "log", "-1", "--format=%s"),
        "head_date": run("git", "log", "-1", "--format=%ad", "--date=iso"),
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "status_count": len([x for x in run("git", "status", "--porcelain").splitlines() if x.strip()]),
        "status": run("git", "status", "--porcelain").splitlines(),
    },
    "files": {},
    "runtime": {
        "python": run(sys.executable, "-c", "import sys;print(sys.version.split()[0])"),
        "self_refine_max_iter": run(
            sys.executable, "-c",
            "import yaml;print(yaml.safe_load(open('configs/config.yaml'))['agent']['form_fill'].get('self_refine_max_iter'))"),
        "self_refine_enabled": run(
            sys.executable, "-c",
            "import yaml;print(yaml.safe_load(open('configs/config.yaml'))['agent']['form_fill'].get('self_refine'))"),
    },
}

for rel in FILES:
    manifest["files"][rel] = {"sha256": sha(rel), "bytes": os.path.getsize(rel) if os.path.exists(rel) else None}

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "B1_freeze_manifest.json"), "w") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

with open(os.path.join(OUT, "B1_freeze_diff.patch"), "w") as f:
    f.write(run("git", "diff", "--", *FILES))

# Prompt 全文单独留档：它们是实验变量，不在 diff 里可追溯
import re
text = open("src/chimera_agent_baseline/agent/self_refine.py").read()
with open(os.path.join(OUT, "B1_prompts.txt"), "w") as f:
    for name in ("_CRITIQUE_SYSTEM", "_REFINE_SYSTEM"):
        m = re.search(name + r"\s*=\s*(\(.*?\)|\"\"\".*?\"\"\"|'''.*?'''|\".*?\")\s*\n\n", text, re.S)
        f.write(f"===== {name} =====\n")
        f.write(m.group(1) if m else "NOT FOUND\n")
        f.write("\n\n")

print(json.dumps({k: manifest[k] for k in ("frozen_at", "git", "runtime")}, ensure_ascii=False, indent=2))
print("\nfiles:")
for k, v in manifest["files"].items():
    print(f"  {v['sha256'][:16] if v['sha256'] else 'MISSING':18} {v['bytes']!s:>8}  {k}")
