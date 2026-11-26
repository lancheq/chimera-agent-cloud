"""在 HTTP transport 边界打桩的零生成宿主回归。

真实之处：Agent 宿主、LangChain 消息对象、OpenAI SDK 序列化、统一归一化、
request_meter 协议校验与计量、Reflection authority、Producer/Consumer、Calibration。
伪造之处：只有 httpx 的最终响应（MockTransport），不触碰 35B。

用独立账本，不影响正式 C2a 预算。
"""
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

import httpx

ROOT = Path.cwd()
HOST = Path(os.environ.get("HOST_REPO", "/home/hedongze/CHIMERA-agent"))
sys.path.insert(0, str(ROOT / "src"))

CASE = os.environ.get("C2_CASE", "T3-001")
RUN_ID = time.strftime("transport_%Y%m%d_%H%M%S")
os.environ["CHIMERA_STRUCTURED_REFINE"] = "1"

T3_JUDGMENT = {
    "event": 1,
    "months_to_recurrence": 18.0,
    "free_text": "High-risk features present; recurrence projected at 18 months.",
    "repeat_test": None,
}
NEW_FREE_TEXT = "Reassessed against the record; recurrence window revised."


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


class Transport:
    """决定「服务器返回什么」，并记录每次真实序列化后的请求。"""

    def __init__(self, sink: Path):
        self.sink = sink
        self.n = 0
        self.log = []

    def _kind(self, body: dict) -> str:
        msgs = body.get("messages") or []
        text = "\n".join(str(m.get("content") or "") for m in msgs)
        if msgs and msgs[-1].get("role") == "assistant" and str(msgs[-1].get("content")) == "{":
            return "form_fill_prefill"
        if "reviewing a proposed correction" in text:
            return "medical"
        if "clinical reasoning auditor" in text or "Audit the judgment" in text:
            return "critique"
        if "clinical reasoning corrector" in text or "patch set" in text:
            return "refine"
        has_tool = any(m.get("role") == "tool" for m in msgs)
        if not has_tool and body.get("tools"):
            return "agent_tool_calls"
        return "agent_final"

    def _patch(self, text: str):
        def ex(start, end):
            i = text.find(start)
            if i < 0:
                return None
            i += len(start)
            j = text.find(end, i)
            return text[i:j] if j > 0 else None

        jtxt = ex("Current judgment (copy `before` from here):\n",
                  "\n\nCase record (quote evidence from here):")
        rec = ex('Case record (quote evidence from here):\n"""\n', '\n"""')
        try:
            cur = json.loads(jtxt)
        except Exception:
            return []
        quote = ""
        for line in (rec or "").splitlines():
            if len(line.strip()) >= 20:
                quote = line.strip()[:60]
                break
        if not quote:
            return []
        return [{"path": "/free_text", "before": cur.get("free_text", ""),
                 "after": NEW_FREE_TEXT, "error_type": "incomplete_rationale",
                 "evidence_quote": quote, "reason": "transport probe"}]

    def respond(self, body: dict) -> dict:
        self.n += 1
        kind = self._kind(body)
        if kind == "agent_tool_calls":
            content, tool_calls, finish = "", [
                {"id": "call_pathology", "type": "function",
                 "function": {"name": "get_surgical_pathology_report",
                              "arguments": '{"case_id": "T3-001"}'}},
                {"id": "call_notes", "type": "function",
                 "function": {"name": "get_previous_notes",
                              "arguments": '{"case_id": "T3-001"}'}},
            ], "tool_calls"
        elif kind == "form_fill_prefill":
            content = json.dumps(T3_JUDGMENT)[1:]     # form_fill 会补回前导 "{"
            tool_calls, finish = None, "stop"
        elif kind == "critique":
            content = json.dumps({"issues": ["rationale may overstate certainty"],
                                  "severity": "mild"})
            tool_calls, finish = None, "stop"
        elif kind == "refine":
            content = json.dumps({"patches": self._patch(
                "\n".join(str(m.get("content") or "") for m in body.get("messages") or []))})
            tool_calls, finish = None, "stop"
        elif kind == "medical":
            content = json.dumps({"verdict": "accept", "blocking": [], "notes": "transport probe"})
            tool_calls, finish = None, "stop"
        else:
            content = json.dumps(T3_JUDGMENT)
            tool_calls, finish = None, "stop"

        message = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        self.log.append({"seq": self.n, "kind": kind,
                         "msg_count": len(body.get("messages") or []),
                         "body_digest": _digest(body),
                         "last_roles": [m.get("role") for m in (body.get("messages") or [])][-4:],
                         "response_kind": finish})
        (self.sink / "requests.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in self.log))
        return {"id": f"mock-{self.n}", "object": "chat.completion", "created": int(time.time()),
                "model": body.get("model", "mock"),
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}


class Counting:
    """包在计量后的模型外面，仅用于按 kind 统计（计量由 request_meter 负责）。"""

    def __init__(self, inner):
        self.inner = inner
        self.n = 0

    def invoke(self, *a, **kw):
        self.n += 1
        return self.inner.invoke(*a, **kw)

    async def ainvoke(self, *a, **kw):
        self.n += 1
        return await self.inner.ainvoke(*a, **kw)

    def bind_tools(self, tools, **kw):
        self.inner = self.inner.bind_tools(tools, **kw)
        return self

    def with_structured_output(self, *a, **kw):
        return self.inner.with_structured_output(*a, **kw)

    def __getattr__(self, k):
        return getattr(self.inner, k)


def _exact_counter(payload) -> dict:
    chars = sum(len(str(m.get("content", ""))) for m in payload.get("messages") or []
                if isinstance(m, dict))
    return {"input_tokens": max(1, chars // 3), "fits": True}


def main() -> int:
    from omegaconf import OmegaConf

    work = ROOT / "output" / RUN_ID
    work.mkdir(parents=True, exist_ok=True)

    cfg = OmegaConf.load(HOST / "configs" / "config.yaml")
    dst = work / "data" / "task3" / "agent_input" / CASE
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(HOST / "data" / "task3" / "agent_input" / CASE, dst)
    cfg.paths.data_root = str(work / "data")
    cfg.paths.output_dir = str(work / "out")
    cfg.paths.resource_dir = str(HOST / "resources")
    cfg.paths.model_dir = str(HOST / "model")
    cfg.paths.embedding_model_dir = str(HOST / "resources" / "embedding_model")
    cfg.agent.workflow = "react"
    cfg.agent.max_model_turns = 3
    cfg.model.max_retries = 0

    from chimera_agent_baseline.run_guard import assert_environment
    manifest = assert_environment(ROOT / "src")
    (work / "module_origin.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    transport = Transport(work)
    mock_client = httpx.Client(transport=httpx.MockTransport(
        lambda req: _handle(req, transport)), timeout=30, follow_redirects=False)

    from chimera_agent_baseline.models import _load_openai
    from chimera_agent_baseline.models.request_meter import meter_openai
    from chimera_agent_baseline.rag import start_embedding_service
    import chimera_agent_baseline.run as run_mod
    from chimera_agent_baseline.runtime_audit import PrivateRuntime

    base = _load_openai(cfg, loopback_only=True)
    base.root_client._client = mock_client          # 只换传输层，其余全是真件
    base.root_async_client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: _handle(req, transport)),
        timeout=30, follow_redirects=False)
    metered = meter_openai(base)
    model = Counting(metered)

    rt = PrivateRuntime(work / "audit", max_attempts=40, max_reserved_tokens=10 ** 9,
                        token_counter=_exact_counter,
                        identity={"profile": "transport_probe", "purpose": "zero_gen"},
                        identity_check=lambda: None)
    rt.reflection_pilot = object()                  # 占位：本轮绕过 load_model，不经准入
    embed = start_embedding_service(cfg.paths.embedding_model_dir)
    run_mod.load_model = lambda cfg_: model

    status = "ok"
    from chimera_agent_baseline.runtime_audit import RuntimeFatal
    try:
        with rt.activate():
            asyncio.run(run_mod.run_agent(cfg))
    except RuntimeFatal as exc:                     # 协议/计量失败：记录并退出
        status = f"RuntimeFatal: {str(exc)[:200]}"
        traceback.print_exc(limit=2)
    except Exception as exc:                        # 其他异常照常暴露
        status = f"{type(exc).__name__}: {str(exc)[:200]}"
        traceback.print_exc(limit=2)
    finally:
        if embed is not None:
            try:
                embed.stop()
            except Exception:                       # noqa: BLE001
                pass

    out = work / "out"
    pred = out / "task3" / CASE / "prediction.json"
    summary = {
        "run_id": RUN_ID, "case": CASE, "status": status,
        "sdk_calls": model.n, "transport_responses": transport.n,
        "kinds": [r["kind"] for r in transport.log],
        "prediction_exists": pred.exists(),
        "prediction_has_new_text": (NEW_FREE_TEXT in pred.read_text()) if pred.exists() else False,
        "authority": type(getattr(rt, "reflection_authority", None)).__name__,
        "meter_attempts": getattr(rt, "attempts", None),
    }
    (work / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _handle(request: httpx.Request, transport: Transport) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(200, json=transport.respond(body))


if __name__ == "__main__":
    raise SystemExit(main())
