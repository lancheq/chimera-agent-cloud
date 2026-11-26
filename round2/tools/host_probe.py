"""最小宿主探针：真实 run_agent 路径，仅模型调用边界打桩（零真实调用）。

目的：验证 CHIMERA_STRUCTURED_REFINE=1 时，runtime 装配的生产 authority 是否
在真实 form_fill 的 G1R 钩子上被执行，并走完 Producer → Review → Consumer。
不评估医学质量。
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path.cwd()
HOST = Path(os.environ.get("HOST_REPO", "/home/hedongze/CHIMERA-agent"))
sys.path.insert(0, str(ROOT / "src"))

os.environ["CHIMERA_STRUCTURED_REFINE"] = os.environ.get("SR_FLAG", "1")

from langchain_core.messages import AIMessage  # noqa: E402

# T3 的 Dynamic schema 只要求 3 个字段；validate_judgment 做 canonical 全等，
# 多给字段会被判非法（实测：多给 confidence/variable_weights 直接 failed_no_valid_initial）。
T3_JUDGMENT = {
    "event": 1,
    "months_to_recurrence": 18.0,
    "free_text": "High-risk features present; recurrence projected at 18 months.",
    "repeat_test": None,
}
NEW_FREE_TEXT = "Reassessed against the record; recurrence window revised."


class StubModel:
    def __init__(self):
        self.calls = 0
        self.kinds = []

    def _extract(self, text, start, end):
        i = text.find(start)
        if i < 0:
            return None
        i += len(start)
        j = text.find(end, i)
        return text[i:j] if j > 0 else None

    def _build_patch(self, prompt):
        jtxt = self._extract(prompt, "Current judgment (copy `before` from here):\n",
                             "\n\nCase record (quote evidence from here):")
        rec = self._extract(prompt, 'Case record (quote evidence from here):\n"""\n', '\n"""')
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
                 "evidence_quote": quote, "reason": "probe: rewrite rationale"}]

    def _next(self, messages):
        self.calls += 1
        text = "\n".join(str(getattr(m, "content", m)) for m in messages)
        # form_fill 用 assistant prefill：先发一个 "{"，再把响应拼回 "{" + text。
        # 桩必须只回「续写部分」，否则会变成 {{... 而解析失败。
        last = str(getattr(messages[-1], "content", "")).strip() if messages else ""
        if last == "{":
            self.kinds.append("form_fill_prefill")
            return json.dumps(T3_JUDGMENT)[1:]
        if "reviewing a proposed correction" in text:
            self.kinds.append("medical")
            return json.dumps({"verdict": "accept", "blocking": [], "notes": "probe"})
        if "clinical reasoning auditor" in text or "Audit the judgment" in text:
            self.kinds.append("critique")
            return json.dumps({"issues": ["rationale may overstate certainty"],
                               "severity": "mild"})
        if "clinical reasoning corrector" in text or "patch set" in text:
            self.kinds.append("refine")
            return json.dumps({"patches": self._build_patch(text)})
        self.kinds.append("other")
        return json.dumps(T3_JUDGMENT)

    def invoke(self, messages, *a, **kw):
        return AIMessage(content=self._next(messages))

    async def ainvoke(self, messages, *a, **kw):
        return AIMessage(content=self._next(messages))

    def bind_tools(self, tools, **kw):
        return self

    def with_structured_output(self, *a, **kw):
        return self


def main() -> int:
    from omegaconf import OmegaConf

    cfg = OmegaConf.load(HOST / "configs" / "config.yaml")
    tmp = Path(tempfile.mkdtemp(prefix="hostprobe-"))
    dst = tmp / "data" / "task3" / "agent_input" / "T3-001"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(HOST / "data" / "task3" / "agent_input" / "T3-001", dst)
    cfg.paths.data_root = str(tmp / "data")
    cfg.paths.output_dir = str(tmp / "out")
    cfg.paths.resource_dir = str(HOST / "resources")
    (tmp / "out").mkdir(parents=True, exist_ok=True)

    import chimera_agent_baseline.run as run_mod
    from chimera_agent_baseline.runtime_audit import PrivateRuntime

    stub = StubModel()
    run_mod.load_model = lambda cfg_: stub

    rt = PrivateRuntime(tmp / "audit",
                        max_attempts=8,
                        max_reserved_tokens=10 ** 9,
                        token_counter=lambda payload: 0,
                        identity={"profile": "probe", "purpose": "host_probe",
                                  "effective_config": {}, "sources": {}, "loaded_flags": {}},
                        identity_check=lambda: None)
    failed = None
    try:
        with rt.activate():
            asyncio.run(run_mod.run_agent(cfg))
    except BaseException as exc:      # noqa: BLE001
        failed = f"{type(exc).__name__}: {str(exc)[:200]}"
        traceback.print_exc(limit=2)

    print("\n===== 结果 =====")
    print("flag:", os.environ.get("CHIMERA_STRUCTURED_REFINE"))
    print("authority 装配:", type(getattr(rt, "reflection_authority", None)).__name__)
    print("模型调用:", stub.calls, stub.kinds)
    if failed:
        print("异常:", failed)
    out = tmp / "out"
    hits = []
    for p in sorted(tmp.rglob("events.jsonl")):
        for line in p.read_text(errors="ignore").splitlines():
            if "reflection_" in line:
                hits.append(line[:170])
    print("reflection 事件条数:", len(hits))
    for h in hits[:6]:
        print("  ", h)
    print("输出树:")
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print("   ", p.relative_to(tmp), p.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
