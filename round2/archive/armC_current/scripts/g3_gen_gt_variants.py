#!/usr/bin/env python3
"""G3：GT 病例数值变体增广（label-preserving augmentation）。

对每个有 ground-truth decision 的 GT case（task1/2/3），读 structured-prompt.json
与 decision 文件，调 GLM-5.2 生成 2–3 个数值/临床变体：PSA±20%、前列腺带区
(PZ/TZ/CZ)、Gleason 评分调整等，但 **保持 decision label 与原始 GT 一致**。
每个变体一行 JSON 写入 data/synthetic/gt_variants.jsonl。

合规：开发期使用 MIT 许可开源模型 GLM-5.2（智谱）生成合成训练数据；最终提交
系统以开源 Qwen3.6-35B-A3B 全离线运行，不依赖任何外部 API。

用法
----
  # 全量（所有 GT case，约 236 → 500-700 变体）
  python scripts/g3_gen_gt_variants.py

  # 冒烟（5 个 GT case，不写 .done）
  python scripts/g3_gen_gt_variants.py --smoke

  # 断点续跑（跳过 checkpoint 已完成的 case）
  python scripts/g3_gen_gt_variants.py --resume
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

OUT_FILE = DATA / "synthetic" / "gt_variants.jsonl"
CHECKPOINT_FILE = DATA / "g3_variants_checkpoint.json"
LOCK_FILE = ROOT / ".task_locks" / "G3.done"

SMOKE_N = 5
MAX_WORKERS = 6
VARIANTS_PER_CASE = 3  # 目标 2–3，上限 3
CHECKPOINT_EVERY = 20

# 每个 task 的 ground-truth 决策文件（存在 = 有 GT 标注）
DECISION_FILE = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}

# 官方/mock case 排除（与 gen_pseudo_labels_glm.py 一致）
_EXCLUDE_PREFIX = ("BX_", "T2_", "T3-mock")
_EXCLUDE_SUBSTR = "-mock-"

# 每个 task 可修改的临床/数值字段
TASK_FIELDS = {
    1: [
        "psa", "age", "bx_gl_prim", "bx_gl_sec", "bx_gl_tert",
        "vol", "psad", "psav", "psap", "pirads", "dre", "cspca", "zone",
    ],
    2: [
        "psa", "age", "bx_gl_prim", "bx_gl_sec", "bx_gl_tert",
        "vol", "psad", "psav", "psap", "pirads", "dre", "cspca", "zone",
    ],
    3: ["psa", "age", "dre", "zone"],
}

# ---------------------------------------------------------------------------
# GLM-5.2 客户端（与 g2/gen_pseudo_labels_glm 一致）
# ---------------------------------------------------------------------------

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


def _base_url() -> str:
    return os.environ.get("LLMAPI_BASE_URL", "https://llmapi.blsc.cn/v1")


def _model() -> str:
    return os.environ.get("GLM_MODEL", "GLM-5.2")


def get_client():
    global _client
    if _client is not None:
        return _client
    from openai import OpenAI

    api_key = os.environ.get("LLMAPI_KEY")
    if not api_key:
        sys.exit("ERROR: LLMAPI_KEY not set. Export it or add to .env")
    _client = OpenAI(api_key=api_key, base_url=_base_url(), max_retries=3, timeout=180)
    return _client


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a clinical data augmentation assistant for prostate cancer decision cases. "
    "Given an original case (structured clinical data + the ground-truth decision), generate "
    "2 to 3 NUMERICAL/CLINICAL VARIANTS that PRESERVE the ground-truth decision label exactly "
    "(label-preserving augmentation).\n\n"
    "For each variant, change only a few clinical/numerical fields. Do NOT reproduce the whole "
    "case — emit only the fields you are modifying:\n"
    "- PSA: vary by up to ±20% of the original; must stay positive and clinically realistic.\n"
    "- Prostate zone: set a 'zone' field to 'PZ', 'TZ', or 'CZ' (add it if absent).\n"
    "- Gleason primary/secondary (bx_gl_prim, bx_gl_sec): integers 3–5 only.\n"
    "- Age: 40–90.\n"
    "- Optionally PSAD, prostate volume (vol), DRE — keep clinically realistic.\n"
    "Modifiable fields for this task: {fields}.\n\n"
    "Output ONLY a JSON array (no markdown fences, no prose outside JSON) of 2–3 elements, "
    "each an object with EXACTLY these keys:\n"
    '{{"modifications": {{"<field>": <new_value>, ...}}, '
    '"decision": <copied verbatim from the original decision>, '
    '"variant_desc": "<short description of what changed>"}}\n\n'
    "Rules:\n"
    "- 'decision' MUST be identical to the original ground-truth decision (copy it verbatim; "
    "do NOT change the label).\n"
    "- 'modifications' must be a non-empty object and may only include modifiable/clinical fields.\n"
    "- Do NOT include narrative text fields (notes, note_sections, free_text, etc.).\n"
    "- Keep every numeric value in a clinically realistic range.\n"
    "- Make each variant distinct."
)


# ---------------------------------------------------------------------------
# JSON salvage（与 g2/gen_pseudo_labels_glm 一致，扩展支持数组）
# ---------------------------------------------------------------------------


def _extract_json_any(text: str):
    """从 GLM 响应中提取 JSON（dict 或 list）。"""
    if not text:
        return None
    text = text.strip()
    # 1. 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2. 去除 markdown 围栏（数组或对象）
    m = re.search(r"```(?:json)?\s*(\[[\s\S]*?\]|\{[\s\S]*?\})\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 3. 提取第一个 [...] 块
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # 4. 提取第一个 {...} 块（单变体兜底）
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# 数值工具
# ---------------------------------------------------------------------------


def _num(v, default=None):
    try:
        f = float(v)
        return f if f == f else default  # NaN check
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# GT case 发现
# ---------------------------------------------------------------------------


def _excluded(name: str) -> bool:
    return name.startswith(_EXCLUDE_PREFIX) or _EXCLUDE_SUBSTR in name


def find_gt_cases() -> list[tuple[Path, int]]:
    """返回所有有 decision 文件、非官方/mock 的 GT case 目录列表。"""
    out: list[tuple[Path, int]] = []
    for task in (1, 2, 3):
        task_dir = DATA / f"task{task}" / "agent_input"
        if not task_dir.exists():
            continue
        for c in sorted(task_dir.iterdir()):
            if not c.is_dir():
                continue
            if _excluded(c.name):
                continue
            if not (c / DECISION_FILE[task]).exists():
                continue
            out.append((c, task))
    return out


# ---------------------------------------------------------------------------
# 变体构建 + 校验
# ---------------------------------------------------------------------------


def _build_final_sp(original_sp: dict, vobj: dict) -> dict | None:
    """把 GLM 返回的 modifications 合并进原始 structured_prompt。

    兼容两种 GLM 输出：
      - "modifications": {field: value} -> 合并进 original_sp 的深拷贝
      - "structured_prompt": {完整对象} -> 直接深拷贝使用
    """
    if isinstance(vobj.get("structured_prompt"), dict):
        return copy.deepcopy(vobj["structured_prompt"])
    mods = vobj.get("modifications")
    if isinstance(mods, dict):
        sp = copy.deepcopy(original_sp)
        for k, v in mods.items():
            sp[k] = v
        return sp
    return None


def validate_variant(task: int, vobj: dict, original_decision, final_sp) -> tuple[bool, str]:
    """校验单个变体。返回 (valid, reason)。"""
    if not isinstance(final_sp, dict):
        return False, "bad_structured_prompt"

    # decision 必须存在且与原始 GT 一致（label-preserving 校验）
    dec = vobj.get("decision")
    if dec is None:
        return False, "no_decision"
    if task in (1, 2):
        if str(dec).strip().lower() != str(original_decision).strip().lower():
            return False, f"decision_mismatch:{dec}!={original_decision}"
    else:  # task 3: {"months_to_recurrence": num, "event": 0|1}
        if not isinstance(dec, dict):
            return False, "decision_not_object"
        if dec.get("event") != original_decision.get("event"):
            return False, (
                f"event_mismatch:{dec.get('event')}!={original_decision.get('event')}"
            )

    # modifications 路径要求非空（确保是真变体）
    if "modifications" in vobj:
        mods = vobj.get("modifications")
        if not isinstance(mods, dict) or not mods:
            return False, "empty_modifications"

    # PSA 必须为正且在临床范围
    psa = _num(final_sp.get("psa"))
    if psa is not None:
        if psa <= 0:
            return False, f"bad_psa:{psa}"
        if psa > 200:
            return False, f"psa_out_of_range:{psa}"

    # Gleason primary/secondary 3–5
    for k in ("bx_gl_prim", "bx_gl_sec"):
        if final_sp.get(k) is not None:
            g = _num(final_sp.get(k))
            if g is None or not (3 <= g <= 5):
                return False, f"bad_gleason_{k}:{final_sp.get(k)}"

    # age 40–90
    if final_sp.get("age") is not None:
        age = _num(final_sp.get("age"))
        if age is None or not (40 <= age <= 90):
            return False, f"bad_age:{final_sp.get('age')}"

    # 前列腺体积
    if final_sp.get("vol") is not None:
        vol = _num(final_sp.get("vol"))
        if vol is not None and not (5 <= vol <= 400):
            return False, f"bad_vol:{final_sp.get('vol')}"

    # PSAD
    if final_sp.get("psad") is not None:
        psad = _num(final_sp.get("psad"))
        if psad is not None and not (0 < psad <= 5):
            return False, f"bad_psad:{final_sp.get('psad')}"

    # variant_desc 非空
    vd = vobj.get("variant_desc")
    if not (isinstance(vd, str) and vd.strip()):
        return False, "bad_variant_desc"

    return True, "ok"


# ---------------------------------------------------------------------------
# 单 case 变体生成
# ---------------------------------------------------------------------------


def gen_variants_for_case(
    case_dir: Path, task: int, original_decision, original_sp: dict
) -> tuple[list, str]:
    """调 GLM 产出一个 case 的 2–3 个变体原始对象。返回 (list_of_vobj, status)。"""
    client = get_client()
    payload = {
        "task": task,
        "original_case_id": case_dir.name,
        "decision": original_decision,
        "structured_prompt": original_sp,
        "modifiable_fields": TASK_FIELDS[task],
    }
    sys_msg = SYSTEM_PROMPT.format(fields=", ".join(TASK_FIELDS[task]))

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=_model(),
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.7,
                max_tokens=4096,
            )
            content = resp.choices[0].message.content
            parsed = _extract_json_any(content or "")
            if parsed is not None:
                if isinstance(parsed, dict):
                    parsed = [parsed]
                if isinstance(parsed, list):
                    return parsed, "ok"
            if attempt == 0:
                time.sleep(1)
                continue
            return [], "salvage_failed"
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            return [], f"api_error: {e}"
    return [], "salvage_failed"


def process_case(case_dir: Path, task: int) -> dict:
    """处理单个 GT case：读文件 -> 调 GLM -> 校验 -> 组装变体记录。"""
    try:
        sp = json.loads((case_dir / "structured-prompt.json").read_text())
        decision = json.loads((case_dir / DECISION_FILE[task]).read_text())
    except Exception as e:
        return {
            "case_id": case_dir.name, "task": task, "error": f"read_error:{e}",
            "records": [], "attempted": 0, "valid": 0, "fails": [],
        }

    raw, status = gen_variants_for_case(case_dir, task, decision, sp)
    if status != "ok":
        return {
            "case_id": case_dir.name, "task": task, "error": status,
            "records": [], "attempted": 0, "valid": 0, "fails": [],
        }

    records: list[dict] = []
    attempted = 0
    valid = 0
    fails: list[str] = []
    for vobj in raw:
        if not isinstance(vobj, dict):
            continue
        attempted += 1
        final_sp = _build_final_sp(sp, vobj)
        ok, reason = validate_variant(task, vobj, decision, final_sp)
        if not ok:
            fails.append(reason)
            continue
        valid += 1
        var_id = f"{case_dir.name}_v{valid}"
        if isinstance(final_sp, dict) and "case_id" in final_sp:
            final_sp["case_id"] = var_id
        rec = {
            "case_id": var_id,
            "task": task,
            "original_case_id": case_dir.name,
            "structured_prompt": final_sp,
            "decision": decision,  # same_as_original
            "variant_desc": str(vobj.get("variant_desc", "")).strip(),
        }
        records.append(rec)
        if valid >= VARIANTS_PER_CASE:
            break

    return {
        "case_id": case_dir.name, "task": task, "error": None,
        "records": records, "attempted": attempted, "valid": valid, "fails": fails,
    }


# ---------------------------------------------------------------------------
# Checkpoint（断点续跑）
# ---------------------------------------------------------------------------


def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text())
        except Exception:
            pass
    return {"done": [], "variants": []}


def save_checkpoint(ckpt: dict) -> None:
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_FILE.write_text(json.dumps(ckpt, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _new_stats(total_cases: int) -> dict:
    return {
        "total_cases": total_cases,
        "cases_processed": 0,
        "cases_ok": 0,
        "cases_error": 0,
        "variants_attempted": 0,
        "variants_valid": 0,
        "variants_invalid": 0,
        "per_task": {
            t: {"cases": 0, "variants": 0, "attempted": 0, "errors": 0}
            for t in (1, 2, 3)
        },
    }


def main() -> None:
    _load_dotenv()
    ap = argparse.ArgumentParser(description="G3: GT 病例数值变体增广")
    ap.add_argument("--smoke", action="store_true", help=f"仅处理 {SMOKE_N} 个 GT case，不写 .done")
    ap.add_argument("--resume", action="store_true", help="从 checkpoint 续跑")
    args = ap.parse_args()

    all_cases = find_gt_cases()
    base_url = _base_url()
    model = _model()

    print(f"G3 GT 变体增广 | model={model} base_url={base_url}")
    print(f"发现 GT case: {len(all_cases)} | workers={MAX_WORKERS} "
          f"| 变体/case≤{VARIANTS_PER_CASE}")
    if args.smoke:
        print(f"⚠ 冒烟模式：仅 {SMOKE_N} 个 case，不写 .task_locks/G3.done")
    if args.resume:
        print("断点续跑：跳过 checkpoint 已完成的 case")
    print(f"{'=' * 60}")

    # 选 case 子集
    if args.smoke:
        to_process = all_cases[:SMOKE_N]
        ckpt: dict = {"done": [], "variants": []}
    else:
        ckpt = load_checkpoint() if args.resume else {"done": [], "variants": []}
        done_ids = set(ckpt.get("done", []))
        to_process = [c for c in all_cases if c[0].name not in done_ids]
        if args.resume:
            print(f"checkpoint 已完成 {len(done_ids)} case，剩余 {len(to_process)} 待处理")
            print(f"{'=' * 60}")

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 已累积的变体（resume 时含历史）
    all_variants: list[dict] = ckpt.setdefault("variants", [])
    done_list: list[str] = ckpt.setdefault("done", [])

    stats = _new_stats(len(all_cases))
    for t in (1, 2, 3):
        stats["per_task"][t]["cases"] = sum(1 for c in all_cases if c[1] == t)

    processed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_case, c[0], c[1]): c for c in to_process}
        for fut in as_completed(futures):
            res = fut.result()
            processed += 1
            stats["cases_processed"] += 1
            cid = res["case_id"]
            task = res["task"]

            if res["error"]:
                stats["cases_error"] += 1
                stats["per_task"][task]["errors"] += 1
                print(f"  [{processed}/{len(to_process)}] {cid} (task{task}) "
                      f"GEN_FAIL ({res['error']})")
            else:
                stats["cases_ok"] += 1
                stats["variants_attempted"] += res["attempted"]
                stats["variants_valid"] += res["valid"]
                stats["variants_invalid"] += res["attempted"] - res["valid"]
                stats["per_task"][task]["attempted"] += res["attempted"]
                stats["per_task"][task]["variants"] += res["valid"]
                all_variants.extend(res["records"])
                done_list.append(cid)
                if res["fails"]:
                    print(f"  [{processed}/{len(to_process)}] {cid} (task{task}) "
                          f"OK {res['valid']}/{res['attempted']} "
                          f"(discard: {', '.join(res['fails'][:3])})")
                else:
                    print(f"  [{processed}/{len(to_process)}] {cid} (task{task}) "
                          f"OK {res['valid']}/{res['attempted']}")

            # 每 CHECKPOINT_EVERY 个 case 落盘 checkpoint
            if processed % CHECKPOINT_EVERY == 0:
                save_checkpoint(ckpt)

    save_checkpoint(ckpt)

    # 写 jsonl（全部累积变体）
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for v in all_variants:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")

    # 统计
    pass_rate = (
        stats["variants_valid"] / stats["variants_attempted"] * 100
        if stats["variants_attempted"]
        else 0.0
    )

    print(f"\n{'=' * 60}")
    print(f"G3 统计")
    print(f"{'=' * 60}")
    print(f"  total GT cases       : {stats['total_cases']}")
    print(f"  cases processed      : {stats['cases_processed']}")
    print(f"    - ok               : {stats['cases_ok']}")
    print(f"    - error            : {stats['cases_error']}")
    print(f"  variants attempted   : {stats['variants_attempted']}")
    print(f"  variants generated   : {stats['variants_valid']}")
    print(f"  variants discarded   : {stats['variants_invalid']}")
    print(f"  validation pass rate : {pass_rate:.1f}%")
    print(f"  per-task breakdown:")
    for t in (1, 2, 3):
        pt = stats["per_task"][t]
        pr = pt["variants"] / pt["attempted"] * 100 if pt["attempted"] else 0.0
        print(f"    task{t}: cases={pt['cases']} variants={pt['variants']} "
              f"attempted={pt['attempted']} errors={pt['errors']} pass={pr:.1f}%")
    print(f"  output               : {OUT_FILE}")
    print(f"  checkpoint           : {CHECKPOINT_FILE}")

    # 非 smoke 模式写 .done
    if not args.smoke:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_stats = {
            "task": "G3",
            "total_gt_cases": stats["total_cases"],
            "cases_processed": stats["cases_processed"],
            "cases_ok": stats["cases_ok"],
            "cases_error": stats["cases_error"],
            "variants_attempted": stats["variants_attempted"],
            "variants_generated": stats["variants_valid"],
            "validation_pass_rate": round(pass_rate, 2),
            "per_task": {
                str(t): stats["per_task"][t] for t in (1, 2, 3)
            },
            "output": str(OUT_FILE),
        }
        LOCK_FILE.write_text(json.dumps(lock_stats, indent=2, ensure_ascii=False) + "\n")
        print(f"  lock                 : {LOCK_FILE}")

    if stats["cases_error"]:
        print(f"\n⚠ {stats['cases_error']} 个 case 生成失败（见上方 GEN_FAIL）")
    if not args.smoke and stats["variants_valid"] == 0:
        print("\n⚠ 未生成任何有效变体")
        sys.exit(1)


if __name__ == "__main__":
    main()
