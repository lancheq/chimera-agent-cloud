"""运行环境 fail-fast 守卫。

在发出第一个真实请求之前确认：模块来源单一、源码未漂移、配置与准入一致。
任一不符立即退出，绝不在混用的解释器里跑实验。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from chimera_agent_baseline.runtime_audit import RuntimeFatal

KEY_MODULES = (
    "chimera_agent_baseline",
    "chimera_agent_baseline.run",
    "chimera_agent_baseline.agent.form_fill",
    "chimera_agent_baseline.agent.reflection_contract",
    "chimera_agent_baseline.agent.reflection_authority",
    "chimera_agent_baseline.agent.reflection_patch",
    "chimera_agent_baseline.agent.self_refine",
    "chimera_agent_baseline.models",
    "chimera_agent_baseline.models.request_meter",
    "chimera_agent_baseline.message_norm",
    "chimera_agent_baseline.rag",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_environment(approved_root: str | Path, *, expect: dict[str, str] | None = None,
                       config=None, expected_config_digest: str | None = None) -> dict:
    """检查并返回实测清单。任何不一致抛 RuntimeFatal，不返回半可信状态。"""
    root = Path(approved_root).resolve()
    if not root.is_dir():
        raise RuntimeFatal(f"guard: approved root missing: {root}")

    import importlib

    files: dict[str, str] = {}
    foreign: list[str] = []
    for name in KEY_MODULES:
        try:
            mod = importlib.import_module(name)
        except Exception as exc:                       # noqa: BLE001
            raise RuntimeFatal(f"guard: cannot import {name}: {type(exc).__name__}") from exc
        path = Path(getattr(mod, "__file__", "") or "").resolve()
        if not str(path).startswith(str(root)):
            foreign.append(f"{name} -> {path}")
        files[name] = str(path)
    if foreign:
        raise RuntimeFatal(
            "guard: modules loaded from outside approved root:\n  " + "\n  ".join(foreign))

    hashes = {name: sha256(Path(p)) for name, p in files.items()}
    if expect:
        bad = [f"{k}: {hashes[k][:12]} != {expect[k][:12]}"
               for k in expect if k in hashes and hashes[k] != expect[k]]
        if bad:
            raise RuntimeFatal("guard: source drift:\n  " + "\n  ".join(bad))

    if config is not None and expected_config_digest is not None:
        from chimera_agent_baseline.runtime_audit import content_digest
        view = {"model": str(config.model.model_id), "base_url": str(config.model.base_url),
                "workflow": str(config.agent.workflow),
                "max_retries": int(getattr(config.model, "max_retries", 0) or 0)}
        if content_digest(view) != expected_config_digest:
            raise RuntimeFatal("guard: config does not match admission digest")

    return {
        "interpreter": sys.executable,
        "approved_root": str(root),
        "sys_path": [p for p in sys.path if p],
        "modules": files,
        "hashes": hashes,
    }
