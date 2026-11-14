#!/usr/bin/env python3
"""Run on the Mac. Copy only review materials to a NEW staging directory.

No original files are modified. No full project tree or model/data directory
is synced. Inventory data/archive paths separately for a later cohort import.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("/Users/lancheq/Desktop/项目汇总/Chimera-agent"))
    parser.add_argument("--rp", type=Path, default=Path("/Users/lancheq/Desktop/留学申请/RP"))
    parser.add_argument("--output", type=Path, required=True, help="New staging directory")
    args = parser.parse_args()
    if not args.project.is_dir() or not args.rp.is_dir():
        parser.error("Project and RP directories must both exist")
    if args.output.exists():
        parser.error("Output already exists; choose a new directory")
    project_files = [
        "AGENT.md", "AGENTS.md", "docs/EXPERIMENTS.md",
        "docs/OPEN_ISSUES_未解决问题清单.md",
        "docs/CHIMERA-agent_双线状态与待验证_20261026.md",
    ]
    requested = [(args.project / name, Path("project") / name) for name in project_files]
    for directory in ("reports/platform_test_20261104", "reports/platform_result_20261104"):
        root = args.project / directory
        if root.is_dir():
            requested.extend((p, Path("project") / p.relative_to(args.project))
                             for p in root.rglob("*") if p.is_file()
                             and p.suffix.lower() in {".md", ".json", ".csv", ".txt", ".html"})
    requested.extend((p, Path("rp") / p.relative_to(args.rp)) for p in args.rp.rglob("*")
                     if p.is_file() and not p.name.startswith("~$")
                     and p.suffix.lower() in {".docx", ".md", ".html"})
    args.output.mkdir(parents=True)
    manifest = []
    for source, relative in sorted(set(requested)):
        if not source.is_file():
            manifest.append({"source": str(source), "missing": True})
            continue
        if source.stat().st_size > 20 * 1024 * 1024:
            manifest.append({"source": str(source), "skipped": "larger_than_20_MiB"})
            continue
        dest = args.output / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        manifest.append({"source": str(source), "staged": str(relative), "bytes": dest.stat().st_size,
                         "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()})
    (args.output / "material_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Staged {sum('staged' in item for item in manifest)} files in {args.output}")


if __name__ == "__main__":
    main()
