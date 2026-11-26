"""ReflectionPilot 受限准入的最小正负对照。零真实调用、零网络。"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chimera_agent_baseline.reflection_pilot import (  # noqa: E402
    CallMeter, ReflectionPilotApproval, make_approval,
)
from chimera_agent_baseline.runtime_audit import RuntimeFatal  # noqa: E402


def cfg(base_url="http://127.0.0.1:8765/v1", model_id="Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"):
    return SimpleNamespace(model=SimpleNamespace(
        provider="openai", model_id=model_id, base_url=base_url))


def runtime():
    return SimpleNamespace()


# 1 未授权：load_model 保持原有拒绝
def test_no_approval_still_rejected():
    from chimera_agent_baseline.models import load_model
    from chimera_agent_baseline.runtime_audit import PrivateRuntime
    import tempfile

    rt = PrivateRuntime(Path(tempfile.mkdtemp()) / "a", max_attempts=2,
                        max_reserved_tokens=10 ** 6, token_counter=lambda p: 0,
                        identity={"purpose": "t"}, identity_check=lambda: None)
    with rt.activate():
        with pytest.raises(RuntimeFatal, match="Synthetic entry approval"):
            load_model(cfg())


# 2 病例不在白名单
def test_case_not_whitelisted_rejected():
    a = make_approval(cfg(), experiment="C2a", case_id="T3-001",
                      meter=CallMeter(12, used=1))
    with pytest.raises(RuntimeFatal, match="case mismatch"):
        a.verify_case("T3-002", {"T3-002"})
    with pytest.raises(RuntimeFatal, match="unapproved cases present"):
        a.verify_case("T3-001", {"T3-001", "T3-009"})


# 3 端点非 loopback
def test_non_loopback_endpoint_rejected():
    a = make_approval(cfg(), experiment="C2a", case_id="T3-001", meter=CallMeter(12, 1))
    remote = cfg(base_url="http://10.0.0.5:8765/v1")
    with pytest.raises(RuntimeFatal, match="endpoint mismatch"):
        a.verify(remote, runtime())
    # 端点相同但主机改为外部地址（构造同 URL 的情形由 mismatch 先拦）
    a2 = ReflectionPilotApproval(experiment="C2a", case_id="T3-001",
                                 base_url="http://10.0.0.5:8765/v1",
                                 model_id=cfg().model.model_id, config_digest="x",
                                 meter=CallMeter(12, 1))
    with pytest.raises(RuntimeFatal, match="non-loopback"):
        a2.verify(remote, SimpleNamespace())


# 4 缺计量器 / 预算耗尽
def test_meter_required_and_exhausted():
    a = make_approval(cfg(), experiment="C2a", case_id="T3-001", meter=CallMeter(12, 1))
    broken = ReflectionPilotApproval(**{**a.__dict__, "meter": None})
    with pytest.raises(RuntimeFatal, match="meter required"):
        broken.verify(cfg(), runtime())

    spent = make_approval(cfg(), experiment="C2a", case_id="T3-001",
                          meter=CallMeter(12, used=12))
    with pytest.raises(RuntimeFatal, match="budget exhausted"):
        spent.verify(cfg(), runtime())


# 5 配置摘要不符（改任一冻结项即失效）
def test_config_digest_binding():
    a = make_approval(cfg(), experiment="C2a", case_id="T3-001", meter=CallMeter(12, 1))
    other_model = cfg(model_id="Qwen3.8-27B")
    with pytest.raises(RuntimeFatal):
        a.verify(other_model, runtime())


# 6 合法授权放行
def test_valid_approval_passes():
    m = CallMeter(12, used=1)
    a = make_approval(cfg(), experiment="C2a", case_id="T3-001", meter=m)
    a.verify(cfg(), runtime())
    a.verify_case("T3-001", {"T3-001"})
    assert m.remaining == 11


# 7 计量器：扣减与耗尽，不可重置
def test_meter_never_resets():
    m = CallMeter(12, used=11)
    assert m.remaining == 1
    assert m.consume("critique") == 12
    assert m.remaining == 0
    with pytest.raises(RuntimeFatal, match="budget exhausted"):
        m.consume("refine")
    assert m.remaining == 0


# 8 原有三类入口行为不变：host_smoke / case_pilot 分支仍在 raise 之前
def test_existing_branches_order_preserved():
    src = (Path(__file__).resolve().parents[1]
           / "src/chimera_agent_baseline/models/__init__.py").read_text()
    i_pilot = src.index("reflection_pilot")
    i_raise = src.index("cannot load a real model")
    i_host = src.index("host_smoke")
    i_case = src.index("case_pilot")
    assert i_pilot > i_host and i_pilot > i_case      # 不抢占既有分支
    assert i_pilot < i_raise                          # 但在默认拒绝之前
