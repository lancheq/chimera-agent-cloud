"""C2a：真实 35B 单例 T3 验证，模型调用边界硬计数（上限 12）。

只做三件事：真模型、真 run_agent 路径、全程留痕。不修改任何实验变量，
除 max_model_turns 为预算保护而下调（记录在 run_config）。
"""
import asyncio
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path.cwd()
HOST = Path(os.environ.get("HOST_REPO", "/home/hedongze/CHIMERA-agent"))
sys.path.insert(0, str(ROOT / "src"))

CASE = os.environ.get("C2_CASE", "T3-001")
HARD_CAP = int(os.environ.get("C2_CAP", "12"))
LEDGER = ROOT / "output" / "c2_budget_ledger.json"
MAX_TURNS_DEFAULT = 3


def ledger_load() -> int:
    try:
        return int(json.loads(LEDGER.read_text())["used"])
    except Exception:
        return 0


def ledger_save(used: int) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps({"used": used, "limit": HARD_CAP,
                                  "updated": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                 ensure_ascii=False, indent=2))
MAX_TURNS = int(os.environ.get("C2_MAX_TURNS", str(MAX_TURNS_DEFAULT)))
RUN_ID = time.strftime("c2a_%Y%m%d_%H%M%S")

os.environ["CHIMERA_STRUCTURED_REFINE"] = "1"


class BudgetExhausted(RuntimeError):
    pass


from chimera_agent_baseline.reflection_pilot import CallMeter, make_approval  # noqa: E402


class CountingModel:
    """包在真实模型外面：计数、落盘、超限即抛。不改变响应内容。"""

    def __init__(self, inner, sink: Path, meter):
        self.inner = inner
        self.sink = sink
        self.meter = meter
        self.n = 0

    def _tag(self, messages):
        text = "\n".join(str(getattr(m, "content", m)) for m in messages)
        if "reviewing a proposed correction" in text:
            return "medical_review"
        if "clinical reasoning auditor" in text or "Audit the judgment" in text:
            return "critique"
        if "clinical reasoning corrector" in text or "patch set" in text:
            return "structured_refine"
        if "form for" in text or "fill out the form" in text:
            return "form_fill"
        return "other"

    def _record(self, kind, messages, resp):
        self.n += 1
        try:
            self.meter.consume(kind)      # 额度由准入对象持有，不可重置
        except RuntimeFatal as exc:
            raise BudgetExhausted(str(exc)) from exc
        content = getattr(resp, "content", str(resp))
        row = {"seq": self.n, "kind": kind,
               "prompt_tail": "\n".join(str(getattr(m, "content", m)) for m in messages)[-3000:],
               "response": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)}
        with open(self.sink, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def invoke(self, messages, *a, **kw):
        kind = self._tag(messages)
        resp = self.inner.invoke(messages, *a, **kw)
        self._record(kind, messages, resp)
        return resp

    async def ainvoke(self, messages, *a, **kw):
        kind = self._tag(messages)
        resp = await self.inner.ainvoke(messages, *a, **kw)
        self._record(kind, messages, resp)
        return resp

    def bind_tools(self, tools, **kw):
        self.inner = self.inner.bind_tools(tools, **kw)
        return self

    def with_structured_output(self, *a, **kw):
        return self.inner.with_structured_output(*a, **kw)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _exact_counter(payload) -> dict:
    """wire() 要求的回执：input_tokens 为非负整数，fits 为真，且不得改动 payload。

    这里是保守估计（按字符数上界折算），只用于 token 预留；调用次数上限由
    runtime.max_attempts 在 HTTP 边界强制执行，与 token 估算无关。
    """
    import math
    body = payload.get("messages") or []
    chars = sum(len(str(m.get("content", ""))) for m in body if isinstance(m, dict))
    est = max(1, math.ceil(chars / 3.0))
    return {"input_tokens": est, "fits": True, "estimator": "chars_div_3_upper_bound"}


def main() -> int:
    from omegaconf import OmegaConf

    global PREUSED
    PREUSED = ledger_load()          # 跨失败/重启累计，不再硬编码
    cfg = OmegaConf.load(HOST / "configs" / "config.yaml")
    work = ROOT / "output" / RUN_ID
    audit = work / "audit"
    out = work / "out"
    calls = work / "model_calls.jsonl"
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    data = work / "data"
    dst = data / "task3" / "agent_input" / CASE
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(HOST / "data" / "task3" / "agent_input" / CASE, dst)

    cfg.paths.data_root = str(data)
    cfg.paths.output_dir = str(out)
    cfg.paths.resource_dir = str(HOST / "resources")
    cfg.paths.model_dir = str(HOST / "model")
    cfg.paths.embedding_model_dir = str(HOST / "resources" / "embedding_model")
    cfg.agent.workflow = "react"     # C2a 唯一实验变量变更：Direct ReAct
    cfg.agent.max_model_turns = MAX_TURNS
    # 计量完整性前提：SDK 层零重试，一次 invoke == 一次 HTTP POST == 一次额度
    cfg.model.max_retries = 0

    frozen = {
        "run_id": RUN_ID, "case": CASE, "task": 3,
        "structured_refine": True, "reflection_rounds": 1,
        "medical_reviewer": True, "calibration": "frozen_v1",
        "predictor": "off", "max_model_turns": MAX_TURNS, "max_retries": 0, "hard_cap": HARD_CAP, "preused": PREUSED, "remaining": HARD_CAP - PREUSED,
        "workflow": "react",
        "model": {"provider": str(cfg.model.provider), "id": str(cfg.model.model_id),
                  "base_url": str(cfg.model.base_url)},
        "git": os.popen("git rev-parse HEAD").read().strip(),
    }
    (work / "run_config.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=2))

    import chimera_agent_baseline.run as run_mod
    from chimera_agent_baseline.rag import start_embedding_service
    from chimera_agent_baseline.runtime_audit import PrivateRuntime, RuntimeFatal

    meter = CallMeter(HARD_CAP, used=PREUSED)
    approval = make_approval(cfg, experiment="C2a", case_id=CASE, meter=meter)
    approval.verify_case(CASE, {CASE})

    rt = PrivateRuntime(audit, max_attempts=meter.remaining, max_reserved_tokens=10 ** 9,
                        token_counter=_exact_counter,
                        identity={"profile": "c2a", "purpose": "real_validation",
                                  "effective_config": frozen, "sources": {}, "loaded_flags": {}},
                        identity_check=lambda: None)
    # 计量层拒绝时落盘实际 payload，便于定位协议违例（不改变校验行为）
    import chimera_agent_baseline.models.request_meter as _rm
    _orig_body = _rm.effective_chat_body

    def _body_with_dump(payload, parse=False):
        try:
            return _orig_body(payload, parse=parse)
        except BaseException:      # RuntimeFatal 继承 BaseException，必须一并捕获
            try:
                (work / "rejected_payload.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            except Exception:  # noqa: BLE001
                (work / "rejected_payload.txt").write_text(
                    repr(payload)[:20000], errors="ignore")
            try:
                msgs = (payload or {}).get("messages") or []
                diag = []
                for i, m in enumerate(msgs):
                    c = m.get("content") if isinstance(m, dict) else None
                    diag.append({
                        "i": i,
                        "role": m.get("role") if isinstance(m, dict) else type(m).__name__,
                        "keys": sorted(m) if isinstance(m, dict) else None,
                        "content_type": type(c).__name__,
                        "content_repr": repr(c)[:400],
                        "tool_call_ids": [x.get("id") for x in (m.get("tool_calls") or [])]
                        if isinstance(m, dict) else None,
                        "tool_call_id": m.get("tool_call_id") if isinstance(m, dict) else None,
                    })
                (work / "rejected_messages.json").write_text(
                    json.dumps(diag, ensure_ascii=False, indent=2, default=str))
            except Exception:  # noqa: BLE001
                pass
            raise

    _rm.effective_chat_body = _body_with_dump

    _holder = {}
    # run.py 的 main() 会起 embedding 服务，直接调 run_agent 需自行补齐，否则
    # GuidelinesSearch 拿不到 socket，工具报错后消息链会失配。
    embed_svc = start_embedding_service(cfg.paths.embedding_model_dir)
    status = "ok"
    try:
        with rt.activate():
            rt.reflection_pilot = approval        # 准入在 runtime 激活后挂载，load_model 时才校验
            real = run_mod.load_model(cfg)
            model = CountingModel(real, calls, meter)
            _holder["model"] = model
            run_mod.load_model = lambda cfg_: model
            asyncio.run(run_mod.run_agent(cfg))
    except BudgetExhausted as exc:
        status = f"budget_exhausted: {exc}"
    except BaseException as exc:      # noqa: BLE001
        status = f"{type(exc).__name__}: {str(exc)[:200]}"
        traceback.print_exc(limit=2)

    ledger_save(meter.used)          # 无论成功失败都回写，保证累计

    if embed_svc is not None:
        try:
            embed_svc.stop()
        except Exception:  # noqa: BLE001
            pass

    summary = {"run_id": RUN_ID, "case": CASE, "status": status,
               "model_calls": _holder["model"].n if "model" in _holder else 0,
               "cap": HARD_CAP, "meter_used": meter.used, "meter_remaining": meter.remaining,
               "authority": type(getattr(rt, "reflection_authority", None)).__name__,
               "work_dir": str(work)}
    (work / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
