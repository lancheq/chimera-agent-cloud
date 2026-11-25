#!/usr/bin/env python3
"""G2：生成合成灰区 T1 病例（PSA 3-10, PI-RADS 3）。

调 GLM-5.2 经 llmapi.blsc.cn 合成临床真实的灰区前列腺穿刺决策病例，
每条 JSON 一行写入 data/synthetic/t1_grayzone.jsonl。

合规：开发期使用 MIT 许可开源模型 GLM-5.2（智谱）生成合成训练数据；
最终提交系统以开源 Qwen3.6-35B-A3B 全离线运行，不依赖任何外部 API。

用法
----
  # 全量（80 case）
  python scripts/g2_gen_synthetic_t1.py

  # 冒烟（5 case，不写 .done）
  python scripts/g2_gen_synthetic_t1.py --smoke
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

OUT_FILE = DATA / "synthetic" / "t1_grayzone.jsonl"
LOCK_FILE = ROOT / ".task_locks" / "G2.done"

TARGET = 80
BATCH = 10
MAX_ATTEMPTS = 200
SMOKE_N = 5
MAX_WORKERS = 4

# ---------------------------------------------------------------------------
# GLM-5.2 客户端（与 gen_pseudo_labels_glm.py 一致）
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
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a clinical data generator for prostate cancer biopsy decision cases. "
    "Generate ONE clinically realistic synthetic case per request. "
    "Each case MUST be unique: vary PSA, age, PSAD, prostate volume, DRE, prior biopsy, "
    "and family history details so no two cases are identical. "
    "Output ONLY a valid JSON object with no markdown fences, no code blocks, "
    "and no text outside the JSON. The JSON must contain exactly these fields:\n"
    "{\n"
    '  "psa": <number 3-10 ng/mL>,\n'
    '  "age": <integer 50-75>,\n'
    '  "pirads": "3",\n'
    '  "psad": <number 0.1-0.35>,\n'
    '  "vol": <number 20-150 mL prostate volume>,\n'
    '  "dre": "Normal" | "Abnormal" | "Ination",\n'
    '  "bx": "Positive" | "Negative" | "None",\n'
    '  "cspca": <0 or 1 clinically significant prostate cancer flag>,\n'
    '  "biopsy_decision": "yes" | "no",\n'
    '  "free_text": "<>= 40 chars clinical reasoning for the biopsy decision>"\n'
    "}\n"
    "Rules:\n"
    "- PSA must be 3-10 ng/mL.\n"
    "- PI-RADS must be \"3\".\n"
    "- Age must be 50-75.\n"
    "- PSAD must be 0.1-0.35.\n"
    "- vol must be 20-150 mL.\n"
    "- dre must be one of: Normal, Abnormal, Ination.\n"
    "- bx must be one of: Positive, Negative, None.\n"
    "- cspca must be 0 or 1.\n"
    "- biopsy_decision must be yes or no.\n"
    "- free_text must be >= 40 characters of realistic clinical reasoning.\n"
    "- Ensure clinical realism: PSAD should be roughly psa/vol.\n"
)

USER_PROMPT = (
    "Generate a clinically realistic prostate cancer case for biopsy decision. "
    "PSA must be 3-10 ng/mL, PI-RADS must be 3, age 50-75, PSAD 0.1-0.35. "
    "Output JSON with fields: psa, age, pirads, psad, vol, dre, bx, cspca, "
    "biopsy_decision(yes/no), free_text reasoning."
)


# ---------------------------------------------------------------------------
# JSON salvage（与 gen_pseudo_labels_glm.py 一致）
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


# ---------------------------------------------------------------------------
# Schema 校验
# ---------------------------------------------------------------------------

_DRE = {"Normal", "Abnormal", "Ination"}
_BX = {"Positive", "Negative", "None"}
_BIOPSY = {"yes", "no"}


def _num(v, default=None):
    try:
        f = float(v)
        return f if f == f else default  # NaN check
    except (TypeError, ValueError):
        return default


def validate_case(obj: dict) -> tuple[bool, str]:
    """校验合成病例是否符合灰区 schema。返回 (valid, reason)。"""
    if not isinstance(obj, dict):
        return False, "not_a_dict"

    psa = _num(obj.get("psa"))
    if psa is None or not (3 <= psa <= 10):
        return False, f"bad_psa:{obj.get('psa')}"

    age = obj.get("age")
    try:
        age = int(age)
    except (TypeError, ValueError):
        return False, f"bad_age:{obj.get('age')}"
    if not (50 <= age <= 75):
        return False, f"bad_age:{age}"

    pirads = str(obj.get("pirads", "")).strip()
    if pirads != "3":
        return False, f"bad_pirads:{obj.get('pirads')}"

    psad = _num(obj.get("psad"))
    if psad is None or not (0.1 <= psad <= 0.35):
        return False, f"bad_psad:{obj.get('psad')}"

    vol = _num(obj.get("vol"))
    if vol is None or not (20 <= vol <= 150):
        return False, f"bad_vol:{obj.get('vol')}"

    dre = str(obj.get("dre", "")).strip()
    if dre not in _DRE:
        return False, f"bad_dre:{obj.get('dre')}"

    bx = str(obj.get("bx", "")).strip()
    if bx not in _BX:
        return False, f"bad_bx:{obj.get('bx')}"

    cspca = obj.get("cspca")
    try:
        cspca = int(cspca)
    except (TypeError, ValueError):
        return False, f"bad_cspca:{obj.get('cspca')}"
    if cspca not in (0, 1):
        return False, f"bad_cspca:{cspca}"

    bd = str(obj.get("biopsy_decision", "")).strip().lower()
    if bd not in _BIOPSY:
        return False, f"bad_biopsy_decision:{obj.get('biopsy_decision')}"
    obj["biopsy_decision"] = bd  # normalize

    ft = obj.get("free_text")
    if not (isinstance(ft, str) and len(ft) >= 40):
        return False, "free_text_too_short"

    return True, "ok"


def case_signature(obj: dict) -> tuple:
    """用于去重的 case 签名（关键数值字段四舍五入）。"""
    return (
        round(_num(obj.get("psa"), 0) or 0, 1),
        int(obj.get("age", 0) or 0),
        str(obj.get("pirads", "")).strip(),
        round(_num(obj.get("psad"), 0) or 0, 3),
        round(_num(obj.get("vol"), 0) or 0, 1),
        str(obj.get("dre", "")).strip(),
        str(obj.get("bx", "")).strip(),
        int(obj.get("cspca", -1) if obj.get("cspca") is not None else -1),
        str(obj.get("biopsy_decision", "")).strip().lower(),
    )


# ---------------------------------------------------------------------------
# 生成单个 case
# ---------------------------------------------------------------------------


def gen_one(idx: int) -> tuple[dict | None, str]:
    """调 GLM 产出一个合成病例。返回 (parsed_json, status)。

    status: "ok" | "salvage_failed" | "api_error"
    """
    client = get_client()
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": USER_PROMPT},
                ],
                temperature=1.0,
                max_tokens=1024,
            )
            content = resp.choices[0].message.content
            parsed = _extract_json(content or "")
            if parsed is not None:
                return parsed, "ok"
            if attempt == 0:
                time.sleep(1)
                continue
            return None, "salvage_failed"
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            return None, f"api_error: {e}"
    return None, "salvage_failed"


def gen_batch(n: int, start_idx: int) -> list[tuple[dict | None, str, int]]:
    """用 ThreadPoolExecutor 生成一批 n 个 case。"""
    results: list[tuple[dict | None, str, int]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(gen_one, start_idx + i): start_idx + i for i in range(n)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                obj, status = fut.result()
            except Exception as e:
                obj, status = None, f"api_error: {e}"
            results.append((obj, status, idx))
    return results


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> None:
    _load_dotenv()
    ap = argparse.ArgumentParser(description="G2: 生成合成灰区 T1 病例")
    ap.add_argument("--smoke", action="store_true", help=f"仅生成 {SMOKE_N} 个 case，不写 .done")
    args = ap.parse_args()

    target = SMOKE_N if args.smoke else TARGET
    max_attempts = SMOKE_N * 10 if args.smoke else MAX_ATTEMPTS

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    print(f"G2 合成灰区 T1 病例 | model={MODEL} base_url={BASE_URL}")
    print(f"目标: {target} case | 批大小: {BATCH} | 最大尝试: {max_attempts}")
    if args.smoke:
        print("⚠ 冒烟模式：不写 .task_locks/G2.done")
    print(f"{'='*60}")

    valid_cases: list[dict] = []
    seen_sigs: set[tuple] = set()
    total_generated = 0
    invalid_count = 0
    duplicate_count = 0
    attempt = 0

    while len(valid_cases) < target and attempt < max_attempts:
        need = min(BATCH, target - len(valid_cases))
        # 至少生成 BATCH 个以补偿无效/重复
        batch_size = min(BATCH, max(need, BATCH))
        attempt += batch_size
        batch_results = gen_batch(batch_size, attempt)

        for obj, status, idx in batch_results:
            total_generated += 1
            if status != "ok" or obj is None:
                invalid_count += 1
                print(f"  [{total_generated}] GEN_FAIL ({status})")
                continue

            ok, reason = validate_case(obj)
            if not ok:
                invalid_count += 1
                print(f"  [{total_generated}] SCHEMA_FAIL ({reason})")
                continue

            sig = case_signature(obj)
            if sig in seen_sigs:
                duplicate_count += 1
                print(f"  [{total_generated}] DUPLICATE (discarded)")
                continue

            seen_sigs.add(sig)
            valid_cases.append(obj)
            print(f"  [{total_generated}] OK "
                  f"({len(valid_cases)}/{target}) "
                  f"psa={obj.get('psa')} age={obj.get('age')} "
                  f"psad={obj.get('psad')} bx={obj.get('bx')} "
                  f"decision={obj.get('biopsy_decision')}")

        if len(valid_cases) >= target:
            break

    # 写 jsonl
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for c in valid_cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # 统计
    discarded = invalid_count + duplicate_count
    pass_rate = (len(valid_cases) / total_generated * 100) if total_generated else 0.0

    print(f"\n{'='*60}")
    print(f"G2 统计")
    print(f"{'='*60}")
    print(f"  total generated : {total_generated}")
    print(f"  valid count     : {len(valid_cases)}")
    print(f"  invalid/discarded: {discarded}")
    print(f"    - schema fail  : {invalid_count - duplicate_count if duplicate_count else invalid_count}")
    print(f"    - duplicates   : {duplicate_count}")
    print(f"  schema pass rate : {pass_rate:.1f}%")
    print(f"  output           : {OUT_FILE}")

    # 非 smoke 模式写 .done
    if not args.smoke:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        stats = {
            "task": "G2",
            "target": target,
            "total_generated": total_generated,
            "valid_count": len(valid_cases),
            "invalid_discarded": discarded,
            "schema_pass_rate": round(pass_rate, 2),
            "output": str(OUT_FILE),
        }
        LOCK_FILE.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n")
        print(f"  lock             : {LOCK_FILE}")

    if len(valid_cases) < target:
        print(f"\n⚠ 仅生成 {len(valid_cases)}/{target} 个有效 case（达到最大尝试 {max_attempts}）")
        sys.exit(1)


if __name__ == "__main__":
    main()
