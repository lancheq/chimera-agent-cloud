#!/usr/bin/env python3
"""步骤 7：GLM-5.2 伪标签蒸馏管线。

对每个无 decision 文件的 case（共 187），拼结构化输入，调 GLM-5.2 按
schema 输出硬标签 + 软理由，写 data/pseudo_labels.json。

合规：开发期使用 MIT 许可开源模型 GLM-5.2（智谱，2026-06）为无标注训练
病例生成伪标签；最终提交系统以开源 Qwen3.6-35B-A3B 全离线运行，不依赖
任何外部 API。伪标签仅进训练，不当评测集预测提交。

用法
----
  # 全量产出（187 case）
  python scripts/gen_pseudo_labels_glm.py

  # 单任务
  python scripts/gen_pseudo_labels_glm.py --task 2

  # 断点续跑（跳过 checkpoint 已完成的 case）
  python scripts/gen_pseudo_labels_glm.py --resume

质量门控
--------
  Task 2（治疗决策由 Gleason+PSA+PIRADS+stage 按 EAU 规则可确定性推导）：
    规则校验 GLM 输出一致性，不一致丢弃。
  Task 1/3（依赖组织学/随访事实，伪标签噪声大）：低权重纳入训练。
  随后跑 train_predictor.py --loocv-compare 做 A/B 门控。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 每个 task 的 ground-truth 决策文件（存在 = 有标注）
DECISION_FILE = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}
# 每个 task 的 clinical-data 文件名（masked EHR）
CLINICAL_FILE = {
    1: "prostate-biopsy-decision-clinical-data.json",
    2: "prostate-treatment-decision-clinical-data.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
}
FEATURES_FILE = "prostate-modality-level-neural-representations.json"

OUT_FILE = DATA / "pseudo_labels.json"
CHECKPOINT_FILE = DATA / "pseudo_labels_glm_checkpoint.json"

# 官方/mock case 排除（与 runner skip_gt 一致）
_EXCLUDE_PREFIX = ("BX_", "T2_", "T3-mock")
_EXCLUDE_SUBSTR = "-mock-"

# ---------------------------------------------------------------------------
# GLM-5.2 客户端
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("LLMAPI_BASE_URL", "https://llmapi.blsc.cn/v1")
MODEL = os.environ.get("GLM_MODEL", "GLM-5.2")

_client = None


def _load_dotenv() -> None:
    """从 .env 加载环境变量（开发期便利）。"""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def get_client():
    global _client
    if _client is not None:
        return _client
    from openai import OpenAI

    api_key = os.environ.get("LLMAPI_KEY")
    if not api_key:
        sys.exit("ERROR: LLMAPI_KEY not set. Export it or add to .env")
    _client = OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=3, timeout=120)
    return _client


# ---------------------------------------------------------------------------
# Schema 提示（约束 GLM 输出 JSON 结构）
# ---------------------------------------------------------------------------

_WEIGHT_VALUES = "not_used | noted | important | decisive"
_T1_VARS = ["psa", "age", "dre", "comorbidity", "bx", "pirads", "psad", "vol", "cspca", "fh"]
_T2_VARS = [
    "psa", "age", "ct", "comorbidity", "pirads", "psad", "cspca",
    "bx_gl_prim", "bx_gl_sec", "bx_gl_tert", "bx_isup", "fh",
]


def _weights_schema(vars_: list[str]) -> str:
    inner = ",\n".join(f'    "{v}": "{_WEIGHT_VALUES}"' for v in vars_)
    return "{\n" + inner + "\n  }"


SCHEMA_HINT = {
    1: (
        '{\n'
        '  "biopsy_decision": "yes" | "no",\n'
        '  "confidence": "clear" | "borderline" | "uncertain",\n'
        f'  "variable_weights": {_weights_schema(_T1_VARS)},\n'
        '  "free_text": "string (>= 40 chars): the 2-4 key factors that drove the decision"\n'
        '}'
    ),
    2: (
        '{\n'
        '  "treatment_recommendation": {\n'
        '    "primary": "active_surveillance" | "continued_surveillance" | "watchful_waiting" | "active_treatment",\n'
        '    "modalities": ["string"],\n'
        '    "detail": "string",\n'
        '    "as_protocol": "string",\n'
        '    "as_trigger": "string"\n'
        '  },\n'
        '  "confidence": "clear" | "borderline" | "uncertain",\n'
        f'  "variable_weights": {_weights_schema(_T2_VARS)},\n'
        '  "free_text": "string (>= 40 chars): the 2-4 key factors that drove the recommendation"\n'
        '}'
    ),
    3: (
        '{\n'
        '  "event": 0 | 1,             // 1 = biochemical recurrence predicted, 0 = censored / no recurrence\n'
        '  "months_to_recurrence": number >= 0,   // months to recurrence (or last follow-up if event=0)\n'
        '  "free_text": "string (>= 40 chars): the evidence and factors driving the prognosis"\n'
        '}'
    ),
}

SYSTEM_PROMPT = (
    "You are a prostate cancer clinical decision agent following EAU 2026 Guidelines. "
    "Based on the provided clinical case data, produce a structured decision judgment. "
    "Output ONLY valid JSON (no markdown fences, no explanation outside JSON) matching this schema:\n\n"
    "{schema}\n\n"
    "Rules:\n"
    "- variable_weights: rate each variable's influence on your decision using one of: "
    "not_used, noted, important, decisive.\n"
    "- free_text: concise clinical reasoning (>= 40 characters).\n"
    "- Base decisions on EAU 2026 risk stratification and management guidelines."
)


# ---------------------------------------------------------------------------
# Prompt 拼装
# ---------------------------------------------------------------------------


def _num(v, default=None):
    try:
        f = float(v)
        return f if f == f else default  # NaN check
    except (TypeError, ValueError):
        return default


def _embedding_shapes(feat: dict) -> dict:
    """只塞数值摘要，不塞原始向量。"""
    shapes = {}
    for origin, vecs in (feat or {}).items():
        if isinstance(vecs, list):
            dims = [len(v) for v in vecs if isinstance(v, list)]
            shapes[origin] = {"n_vectors": len(vecs), "dims": dims[:3]}
        else:
            shapes[origin] = {"n_vectors": 0}
    return shapes


def build_glm_prompt(case_dir: Path, task: int) -> dict:
    """拼 structured-prompt + clinical-data + features 概要。"""
    sp = json.loads((case_dir / "structured-prompt.json").read_text())

    summary: dict = {
        "task": task,
        "case_id": sp.get("case_id") or case_dir.name,
        "structured_prompt": sp,
    }

    clin_file = case_dir / CLINICAL_FILE[task]
    if clin_file.exists():
        cd = json.loads(clin_file.read_text())
        # 只塞文本报告与结构化趋势，不塞原始 embedding
        summary["clinical_data"] = {
            "radiology_report": _trunc(cd.get("radiology_report"), 1500),
            "pathology_report": _trunc(cd.get("pathology_report"), 1500),
            "surgical_pathology_report": _trunc(cd.get("surgical_pathology_report"), 1500),
            "psa_trend": cd.get("psa_trend"),
            "family_history": cd.get("family_history"),
            "laboratory_results": cd.get("laboratory_results"),
            "previous_notes": _trunc(cd.get("previous_notes"), 1000),
        }

    feat_file = case_dir / FEATURES_FILE
    if feat_file.exists():
        feat = json.loads(feat_file.read_text())
        summary["embedding_shapes"] = _embedding_shapes(feat)

    return summary


def _trunc(s, n):
    if s is None:
        return None
    s = str(s)
    return s[:n] + ("..." if len(s) > n else "")


# ---------------------------------------------------------------------------
# GLM 调用 + JSON salvage
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict | None:
    """从 GLM 响应中提取 JSON 对象（salvage）。"""
    if not text:
        return None
    text = text.strip()
    # 1. 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2. 去除 markdown 围栏
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 3. 提取第一个 {...} 块
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def gen_one(case_dir: Path, task: int) -> tuple[dict | None, str]:
    """调 GLM 产出一个 case 的判断。返回 (parsed_json, status)。

    status: "ok" | "salvage_failed" | "api_error"
    """
    client = get_client()
    summary = build_glm_prompt(case_dir, task)
    schema = SCHEMA_HINT[task]
    sys_msg = SYSTEM_PROMPT.format(schema=schema)

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": json.dumps(summary, ensure_ascii=False)},
                ],
                temperature=0.3,
                max_tokens=8192,
            )
            content = resp.choices[0].message.content
            parsed = _extract_json(content or "")
            if parsed is not None:
                return parsed, "ok"
            if attempt == 0:
                # 重试：加强约束
                time.sleep(1)
                continue
            return None, "salvage_failed"
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            return None, f"api_error: {e}"
    return None, "salvage_failed"


# ---------------------------------------------------------------------------
# Schema 校验
# ---------------------------------------------------------------------------

_BIOPSY = {"yes", "no"}
_ACTIONS = {"active_surveillance", "continued_surveillance", "watchful_waiting", "active_treatment"}
_CONF = {"clear", "borderline", "uncertain"}
_WEIGHTS = {"not_used", "noted", "important", "decisive"}


def _valid_weights(w) -> bool:
    if not isinstance(w, dict):
        return False
    for v in w.values():
        if v not in _WEIGHTS:
            return False
    return True


def validate_schema(task: int, obj: dict) -> tuple[bool, str]:
    """校验 GLM 输出是否符合 task schema。返回 (valid, reason)。"""
    if not isinstance(obj, dict):
        return False, "not_a_dict"
    ft = obj.get("free_text")
    if not (isinstance(ft, str) and len(ft) >= 40):
        return False, "free_text_too_short"

    if task == 1:
        bd = obj.get("biopsy_decision")
        if bd not in _BIOPSY:
            return False, f"bad_biopsy_decision:{bd}"
        if obj.get("confidence") not in _CONF:
            return False, f"bad_confidence:{obj.get('confidence')}"
        if not _valid_weights(obj.get("variable_weights")):
            return False, "bad_variable_weights"
        return True, "ok"

    if task == 2:
        tr = obj.get("treatment_recommendation")
        if not isinstance(tr, dict):
            return False, "no_treatment_recommendation"
        if tr.get("primary") not in _ACTIONS:
            return False, f"bad_primary:{tr.get('primary')}"
        if obj.get("confidence") not in _CONF:
            return False, f"bad_confidence:{obj.get('confidence')}"
        if not _valid_weights(obj.get("variable_weights")):
            return False, "bad_variable_weights"
        return True, "ok"

    if task == 3:
        ev = obj.get("event")
        if ev not in (0, 1):
            return False, f"bad_event:{ev}"
        mtr = _num(obj.get("months_to_recurrence"))
        if mtr is None or mtr < 0:
            return False, f"bad_months_to_recurrence:{obj.get('months_to_recurrence')}"
        return True, "ok"

    return False, "unknown_task"


# ---------------------------------------------------------------------------
# Task 2 EAU 规则校验
# ---------------------------------------------------------------------------


def _gleason_to_isup(gl_prim, gl_sec, bx_isup=None) -> int | None:
    """Gleason -> ISUP grade group。优先用 bx_isup。"""
    if bx_isup is not None:
        n = _num(bx_isup)
        if n is not None and 1 <= n <= 5:
            return int(n)
    p = _num(gl_prim)
    s = _num(gl_sec)
    if p is None or s is None:
        return None
    total = p + s
    if total <= 6:
        return 1
    if total == 7:
        return 2 if p <= 3 else 3
    if total == 8:
        return 4
    if total >= 9:
        return 5
    return None


_STAGE_RE = re.compile(r"[Tt](\d)")


def _stage_t(stage_str) -> int | None:
    """从临床分期字符串提取 T 数字 (cT1c -> 1, cT3a -> 3)。"""
    if not stage_str:
        return None
    m = _STAGE_RE.search(str(stage_str))
    if m:
        return int(m.group(1))
    return None


def task2_eau_rule_check(case_dir: Path, decision: str) -> tuple[bool, str]:
    """Task 2 EAU 规则一致性校验。

    可确定性推导的 case：
      - 高危 (ISUP GG≥4 OR PSA>20 OR cT3+) -> 必须 active_treatment
      - 低危 (ISUP GG≤1 AND PSA<10 AND cT1-2) -> 不可 active_treatment
    中危不可确定性推导 -> 一律放行。
    """
    sp = json.loads((case_dir / "structured-prompt.json").read_text())
    psa = _num(sp.get("psa"))
    isup = _gleason_to_isup(sp.get("bx_gl_prim"), sp.get("bx_gl_sec"), sp.get("bx_isup"))
    t_stage = _stage_t(sp.get("ct"))

    # 高危
    high_risk = (
        (isup is not None and isup >= 4)
        or (psa is not None and psa > 20)
        or (t_stage is not None and t_stage >= 3)
    )
    if high_risk:
        if decision != "active_treatment":
            return False, (
                f"high_risk_but_surveillance (isup={isup}, psa={psa}, t={t_stage}, "
                f"decision={decision})"
            )
        return True, "consistent_high_risk"

    # 低危
    low_risk = (
        isup is not None and isup <= 1
        and psa is not None and psa < 10
        and t_stage is not None and t_stage <= 2
    )
    if low_risk:
        if decision == "active_treatment":
            return False, (
                f"low_risk_but_active_treatment (isup={isup}, psa={psa}, t={t_stage})"
            )
        return True, "consistent_low_risk"

    # 中危 / 信息不足 -> 放行
    return True, f"intermediate_or_uncertain (isup={isup}, psa={psa}, t={t_stage})"


# ---------------------------------------------------------------------------
# 输出映射（GLM judgment -> pseudo_labels.json 条目）
# ---------------------------------------------------------------------------


def to_pseudo_entry(case_id: str, task: int, obj: dict) -> dict:
    """把 GLM judgment 映射成 pseudo_labels.json 条目。"""
    if task == 1:
        return {
            "case_id": case_id,
            "decision": obj["biopsy_decision"],
            "confidence": obj.get("confidence"),
            "variable_weights": obj.get("variable_weights"),
            "free_text": obj.get("free_text"),
        }
    if task == 2:
        tr = obj.get("treatment_recommendation") or {}
        return {
            "case_id": case_id,
            "decision": tr.get("primary"),
            "confidence": obj.get("confidence"),
            "variable_weights": obj.get("variable_weights"),
            "free_text": obj.get("free_text"),
        }
    # task 3
    mtr = _num(obj.get("months_to_recurrence"))
    if mtr is None:
        mtr = 0.0
    return {
        "case_id": case_id,
        "decision": {
            "event": int(obj["event"]),
            "months_to_recurrence": float(mtr),
        },
        "free_text": obj.get("free_text"),
    }


# ---------------------------------------------------------------------------
# Case 发现
# ---------------------------------------------------------------------------


def _excluded(name: str) -> bool:
    return name.startswith(_EXCLUDE_PREFIX) or _EXCLUDE_SUBSTR in name


def find_unlabeled_cases(task: int) -> list[Path]:
    """返回无 decision 文件、非官方/mock 的 case 目录列表。"""
    task_dir = DATA / f"task{task}" / "agent_input"
    if not task_dir.exists():
        return []
    out = []
    for c in sorted(task_dir.iterdir()):
        if not c.is_dir():
            continue
        if (c / DECISION_FILE[task]).exists():
            continue
        if _excluded(c.name):
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# Checkpoint（断点续跑）
# ---------------------------------------------------------------------------


def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text())
        except Exception:
            pass
    return {"task1": [], "task2": [], "task3": []}


def save_checkpoint(ckpt: dict) -> None:
    CHECKPOINT_FILE.write_text(json.dumps(ckpt, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_task(task: int, ckpt: dict, resume: bool) -> dict:
    """跑单个 task 的全部无标注 case。返回统计 dict。"""
    cases = find_unlabeled_cases(task)
    done_ids = {e["case_id"] for e in ckpt.get(f"task{task}", [])} if resume else set()
    if resume:
        ckpt[f"task{task}"] = [e for e in ckpt.get(f"task{task}", []) if e["case_id"] in done_ids]

    stats = {
        "total": len(cases),
        "ok": 0,
        "salvage_failed": 0,
        "schema_failed": 0,
        "api_error": 0,
        "t2_discarded": [],
        "skipped_resume": 0,
    }

    for i, case_dir in enumerate(cases):
        cid = case_dir.name
        if cid in done_ids:
            stats["skipped_resume"] += 1
            continue

        obj, status = gen_one(case_dir, task)
        if status != "ok":
            if status.startswith("api_error"):
                stats["api_error"] += 1
            else:
                stats["salvage_failed"] += 1
            print(f"  [task{task}] {i+1}/{len(cases)} {cid}: FAIL ({status})")
            time.sleep(0.3)
            continue

        valid, reason = validate_schema(task, obj)
        if not valid:
            stats["schema_failed"] += 1
            print(f"  [task{task}] {i+1}/{len(cases)} {cid}: SCHEMA_FAIL ({reason})")
            time.sleep(0.3)
            continue

        entry = to_pseudo_entry(cid, task, obj)

        # Task 2 EAU 规则校验
        if task == 2:
            consistent, rule_reason = task2_eau_rule_check(case_dir, entry["decision"])
            if not consistent:
                stats["t2_discarded"].append({"case_id": cid, "decision": entry["decision"], "reason": rule_reason})
                print(f"  [task2] {i+1}/{len(cases)} {cid}: DISCARDED ({rule_reason})")
                time.sleep(0.3)
                continue

        ckpt[f"task{task}"].append(entry)
        stats["ok"] += 1
        # 每 5 个 case 落盘一次 checkpoint
        if stats["ok"] % 5 == 0:
            save_checkpoint(ckpt)
        print(f"  [task{task}] {i+1}/{len(cases)} {cid}: OK ({entry['decision']})")
        time.sleep(0.3)

    save_checkpoint(ckpt)
    return stats


def main() -> None:
    _load_dotenv()
    ap = argparse.ArgumentParser(description="GLM-5.2 伪标签蒸馏管线")
    ap.add_argument("--task", type=int, choices=[1, 2, 3], help="只跑单个 task")
    ap.add_argument("--resume", action="store_true", help="从 checkpoint 续跑")
    ap.add_argument("--dry-run", action="store_true", help="只拼 prompt 不调 API（调试）")
    args = ap.parse_args()

    if args.dry_run:
        for t in ([args.task] if args.task else [1, 2, 3]):
            for c in find_unlabeled_cases(t):
                s = build_glm_prompt(c, t)
                print(f"task{t} {c.name}: keys={list(s.keys())}")
        return

    tasks = [args.task] if args.task else [1, 2, 3]
    ckpt = load_checkpoint() if args.resume else {"task1": [], "task2": [], "task3": []}

    print(f"GLM-5.2 伪标签蒸馏 | model={MODEL} base_url={BASE_URL}")
    all_stats = {}
    for t in tasks:
        print(f"\n{'='*60}")
        print(f"Task {t} — {len(find_unlabeled_cases(t))} unlabeled cases")
        print(f"{'='*60}")
        all_stats[t] = run_task(t, ckpt, args.resume)

    # 写 pseudo_labels.json
    out = {f"task{t}": ckpt.get(f"task{t}", []) for t in (1, 2, 3)}
    # 备份旧文件
    if OUT_FILE.exists():
        bak = OUT_FILE.with_suffix(".prev.json")
        OUT_FILE.rename(bak)
        print(f"\n备份旧 pseudo_labels.json -> {bak.name}")
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    # 统计汇总
    print(f"\n{'='*60}")
    print("汇总")
    print(f"{'='*60}")
    total_ok = 0
    for t in tasks:
        s = all_stats[t]
        total_ok += s["ok"]
        print(f"Task {t}: total={s['total']} ok={s['ok']} "
              f"salvage_failed={s['salvage_failed']} schema_failed={s['schema_failed']} "
              f"api_error={s['api_error']} skipped_resume={s['skipped_resume']}")
        if t == 2 and s["t2_discarded"]:
            print(f"  Task 2 EAU 规则丢弃 ({len(s['t2_discarded'])} case):")
            for d in s["t2_discarded"]:
                print(f"    {d['case_id']}: {d['decision']} — {d['reason']}")
    print(f"\n伪标签总数 = {total_ok}")
    print(f"写入 {OUT_FILE}")
    print(f"Checkpoint: {CHECKPOINT_FILE}")


if __name__ == "__main__":
    main()
