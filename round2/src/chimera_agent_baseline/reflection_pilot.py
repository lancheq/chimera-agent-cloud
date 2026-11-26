"""Reflection Pilot 受限准入：仅用于 C2a 单例真实验证。

与 Case Pilot / Host Smoke / Synthetic 三类既有准入平级，互不影响：
- 默认拒绝：未挂载本准入时，load_model 仍抛 "Synthetic entry approval cannot load a real model"
- 无 env 旁路：不读环境变量放宽
- 单次实验绑定：病例、模型、端点、配置摘要、计量器四者同时匹配才放行
- 计量在模型调用边界，额度耗尽立即拒绝；SDK 重试由 meter_openai 结构性地禁掉
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from chimera_agent_baseline.runtime_audit import RuntimeFatal

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


class CallMeter:
    """调用额度。已消耗数量在构造时给定，不可重置。"""

    def __init__(self, limit: int, used: int = 0):
        if limit <= 0 or used < 0 or used > limit:
            raise ValueError("invalid meter range")
        self.limit = int(limit)
        self.used = int(used)
        self._lock = threading.Lock()
        self.consumed_kinds: list[str] = []

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def consume(self, kind: str) -> int:
        with self._lock:
            if self.used >= self.limit:
                raise RuntimeFatal(f"ReflectionPilot budget exhausted at {self.limit} calls")
            self.used += 1
            self.consumed_kinds.append(kind)
            return self.used


@dataclass(frozen=True)
class ReflectionPilotApproval:
    """一次实验、一个病例、一个端点的受限授权。不可跨实验复用。"""

    experiment: str
    case_id: str
    base_url: str
    model_id: str
    config_digest: str
    meter: CallMeter

    def _fail(self, reason: str) -> None:
        raise RuntimeFatal(f"ReflectionPilot approval rejected: {reason}")

    def verify(self, cfg, runtime) -> None:
        if runtime is None:
            self._fail("no active runtime")
        if not self.experiment or len(self.experiment) < 2:
            self._fail("experiment id required")
        if not self.case_id:
            self._fail("case id required")
        if self.meter is None:
            self._fail("meter required")
        if self.meter.remaining <= 0:
            self._fail("budget exhausted")

        url = str(getattr(cfg.model, "base_url", ""))
        if url != self.base_url:
            self._fail(f"endpoint mismatch: {url!r} != {self.base_url!r}")
        host = url.split("//")[-1].split("/")[0].split(":")[0]
        if host not in LOOPBACK_HOSTS:
            self._fail(f"non-loopback endpoint rejected: {host}")
        if str(getattr(cfg.model, "model_id", "")) != self.model_id:
            self._fail("model id mismatch")

        if self.config_digest != _digest(frozen_view(cfg, self)):
            self._fail("frozen config digest mismatch")

    def verify_case(self, case_id: str, cases_in_root: set[str]) -> None:
        """运行前确认：只有白名单病例，且确实是它。"""
        if case_id != self.case_id:
            self._fail(f"case mismatch: {case_id!r} != {self.case_id!r}")
        extra = set(cases_in_root) - {self.case_id}
        if extra:
            self._fail(f"unapproved cases present: {sorted(extra)}")


def frozen_view(cfg, approval: ReflectionPilotApproval) -> dict:
    """参与摘要的字段集合。任何一项变化都会让摘要失配，授权随之失效。"""
    return {
        "experiment": approval.experiment,
        "case_id": approval.case_id,
        "model_provider": str(getattr(cfg.model, "provider", "")),
        "model_id": str(getattr(cfg.model, "model_id", "")),
        "base_url": str(getattr(cfg.model, "base_url", "")),
        "max_retries": int(getattr(cfg.model, "max_retries", 0) or 0),
        "structured_refine": True,
        "reflection_rounds": 1,
        "medical_reviewer": True,
        "calibration": "frozen_v1",
        "predictor": "off",
    }


def _digest(value) -> str:
    from chimera_agent_baseline.runtime_audit import content_digest
    return content_digest(value)


def make_approval(cfg, *, experiment: str, case_id: str, meter: CallMeter) -> ReflectionPilotApproval:
    """按当前 cfg 生成授权，并把摘要写死进批准对象。"""
    base = dict(experiment=experiment, case_id=case_id,
                base_url=str(cfg.model.base_url), model_id=str(cfg.model.model_id),
                config_digest="", meter=meter)
    provisional = ReflectionPilotApproval(**base)
    return ReflectionPilotApproval(**{**base, "config_digest": _digest(frozen_view(cfg, provisional))})
