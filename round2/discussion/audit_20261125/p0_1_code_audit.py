"""P0-1 代码版本审计：用 Git blob hash 复核内容一致性，并解析两个 HEAD 之间的实际差异。"""
import json
import os
import subprocess

FILES = [
    "src/chimera_agent_baseline/agent/self_refine.py",
    "src/chimera_agent_baseline/agent/graph.py",
    "src/chimera_agent_baseline/agent/form_fill.py",
    "src/chimera_agent_baseline/agent/reflection_contract.py",
    "src/chimera_agent_baseline/agent/prompts.py",
    "src/chimera_agent_baseline/run.py",
    "inference.py",
    "configs/config.yaml",
]
A = "7301642"
B = "606b6df"


def g(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else f"ERR:{r.stderr.strip()[:120]}"


print("=== 两个 HEAD 的关系 ===")
print("A->B 是否祖先关系:", g("git", "merge-base", "--is-ancestor", A, B) == "")
print("A..B 提交数:", len(g("git", "rev-list", f"{A}..{B}").splitlines()))
print("B..A 提交数:", len(g("git", "rev-list", f"{B}..{A}").splitlines()))
print("A..B 提交:")
for line in g("git", "log", f"{A}..{B}", "--oneline").splitlines()[:12]:
    print("   ", line)

print("\n=== 关键文件在这段区间是否被改过 ===")
for f in FILES:
    a = g("git", "rev-parse", f"{A}:{f}")
    b = g("git", "rev-parse", f"{B}:{f}")
    wt = g("git", "hash-object", f)
    same_ab = "同" if a == b else "**异**"
    same_wt = "一致" if wt == b else "**不一致**"
    print(f"  {f:56} A/B {same_ab}  工作区vsB {same_wt}  blob {b[:10] if not b.startswith('ERR') else b}")

print("\n=== 内容一致性汇总（blob hash，非字节数）===")
diff_files = [f for f in FILES if g("git", "rev-parse", f"{A}:{f}") != g("git", "rev-parse", f"{B}:{f}")]
print("区间内发生变化的文件:", diff_files or "无")

wt_dirty = [f for f in FILES if g("git", "hash-object", f) != g("git", "rev-parse", f"{B}:{f}")]
print("工作区与 B 不一致的文件:", wt_dirty or "无")

print("\n=== 213 项未跟踪的构成（按顶层目录）===")
import collections
c = collections.Counter()
for line in g("git", "status", "--porcelain").splitlines():
    p = line[3:].strip()
    top = p.split("/")[0]
    c[top] += 1
for k, v in c.most_common(15):
    print(f"  {v:>4}  {k}")

with open("discussion/audit_20261125/P0_1_code_version_audit.json", "w") as f:
    json.dump({"A": A, "B": B,
               "commits_A_to_B": len(g("git", "rev-list", f"{A}..{B}").splitlines()),
               "changed_files_between_heads": diff_files,
               "worktree_dirty_vs_B": wt_dirty,
               "untracked_by_top": dict(c)}, f, ensure_ascii=False, indent=2)
