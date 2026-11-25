#!/usr/bin/env python3
"""G5: Reflective pseudo-label regeneration (DeepSeek-V4-Flash + critique-refine).

背景：旧 GLM-5.2 伪数据三任务全拒收（A1 LOOCV 无增益）。根因是风格与质量而非数量：
  ① free_text 幻觉数值（教科书式推理引用病例中不存在的衍生数）
  ② 风格偏"面面俱到"（T1 median 31 词 / T3 48 词），与医生标注（median 10-12 词短语）不符
  ③ variable_weights 质量（challenge 评分权重 0.25）

三步管线（每 case × 每 label 任务）：
  病例 → DeepSeek-V4-Flash 初答(decision+weights+free_text)
       → 反思步(critique)：同模型自检（决策-指标自洽 / 幻觉数值 / weights 一致 / 风格）
         输出 {"issues":[...], "verdict":"pass"|"revise"}
       → verdict=revise 则 refine 重答（最多 2 轮）
       → 质量门控（规则校验，硬性）：
           - schema + variable_weights 键完整/值域
           - T1: EAU 方向性；数据集级 yes 率须在 GT 61.5%±10pp 内
           - T2: 低危不得推荐 active_treatment（复用旧过滤思路）
           - T3: event=1 须 1<=months<=120
           - 幻觉：free_text 数值须能在病例原文找到（允许四舍五入/百分比变换）
           - 风格：free_text <=80 词 且含 1-2 个 decisive/important 因素关键词
         门控失败 → 带规则 issues 追加一轮 refine → 仍失败则拒收该条。

Label 任务构成（187 unlabeled cases）：
  - own-task 标签：task1 目录 104 条 T1 + task2 目录 81 条 T2 + task3 目录 2 条 T3
  - T3 标签：全部 187 个 unlabeled case（与 g1_gen_t3_pseudo_all 口径一致）
  共 374 个 label 任务。

Output
------
  data/pseudo_labels_v2.json          {"task1":[...], "task2":[...], "task3":[...]}
    entry: {"case_id","task","decision","confidence","variable_weights","free_text",
            "meta":{"rounds","critique_first","gate_issues"}}
    （decision 为裸字符串/{"event","months_to_recurrence"}，与 pseudo_labels.json 同构，
     train_predictor.py --include-pseudo-v2 直接可读）
  data/g5_reflective_checkpoint.json  每 10 项 checkpoint（--resume 可续）
  data/g5_summary.json                revise 比例 / 幻觉率新旧对比 / 词数分布新旧对比 / yes 率
  .task_locks/A.done                  全量完成后写入

Usage
-----
  python scripts/g5_reflective_pseudo.py --smoke    # 10 个 label 任务
  nohup python scripts/g5_reflective_pseudo.py > logs/g5_reflective.log 2>&1 &
  python scripts/g5_reflective_pseudo.py --resume

Compliance: 开发期蒸馏使用第三方 API 教师；最终提交系统完全离线运行开源 35B 模型。
伪标签仅进训练池，评估池物理隔离只用 GT。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

BASE_URL = os.environ.get("LLMAPI_BASE_URL", "https://llmapi.blsc.cn/v1")
MODEL = os.environ.get("TEACHER_MODEL") or os.environ.get("GLM_MODEL", "DeepSeek-V4-Flash-0731")

DECISION_FILE = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}
CLINICAL_FILE = {
    1: "prostate-biopsy-decision-clinical-data.json",
    2: "prostate-treatment-decision-clinical-data.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
}

OUT_FILE = DATA / "pseudo_labels_v2.json"
CHECKPOINT_FILE = DATA / "g5_reflective_checkpoint.json"
SUMMARY_FILE = DATA / "g5_summary.json"
LOCK_FILE = ROOT / ".task_locks" / "A.done"
LOG_PREFIX = "[g5]"

_EXCLUDE_PREFIX = ("BX_", "T2_", "T3-mock")
_EXCLUDE_SUBSTR = "-mock-"

MAX_WORKERS = 6
CHECKPOINT_INTERVAL = 10
MAX_REFINE_ROUNDS = 2
SMOKE_N = 10

WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9./%+*-]*")
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

ACTIONS = ("active_surveillance", "continued_surveillance", "watchful_waiting", "active_treatment")
WEIGHT_VALUES = ("not_used", "noted", "important", "decisive")

# 与 src/chimera_agent_baseline/output/schema.py 保持一致
TASK1_VARS = ["psa", "age", "dre", "comorbidity", "bx", "pirads", "psad", "vol", "cspca", "fh"]
TASK2_VARS = ["psa", "age", "ct", "comorbidity", "pirads", "psad", "cspca",
              "bx_gl_prim", "bx_gl_sec", "bx_gl_tert", "bx_isup", "fh"]

# decisive/important 因素在 free_text 中的关键词（风格门控用）
VAR_KEYWORDS = {
    "psa": ["psa"], "age": ["age", "year-old", "yr-old", "elderly"], "dre": ["dre", "rectal"],
    "comorbidity": ["comorbid", "asa", "charlson", "frail"],
    "bx": ["biopsy", "gleason", "isup", "grade", "core"],
    "pirads": ["pirads", "pi-rads"], "psad": ["psad", "density"],
    "vol": ["volume", "ml "], "cspca": ["cspca", "probability", "%"],
    "fh": ["family", "father", "brother", "hereditary"],
    "ct": ["ct", "t1", "t2", "t3", "t4", "stage"],
    "bx_gl_prim": ["gleason", "pattern"], "bx_gl_sec": ["gleason", "pattern"],
    "bx_gl_tert": ["tertiary", "pattern 5"], "bx_isup": ["isup", "grade group"],
}

# 医生风格示例（来自 GT reasoning，median 10-12 词）
STYLE_EXAMPLES = (
    'Doctor-style examples (from physician annotations, note the brevity):\n'
    '  - "P5 in a man with rapid increasing PSA"\n'
    '  - "Downgrading from intermediate risk to low risk PCa"\n'
    '  - "ISUP GG 4 with cribriform pattern, unfit for surveillance"\n'
)

# ---------------------------------------------------------------------------
# .env / client
# ---------------------------------------------------------------------------

_client = None


def _load_dotenv() -> None:
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
        sys.exit("ERROR: LLMAPI_KEY not set. Export it or add it to .env")
    _client = OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=3, timeout=180)
    return _client


def chat(system: str, user: str, temperature: float, max_tokens: int = 2048) -> str | None:
    """One chat call; returns content or None after 2 attempts."""
    client = get_client()
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as e:
            if attempt == 0:
                time.sleep(3)
                continue
            print(f"{LOG_PREFIX} api_error: {e}", flush=True)
            return None
    return None


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _trunc(s, n):
    if s is None:
        return None
    s = str(s)
    return s[:n] + ("..." if len(s) > n else "")


def _embedding_shapes(feat: dict) -> dict:
    shapes = {}
    for origin, vecs in (feat or {}).items():
        if isinstance(vecs, list):
            dims = [len(v) for v in vecs if isinstance(v, list)]
            shapes[origin] = {"n_vectors": len(vecs), "dims": dims[:3]}
        else:
            shapes[origin] = {"n_vectors": 0}
    return shapes


def build_case_prompt(case_dir: Path, task: int) -> dict:
    sp = json.loads((case_dir / "structured-prompt.json").read_text())
    summary: dict = {
        "task": task,
        "case_id": sp.get("case_id") or case_dir.name,
        "structured_prompt": sp,
    }
    clin_file = case_dir / CLINICAL_FILE[task]
    if clin_file.exists():
        cd = json.loads(clin_file.read_text())
        summary["clinical_data"] = {
            "radiology_report": _trunc(cd.get("radiology_report"), 1500),
            "pathology_report": _trunc(cd.get("pathology_report"), 1500),
            "surgical_pathology_report": _trunc(cd.get("surgical_pathology_report"), 1500),
            "psa_trend": cd.get("psa_trend"),
            "family_history": cd.get("family_history"),
            "laboratory_results": cd.get("laboratory_results"),
            "previous_notes": _trunc(cd.get("previous_notes"), 1000),
        }
    feat_file = case_dir / "prostate-modality-level-neural-representations.json"
    if feat_file.exists():
        summary["embedding_shapes"] = _embedding_shapes(json.loads(feat_file.read_text()))
    return summary


# ---------------------------------------------------------------------------
# Schemas & prompts
# ---------------------------------------------------------------------------


def answer_schema(task: int) -> str:
    if task == 1:
        return (
            "{\n"
            '  "biopsy_decision": "yes" | "no",\n'
            '  "confidence": "clear" | "borderline" | "uncertain",\n'
            '  "variable_weights": {"psa": ..., "age": ..., "dre": ..., "comorbidity": ..., '
            '"bx": ..., "pirads": ..., "psad": ..., "vol": ..., "cspca": ..., "fh": ...},\n'
            '  "free_text": "string (40-120 chars): ONE short phrase naming the 1-2 decisive factors"\n'
            "}\n"
            "variable_weights values: \"not_used\" | \"noted\" | \"important\" | \"decisive\" "
            "(all 10 keys required)."
        )
    if task == 2:
        return (
            "{\n"
            '  "treatment_decision": "active_surveillance" | "continued_surveillance" | '
            '"watchful_waiting" | "active_treatment",\n'
            '  "confidence": "clear" | "borderline" | "uncertain",\n'
            '  "variable_weights": {"psa": ..., "age": ..., "ct": ..., "comorbidity": ..., '
            '"pirads": ..., "psad": ..., "cspca": ..., "bx_gl_prim": ..., "bx_gl_sec": ..., '
            '"bx_gl_tert": ..., "bx_isup": ..., "fh": ...},\n'
            '  "free_text": "string (40-120 chars): ONE short phrase naming the 1-2 decisive factors"\n'
            "}\n"
            "variable_weights values: \"not_used\" | \"noted\" | \"important\" | \"decisive\" "
            "(all 12 keys required)."
        )
    return (
        "{\n"
        '  "event": 0 | 1,             // 1 = biochemical recurrence predicted, 0 = censored\n'
        '  "months_to_recurrence": number > 0,   // months to recurrence (event=1) or last '
        "follow-up (event=0), range 1-120\n"
        '  "free_text": "string (40-150 chars): ONE short phrase naming the 1-2 decisive '
        'prognostic factors"\n'
        "}"
    )


ANSWER_SYSTEM = (
    "You are a prostate cancer clinical decision agent following EAU 2026 Guidelines. "
    "Based on the provided clinical case data, produce a structured decision. "
    "Output ONLY valid JSON (no markdown fences) matching this schema:\n\n{schema}\n\n"
    "free_text style rules (CRITICAL):\n"
    "- Write like a terse clinician's note: 8-30 words, ONE short phrase. "
    "Hard cap 80 words. NEVER write comprehensive textbook-style reasoning.\n"
    "- Name ONLY the 1-2 DECISIVE factors that drove the call. Omit secondary details.\n"
    "- Every number in free_text MUST appear verbatim in the case data. "
    "Never invent, derive, or compute numbers (no velocities, no percentages not in the case).\n"
    + STYLE_EXAMPLES +
    "Base decisions on EAU 2026 risk stratification and management guidelines."
)

CRITIQUE_SYSTEM = (
    "You are a meticulous clinical QA reviewer. Given a clinical case and a candidate "
    "structured answer, audit the candidate and output ONLY valid JSON:\n"
    '{"issues": ["<specific, actionable issue, or empty>"], "verdict": "pass" | "revise"}\n\n'
    "Check these dimensions:\n"
    "1. Decision-consistency: does the decision follow EAU 2026 from the case metrics "
    "(PSA/PI-RADS/PSAD/ISUP/stage thresholds)? Flag clear contradictions.\n"
    "2. Hallucinated numbers: any number in free_text that does NOT appear in the case data "
    "(derived values like PSA velocity or converted percentages are hallucinations unless "
    "present in the case). Quote each bad number in an issue.\n"
    "3. variable_weights consistency (tasks 1-2): factors marked decisive/important must be "
    "mentioned in free_text and consistent with the decision; all keys must be present with "
    "valid values (not_used/noted/important/decisive).\n"
    "4. Style: free_text must be a terse clinician phrase (ideally 8-30 words, hard cap 80) "
    "naming the 1-2 decisive factors. Textbook-style comprehensive reasoning or long "
    "multi-clause sentences = revise.\n"
    "5. Task 3: event=1 requires months_to_recurrence in 1-120.\n"
    "Only flag REAL problems. If the answer is clinically sound, grounded, and terse, "
    'output verdict "pass" with empty issues.'
)

REFINE_SYSTEM = (
    "You are a prostate cancer clinical decision agent following EAU 2026 Guidelines. "
    "Your previous answer was audited and has issues. Fix ALL issues and output ONLY valid "
    "JSON matching the same schema:\n\n{schema}\n\n"
    "free_text style rules (CRITICAL): terse clinician phrase, 8-30 words (hard cap 80), "
    "1-2 decisive factors only, every number must appear verbatim in the case data.\n"
    + STYLE_EXAMPLES
)


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Hallucination check (数值比对)
# ---------------------------------------------------------------------------


def build_corpus(case_dir: Path, task: int) -> str:
    """病例原文语料：structured-prompt + clinical-data 序列化。"""
    parts = []
    sp_path = case_dir / "structured-prompt.json"
    if sp_path.exists():
        parts.append(sp_path.read_text())
    clin = case_dir / CLINICAL_FILE[task]
    if clin.exists():
        parts.append(clin.read_text())
    return "\n".join(parts)


def _nums(text: str) -> list[float]:
    out = []
    for m in NUM_RE.findall(text or ""):
        try:
            out.append(float(m))
        except ValueError:
            continue
    return out


def hallucinated_numbers(free_text: str, corpus: str, task: int,
                         predicted_months: float | None) -> list[str]:
    """返回 free_text 中无法在病例原文找到的数值（字符串原样）。

    放行规则：
      - 完全匹配 / 常规四舍五入（相对容差 0.5%，绝对容差 0.5 覆盖百分比取整）
      - 百分比变换：n 与 corpus 值 ×100 匹配（0.664 → 66.4/66）
      - 千分变换：n 与 corpus 值 /100 匹配
      - T3：预测的 months 本身、以及 month/year 时间窗上下文中的数（预测性表述）
    """
    bad = []
    corpus_nums = _nums(corpus)

    def matched(n: float) -> bool:
        tol = max(0.005 * abs(n), 0.5)
        for c in corpus_nums:
            if abs(c - n) <= tol:
                return True
        return False

    for m in NUM_RE.finditer(free_text or ""):
        raw = m.group(0)
        try:
            n = float(raw)
        except ValueError:
            continue
        # T3 预测 months 本身放行
        if task == 3 and predicted_months is not None and abs(n - predicted_months) < 0.05:
            continue
        # T3 时间窗预测性表述（"within 12 months"）放行
        if task == 3:
            ctx = free_text[max(0, m.start() - 30):m.end() + 12].lower()
            if re.search(r"month|year", ctx) and n <= 120:
                continue
        if matched(n):
            continue
        if matched(n * 100) or (n != 0 and matched(n / 100)):
            continue
        bad.append(raw)
    return bad


# ---------------------------------------------------------------------------
# 风格 & 规则门控
# ---------------------------------------------------------------------------


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def var_keywords_hit(free_text: str, weights: dict) -> int:
    """free_text 命中的 decisive/important 变量个数。"""
    ft = (free_text or "").lower()
    hits = 0
    for var, w in (weights or {}).items():
        if w not in ("decisive", "important"):
            continue
        for kw in VAR_KEYWORDS.get(var, [var]):
            if kw in ft:
                hits += 1
                break
    return hits


def gate_rules(task: int, obj: dict, corpus: str) -> tuple[bool, list[str]]:
    """硬性规则门控。返回 (ok, issues)。"""
    issues: list[str] = []
    ft = obj.get("free_text") or ""

    # --- schema 层 ---
    if task == 1:
        if obj.get("biopsy_decision") not in ("yes", "no"):
            issues.append(f"bad_biopsy_decision:{obj.get('biopsy_decision')}")
    elif task == 2:
        if obj.get("treatment_decision") not in ACTIONS:
            issues.append(f"bad_treatment_decision:{obj.get('treatment_decision')}")
    else:
        ev = obj.get("event")
        ev = int(ev) if isinstance(ev, str) and ev in ("0", "1") else ev
        if ev not in (0, 1):
            issues.append(f"bad_event:{ev}")
        months = obj.get("months_to_recurrence")
        try:
            months = float(months)
        except (TypeError, ValueError):
            months = None
        if months is None or months <= 0 or months > 120:
            issues.append(f"months_out_of_range:{months}")
        elif ev == 1 and months < 1:
            issues.append(f"event1_months_invalid:{months}")

    # --- variable_weights（T1/T2）---
    weights = obj.get("variable_weights") or {}
    if task in (1, 2):
        expect = TASK1_VARS if task == 1 else TASK2_VARS
        missing = [k for k in expect if k not in weights]
        if missing:
            issues.append(f"weights_missing:{','.join(missing)}")
        bad_vals = [k for k, v in weights.items() if v not in WEIGHT_VALUES]
        if bad_vals:
            issues.append(f"weights_bad_value:{','.join(bad_vals)}")

    # --- 幻觉数值 ---
    months_val = None
    if task == 3:
        try:
            months_val = float(obj.get("months_to_recurrence"))
        except (TypeError, ValueError):
            months_val = None
    bad_nums = hallucinated_numbers(ft, corpus, task, months_val)
    if bad_nums:
        issues.append(f"hallucinated_numbers:{','.join(bad_nums)} (must appear verbatim in case)")

    # --- 风格：<=80 词 + decisive 因素关键词 ---
    wc = word_count(ft)
    if wc > 80:
        issues.append(f"free_text_too_long:{wc}words (cap 80)")
    if len(ft) < 40:
        issues.append(f"free_text_too_short:{len(ft)}chars (min 40)")
    if task in (1, 2):
        hits = var_keywords_hit(ft, weights)
        if hits < 1:
            issues.append("free_text_missing_decisive_factor (no decisive/important variable keyword)")

    # --- T1 EAU 方向性（PSA/PI-RADS/PSAD 阈值）---
    if task == 1 and obj.get("biopsy_decision") in ("yes", "no"):
        dec = obj["biopsy_decision"]
        pirads = _num_from_corpus(r'"pirads"\s*:\s*"?(\d+)"?', corpus)
        psad = _num_from_corpus(r'"psad"\s*:\s*([\d.]+)', corpus)
        psa = _num_from_corpus(r'"psa"\s*:\s*([\d.]+)', corpus)
        bx_neg = bool(re.search(r'"bx"\s*:\s*"[Nn]egative"', corpus))
        if dec == "no" and pirads is not None and pirads >= 5:
            issues.append(f"eau_contradiction: no-biopsy with PI-RADS {pirads}")
        if dec == "no" and pirads is not None and pirads >= 4 and psad is not None and psad >= 0.20:
            issues.append(f"eau_contradiction: no-biopsy with PI-RADS {pirads} & PSAD {psad}")
        if dec == "yes" and (pirads is not None and pirads <= 2) and (psad is not None and psad < 0.10) \
                and (psa is not None and psa < 4) and bx_neg:
            issues.append("eau_contradiction: yes-biopsy with PI-RADS<=2, PSAD<0.10, PSA<4, bx negative")

    # --- T2 低危不得 active_treatment（复用旧过滤思路）---
    if task == 2 and obj.get("treatment_decision") == "active_treatment":
        isup = _num_from_corpus(r'"bx_isup"\s*:\s*"?(\d+)"?', corpus)
        psa = _num_from_corpus(r'"psa"\s*:\s*([\d.]+)', corpus)
        ct = re.search(r'"ct"\s*:\s*"([^"]*)"', corpus)
        ct_low = bool(ct) and re.match(r"(?i)^(c?[Tt]1|c?[Tt]2a)", ct.group(1).strip())
        if isup == 1 and psa is not None and psa < 10 and (ct is None or ct_low):
            issues.append("low_risk_conflict: ISUP GG1 + PSA<10 + <=T2a must not get active_treatment")

    return (not issues), issues


def _num_from_corpus(pattern: str, corpus: str) -> float | None:
    m = re.search(pattern, corpus)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 管线：answer -> critique -> refine -> gate
# ---------------------------------------------------------------------------


def _normalize_obj(task: int, obj: dict) -> dict:
    """字段规范化（event 字符串转 int、free_text 去围栏）。"""
    if task == 3:
        ev = obj.get("event")
        if isinstance(ev, str) and ev in ("0", "1"):
            obj["event"] = int(ev)
    ft = obj.get("free_text")
    if isinstance(ft, str):
        obj["free_text"] = ft.strip().strip('`"')
    return obj


def pipeline_one(case_dir: Path, task: int, stats: dict) -> dict | None:
    """单个 label 任务的完整管线。返回 entry dict 或 None（拒收）。"""
    case_id = None
    try:
        summary = build_case_prompt(case_dir, task)
        case_id = summary["case_id"]
        corpus = build_corpus(case_dir, task)
        schema = answer_schema(task)
        user_payload = json.dumps(summary, ensure_ascii=False)

        # --- 1. 初答 ---
        content = chat(ANSWER_SYSTEM.format(schema=schema), user_payload, temperature=0.3)
        obj = _extract_json(content or "")
        if obj is None:
            stats["fail_parse"] += 1
            return None
        obj = _normalize_obj(task, obj)

        # --- 2/3. critique -> refine (最多 MAX_REFINE_ROUNDS) ---
        first_revise = None
        refine_rounds = 0
        refine_issues: list[str] = []
        for rnd in range(MAX_REFINE_ROUNDS + 1):
            crit_user = (
                "Clinical case:\n" + user_payload[:12000] +
                "\n\nCandidate answer:\n" + json.dumps(obj, ensure_ascii=False)
            )
            crit = _extract_json(chat(CRITIQUE_SYSTEM, crit_user, temperature=0.0) or "")
            verdict = (crit or {}).get("verdict")
            issues = [str(i) for i in (crit or {}).get("issues", []) if i]
            if rnd == 0:
                first_revise = (verdict == "revise")
            if verdict != "revise" and not issues:
                break
            if rnd == MAX_REFINE_ROUNDS:
                refine_issues = issues  # 记录后交给硬门控裁决
                break
            refine_issues = issues
            # --- refine 重答 ---
            ref_user = (
                "Clinical case:\n" + user_payload[:12000] +
                "\n\nYour previous answer:\n" + json.dumps(obj, ensure_ascii=False) +
                "\n\nIssues to fix:\n- " + "\n- ".join(issues) +
                "\n\nOutput the corrected complete JSON only."
            )
            new_obj = _extract_json(chat(REFINE_SYSTEM.format(schema=schema), ref_user, temperature=0.2) or "")
            if new_obj is None:
                break  # 保留上一版答案，交给硬门控
            stats["refine_calls"] += 1
            refine_rounds += 1
            obj = _normalize_obj(task, new_obj)

        # --- 4. 硬性规则门控 ---
        ok, gate_issues = gate_rules(task, obj, corpus)
        if not ok and refine_issues:
            # 门控失败 → 追加一轮带规则 issues 的 refine
            ref_user = (
                "Clinical case:\n" + user_payload[:12000] +
                "\n\nYour previous answer:\n" + json.dumps(obj, ensure_ascii=False) +
                "\n\nRule-check failures to fix:\n- " + "\n- ".join(gate_issues) +
                "\n\nOutput the corrected complete JSON only."
            )
            new_obj = _extract_json(chat(REFINE_SYSTEM.format(schema=schema), ref_user, temperature=0.2) or "")
            if new_obj is not None:
                stats["gate_refine_calls"] += 1
                obj = _normalize_obj(task, new_obj)
                ok, gate_issues = gate_rules(task, obj, corpus)

        if not ok:
            stats["fail_gate"] += 1
            stats["gate_reasons"].extend(gate_issues)
            return None

        # --- 组装 entry（与 pseudo_labels.json 同构）---
        if task == 1:
            decision = obj["biopsy_decision"]
        elif task == 2:
            decision = obj["treatment_decision"]
        else:
            try:
                m = float(obj.get("months_to_recurrence"))
            except (TypeError, ValueError):
                return None
            decision = {"event": int(obj["event"]), "months_to_recurrence": round(m, 2)}

        entry = {
            "case_id": case_id,
            "task": task,
            "decision": decision,
            "free_text": obj.get("free_text", ""),
            "meta": {
                "rounds": 1 + refine_rounds,
                "critique_first_revise": bool(first_revise),
                "gate_issues": gate_issues,
            },
        }
        if task in (1, 2):
            entry["confidence"] = obj.get("confidence", "clear")
            entry["variable_weights"] = obj.get("variable_weights", {})
        return entry
    except Exception as e:
        print(f"{LOG_PREFIX} pipeline_error {case_id}: {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Case discovery
# ---------------------------------------------------------------------------


def _excluded(name: str) -> bool:
    return name.startswith(_EXCLUDE_PREFIX) or _EXCLUDE_SUBSTR in name


def find_unlabeled_cases() -> list[tuple[Path, int, str]]:
    """187 个 unlabeled case：返回 (case_dir, own_task, case_id)。"""
    cases = []
    for t in (1, 2, 3):
        task_dir = DATA / f"task{t}" / "agent_input"
        if not task_dir.exists():
            continue
        for c in sorted(task_dir.iterdir()):
            if not c.is_dir() or (c / DECISION_FILE[t]).exists() or _excluded(c.name):
                continue
            if not (c / "structured-prompt.json").exists():
                continue
            try:
                sp = json.loads((c / "structured-prompt.json").read_text())
            except Exception:
                continue
            task = sp.get("task") or t
            cases.append((c, int(task), sp.get("case_id") or c.name))
    return cases


def build_work_items(cases: list[tuple[Path, int, str]]) -> list[tuple[Path, int, str]]:
    """own-task 项 + 全量 T3 项（task3 目录 case 的 own-task 即 T3，去重）。"""
    items = []
    t3_seen = set()
    for c, t, cid in cases:
        items.append((c, t, cid))          # own-task 标签
        if t != 3:
            items.append((c, 3, cid))      # T3 标签（g1 口径：全部 unlabeled）
        else:
            t3_seen.add(cid)
    return items


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def fresh_checkpoint() -> dict:
    return {"task1": [], "task2": [], "task3": []}


def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            ck = json.loads(CHECKPOINT_FILE.read_text())
            if isinstance(ck, dict) and all(k in ck for k in ("task1", "task2", "task3")):
                return ck
        except Exception:
            pass
    return fresh_checkpoint()


def save_checkpoint(ckpt: dict) -> None:
    CHECKPOINT_FILE.write_text(json.dumps(ckpt, indent=2, ensure_ascii=False) + "\n")


def checkpoint_case_ids(ckpt: dict) -> set[tuple[str, int]]:
    ids = set()
    for t in (1, 2, 3):
        for e in ckpt.get(f"task{t}", []):
            if isinstance(e, dict) and e.get("case_id"):
                ids.add((e["case_id"], t))
    return ids


# ---------------------------------------------------------------------------
# 汇总报告（revise 比例 / 幻觉率新旧对比 / 词数分布新旧对比 / yes 率）
# ---------------------------------------------------------------------------


def old_hallucination_stats() -> dict:
    """旧 GLM 伪数据幻觉率 + 词数分布基线。"""
    old_file = DATA / "pseudo_labels.json"
    out = {}
    if not old_file.exists():
        return out
    old = json.loads(old_file.read_text())
    cases = {cid: (cdir, t) for cdir, t, cid in find_unlabeled_cases()}
    for t in (1, 2, 3):
        entries = old.get(f"task{t}", [])
        n_hall = 0
        wcs = []
        for e in entries:
            ft = e.get("free_text", "")
            wcs.append(word_count(ft))
            cdir_task = cases.get(e.get("case_id"))
            if not cdir_task:
                continue
            corpus = build_corpus(cdir_task[0], cdir_task[1])
            months = None
            if cdir_task[1] == 3 and isinstance(e.get("decision"), dict):
                try:
                    months = float(e["decision"].get("months_to_recurrence"))
                except (TypeError, ValueError):
                    months = None
            if hallucinated_numbers(ft, corpus, cdir_task[1], months):
                n_hall += 1
        out[f"task{t}"] = {
            "n": len(entries),
            "hallucination_rate": round(n_hall / len(entries), 3) if entries else None,
            "wc_median": int(statistics.median(wcs)) if wcs else None,
            "wc_mean": round(statistics.mean(wcs), 1) if wcs else None,
            "wc_max": max(wcs) if wcs else None,
        }
    return out


def gt_yes_rate() -> float:
    vals = []
    for f in (DATA / "task1" / "agent_input").glob(f"*/{DECISION_FILE[1]}"):
        try:
            vals.append(str(json.loads(f.read_text())).lower() == "yes")
        except Exception:
            continue
    return sum(vals) / len(vals) if vals else 0.615


def build_summary(ckpt: dict, stats: dict, n_pending: int) -> dict:
    entries = {t: ckpt.get(f"task{t}", []) for t in (1, 2, 3)}
    summary: dict = {"model": MODEL, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # revise 比例 & 轮次
    for t in (1, 2, 3):
        es = entries[t]
        first_revise = sum(1 for e in es if e.get("meta", {}).get("critique_first_revise"))
        wcs = [word_count(e.get("free_text", "")) for e in es]
        summary[f"task{t}"] = {
            "n": len(es),
            "critique_revise_rate": round(first_revise / len(es), 3) if es else None,
            "wc_median": int(statistics.median(wcs)) if wcs else None,
            "wc_mean": round(statistics.mean(wcs), 1) if wcs else None,
            "wc_p90": int(sorted(wcs)[int(0.9 * len(wcs))]) if wcs else None,
            "wc_max": max(wcs) if wcs else None,
            "rejected_gate": stats["fail_gate_by_task"][t],
        }
        if t == 1 and es:
            yes = sum(1 for e in es if e["decision"] == "yes")
            summary["task1"]["yes_rate"] = round(yes / len(es), 3)
            summary["task1"]["gt_yes_rate"] = round(gt_yes_rate(), 3)

    # 新数据幻觉率
    cases = {cid: (cdir, tsk) for cdir, tsk, cid in find_unlabeled_cases()}
    for t in (1, 2, 3):
        n_hall = 0
        es = entries[t]
        for e in es:
            cinfo = cases.get(e["case_id"])
            if not cinfo:
                continue
            corpus = build_corpus(cinfo[0], cinfo[1])
            months = None
            if t == 3 and isinstance(e["decision"], dict):
                months = e["decision"].get("months_to_recurrence")
            if hallucinated_numbers(e.get("free_text", ""), corpus, t, months):
                n_hall += 1
        if es:
            summary[f"task{t}"]["hallucination_rate"] = round(n_hall / len(es), 3)

    summary["old_glm_baseline"] = old_hallucination_stats()
    summary["pipeline"] = {
        "total_items": n_pending,
        "fail_parse": stats["fail_parse"],
        "fail_gate": stats["fail_gate"],
        "refine_calls": stats["refine_calls"],
        "gate_refine_calls": stats["gate_refine_calls"],
        "gate_reasons_top": dict(Counter(
            i.split(":")[0].split(" ")[0] for i in stats["gate_reasons"]).most_common(10)),
    }
    return summary


def print_summary(summary: dict) -> None:
    print(f"\n{'='*64}\nG5 Summary\n{'='*64}")
    for t in (1, 2, 3):
        s = summary.get(f"task{t}", {})
        print(f"Task {t}: n={s.get('n')} revise_rate={s.get('critique_revise_rate')} "
              f"halluc={s.get('hallucination_rate')} wc_med={s.get('wc_median')} "
              f"wc_max={s.get('wc_max')} rejected={s.get('rejected_gate')}")
        if t == 1:
            print(f"  yes_rate={s.get('yes_rate')} (GT {s.get('gt_yes_rate')}, band ±10pp)")
    ob = summary.get("old_glm_baseline", {})
    for t in (1, 2, 3):
        o = ob.get(f"task{t}", {})
        print(f"Old GLM task{t}: n={o.get('n')} halluc={o.get('hallucination_rate')} "
              f"wc_med={o.get('wc_median')} wc_max={o.get('wc_max')}")
    p = summary.get("pipeline", {})
    print(f"Pipeline: items={p.get('total_items')} fail_parse={p.get('fail_parse')} "
          f"fail_gate={p.get('fail_gate')} refine_calls={p.get('refine_calls')}")
    print(f"Gate reasons top: {p.get('gate_reasons_top')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="G5: reflective pseudo-label regeneration")
    ap.add_argument("--smoke", action="store_true", help="只跑 10 个 label 任务")
    ap.add_argument("--resume", action="store_true", help="从 checkpoint 续跑")
    return ap.parse_args()


def main() -> None:
    _load_dotenv()
    args = _parse_args()
    get_client()

    cases = find_unlabeled_cases()
    items = build_work_items(cases)
    ckpt = load_checkpoint() if args.resume else fresh_checkpoint()
    done_ids = checkpoint_case_ids(ckpt) if args.resume else set()

    pending = [(c, t, cid) for (c, t, cid) in items if (cid, t) not in done_ids]
    if args.smoke:
        # 分层抽样：3 T1 + 3 T2 + 4 T3，覆盖不同来源目录
        by_t: dict[int, list] = {1: [], 2: [], 3: []}
        for it in items:
            by_t[it[1]].append(it)
        pending = by_t[1][:3] + by_t[2][:3] + by_t[3][:4]
        # smoke 不写 OUT_FILE 之外的数据文件（避免覆盖全量 checkpoint）
        ckpt = fresh_checkpoint()

    stats = {
        "fail_parse": 0,
        "fail_gate": 0,
        "refine_calls": 0,
        "gate_refine_calls": 0,
        "gate_reasons": [],
        "fail_gate_by_task": {1: 0, 2: 0, 3: 0},
    }

    print("=" * 64)
    print("G5 Reflective Pseudo-Label Regeneration (critique-refine)")
    print("=" * 64)
    print(f"Teacher: {MODEL}  BaseURL: {BASE_URL}")
    print(f"Unlabeled cases: {len(cases)}  label items: {len(items)}  pending: {len(pending)}")
    if args.smoke:
        print("SMOKE mode -- 10 items only (3 T1 / 3 T2 / 4 T3)")
    if args.resume:
        print(f"Resume -- skipped {len(items) - len(pending)} done items")
    print("=" * 64, flush=True)

    def run_one(item):
        cdir, task, cid = item
        return task, pipeline_one(cdir, task, stats)

    done_count = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(run_one, it) for it in pending]
        for fut in as_completed(futures):
            try:
                task, entry = fut.result()
            except Exception as e:
                print(f"{LOG_PREFIX} worker_error: {e}", flush=True)
                continue
            done_count += 1
            if entry is not None:
                ckpt[f"task{task}"].append(entry)
                print(f"  [{done_count}/{len(pending)}] task{task} {entry['case_id']}: OK "
                      f"rounds={entry['meta']['rounds']} "
                      f"revise={entry['meta']['critique_first_revise']}", flush=True)
            else:
                print(f"  [{done_count}/{len(pending)}] task{task}: REJECTED", flush=True)
            if done_count % CHECKPOINT_INTERVAL == 0 and not args.smoke:
                save_checkpoint(ckpt)

    if not args.smoke:
        save_checkpoint(ckpt)
        out = {
            "task1": sorted(ckpt["task1"], key=lambda e: e.get("case_id", "")),
            "task2": sorted(ckpt["task2"], key=lambda e: e.get("case_id", "")),
            "task3": sorted(ckpt["task3"], key=lambda e: e.get("case_id", "")),
        }
        # 数据集级门控：T1 yes 率须在 GT ±10pp 内（超出则按质量剔除多余一端）
        out = enforce_yes_rate_band(out)
        OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

        summary = build_summary(ckpt, stats, len(pending))
        SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
        print_summary(summary)
        print(f"\nOutput:   {OUT_FILE}\nCheckpoint: {CHECKPOINT_FILE}\nSummary:  {SUMMARY_FILE}")

        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(json.dumps({
            "stage": "A",
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "model": MODEL,
            "task1": len(out["task1"]), "task2": len(out["task2"]), "task3": len(out["task3"]),
        }, indent=2) + "\n")
        print(f"Lock:     {LOCK_FILE}")
    else:
        # smoke 也输出报告，便于检查质量
        summary = build_summary(ckpt, stats, len(pending))
        summary["smoke"] = True
        SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
        print_summary(summary)
        smoke_out = {
            "task1": sorted(ckpt["task1"], key=lambda e: e.get("case_id", "")),
            "task2": sorted(ckpt["task2"], key=lambda e: e.get("case_id", "")),
            "task3": sorted(ckpt["task3"], key=lambda e: e.get("case_id", "")),
        }
        (DATA / "pseudo_labels_v2_smoke.json").write_text(
            json.dumps(smoke_out, indent=2, ensure_ascii=False) + "\n")
        print(f"\nSmoke output: {DATA / 'pseudo_labels_v2_smoke.json'}")
        print("逐 case 检查后再启动全量！")


def enforce_yes_rate_band(out: dict) -> dict:
    """数据集级门控：T1 yes 率须在 GT±10pp 内，超出按质量剔除多余一端。"""
    t1 = out["task1"]
    if len(t1) < 10:
        return out
    gt = gt_yes_rate()

    def quality_key(e):
        return (e.get("meta", {}).get("rounds", 1), word_count(e.get("free_text", "")))

    dropped = 0
    while True:
        yes = [e for e in t1 if e["decision"] == "yes"]
        no = [e for e in t1 if e["decision"] != "yes"]
        rate = len(yes) / len(t1)
        if (gt - 0.10) <= rate <= (gt + 0.10) or (len(yes) <= 1 and len(no) <= 1):
            break
        if rate > gt + 0.10:
            worst = max(yes, key=quality_key)
            t1.remove(worst)
        else:
            worst = max(no, key=quality_key)
            t1.remove(worst)
        dropped += 1
    if dropped:
        print(f"{LOG_PREFIX} yes-rate band enforcement: dropped {dropped} entries "
              f"(final yes_rate={rate:.3f}, GT={gt:.3f})")
    out["task1"] = t1
    return out


if __name__ == "__main__":
    main()
