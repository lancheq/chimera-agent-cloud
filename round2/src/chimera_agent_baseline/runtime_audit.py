"""Observable component execution and opt-in strict experiment failures.

Diagnostics never enter the submission record. Legacy mode keeps fallbacks;
CHIMERA_STRICT_RUNTIME=1 rejects required components taking fallback paths.
"""
from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ComponentFailure(RuntimeError):
    pass


def strict_runtime() -> bool:
    return os.environ.get("CHIMERA_STRICT_RUNTIME", "0").lower() in {"1", "true", "yes", "on"}


def component_event(component: str, *, configured: bool, status: str,
                    reason: str | None = None, fallback: str | None = None,
                    required: bool = False, **details: Any) -> dict:
    event = {"component": component, "configured": configured, "status": status,
             "reason": reason, "fallback": fallback, "required": required, **details}
    root = os.environ.get("CHIMERA_OUTPUT_DIR")
    if root:
        try:
            path = Path(root) / "runtime_components.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            log.warning("Cannot persist component event for %s", component, exc_info=True)
            if strict_runtime():
                raise
    if required and status in {"failed", "fallback"} and strict_runtime():
        raise ComponentFailure(f"Required component {component}: {reason}; fallback={fallback}")
    return event


class ProcessingAudit:
    def __init__(self, task: int, case_id: str):
        self.task, self.case_id = task, case_id
        self.stages: list[dict] = []

    def record(self, stage: str, before: dict, after: dict, warnings: list[str], *,
               configured: bool = True, status: str = "executed", reason: str | None = None,
               fallback: str | None = None, required: bool = False) -> None:
        self.stages.append({"stage": stage, "configured": configured, "status": status,
                            "reason": reason, "fallback": fallback,
                            "before": deepcopy(before), "after": deepcopy(after),
                            "warnings": list(warnings)})
        root = os.environ.get("CHIMERA_OUTPUT_DIR")
        if root:
            try:
                path = Path(root) / f"task{self.task}" / self.case_id / "processing_audit.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps({"task": self.task, "case_id": self.case_id,
                                           "stages": self.stages}, ensure_ascii=False, indent=2, default=str))
                temp.replace(path)
            except OSError:
                log.warning("Cannot persist processing audit for %s", self.case_id, exc_info=True)
                if strict_runtime():
                    raise
        component_event(stage, configured=configured, status=status, reason=reason, fallback=fallback,
                        required=required, task=self.task, case_id=self.case_id)
