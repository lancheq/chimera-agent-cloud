#!/usr/bin/env python3
"""
从官方 predictions.json 提取 6 个 test case 的临床数据，
构造 agent 所需的 structured-prompt.json + clinical-data.json。

输出布局:
  data/task{N}/agent_input_official/<case_id>/
    structured-prompt.json                        # 结构化字段 (Jinja 模板渲染用)
    prostate-{task-slug}-clinical-data.json       # 原始文档 (MCP 工具读取)

用法:
  python scripts/prepare_official_cases.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

OFFICIAL = ROOT / "data_backup/upload_evaluation_official/CHIMERA-agent/evaluation"
PRED_FILE = OFFICIAL / "test/input/predictions.json"
PK_MAP = OFFICIAL / "test/input/pk_hash_to_case_map.json"
GT_BASE = OFFICIAL / "ground_truth"

OUT_BASE = ROOT / "data"

# Task → input socket slug
TASK_INPUT_SLUGS = {
    1: "prostate-biopsy-decision-clinical-data",
    2: "prostate-treatment-decision-clinical-data",
    3: "prostate-time-to-recurrence-or-last-follow-up-clin",
}

# Task → clinical data filename (agent 期望的文件名)
TASK_CLINICAL_FILENAMES = {
    1: "prostate-biopsy-decision-clinical-data.json",
    2: "prostate-treatment-decision-clinical-data.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
}


def _pk_to_case_map() -> dict[str, str]:
    data = json.loads((PK_MAP).read_text())
    return data.get("pk_to_case", {})


def _detect_task(case_path: str) -> int:
    """从 case_path (如 'test/input/task1/BX_01') 推断 task 编号。"""
    m = re.search(r"task(\d)", case_path)
    return int(m.group(1)) if m else 0


def _case_id(case_path: str) -> str:
    return Path(case_path).name


def _extract_psa(psa_trend: list[dict] | None, lab_results: list[dict] | None) -> tuple[float | None, float | None, float | None, int | None]:
    """从 psa_trend 和 lab_results 提取 PSA 相关字段。

    Returns: (psa_latest, psa_prior, psa_velocity, months_since_last)
    """
    if not psa_trend:
        # fallback: 从 lab_results 找 PSA
        if lab_results:
            for r in lab_results:
                name = str(r.get("name", "")).lower()
                if "psa" in name and "free" not in name:
                    val = r.get("val", "")
                    m = re.search(r"(\d+\.?\d*)", str(val))
                    if m:
                        return float(m.group(1)), None, None, None
        return None, None, None, None

    # psa_trend 格式: [{"val": 8.5, "date": "Jan 2025"}, ...]
    vals = [(r.get("val"), r.get("date")) for r in psa_trend if r.get("val") is not None]
    if not vals:
        return None, None, None, None

    psa_latest = float(vals[-1][0])
    psa_prior = float(vals[-2][0]) if len(vals) >= 2 else None

    # PSA velocity (ng/mL/yr)
    psav = None
    if len(vals) >= 2 and psa_prior is not None:
        psav = round(psa_latest - psa_prior, 2)

    # months since last PSA (粗略: 假设最后两个日期间隔)
    months = None
    if len(vals) >= 2:
        months = 1  # placeholder, 无法精确计算

    return psa_latest, psa_prior, psav, months


def _search_text(pattern: str, text: str, cast: type = str) -> Any:
    """在文本中搜索模式并返回捕获组。"""
    if not text:
        return None
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    val = m.group(1) if m.groups() else m.group(0)
    if val is None:
        return None
    val = val.strip()
    if cast is float:
        try:
            return float(val)
        except ValueError:
            return None
    elif cast is int:
        try:
            return int(val)
        except ValueError:
            return None
    return val


def _parse_radiology(report: str) -> dict[str, Any]:
    """从 MRI 报告文本提取结构化字段。"""
    if not report:
        return {}
    return {
        "pirads": _search_text(r"PI-?RADS[:\s]*(\d)", report, cast=str),
        "psad": _search_text(r"PSA\s*density[:\s]*(\d+\.?\d*)", report, cast=float),
        "vol": _search_text(r"[Pp]rostate\s*volume[:\s]*(\d+\.?\d*)", report, cast=float),
        "cspca": _search_text(r"csPCa[:\s]*(\d+\.?\d*)|clinically\s*significant.*?(\d+\.?\d*)", report, cast=float),
        "mri_hospital": _search_text(r"^([^\n]+)", report, cast=str) if report else None,
    }


def _parse_pathology(report: str) -> dict[str, Any]:
    """从病理报告提取 Gleason/ISUP。"""
    if not report:
        return {}
    gl = _search_text(r"Gleason[:\s]*(\d)\s*\+\s*(\d)", report)
    isup = _search_text(r"ISUP\s*(?:grade\s*group)?[:\s]*(\d)", report, cast=int)
    result = {}
    if gl:
        m = re.search(r"Gleason[:\s]*(\d)\s*\+\s*(\d)", report, re.IGNORECASE)
        if m:
            result["bx_gl_prim"] = int(m.group(1))
            result["bx_gl_sec"] = int(m.group(2))
    if isup:
        result["bx_isup"] = isup
    return result


def _parse_dre(notes_text: str) -> str | None:
    """从既往笔记提取 DRE。"""
    if not notes_text:
        return None
    m = re.search(r"DRE[:\s]*(\w+)|digital\s*rectal\s*exam[:\s]*(\w+)", notes_text, re.IGNORECASE)
    if m:
        val = m.group(1) or m.group(2)
        return val.capitalize() if val else None
    return None


def _build_structured_prompt(
    case_id: str,
    task: int,
    clinical_data: dict[str, Any],
    gt_patient: dict[str, Any] | None = None,
    gt_clinical: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 structured-prompt.json。

    优先从 clinical_data (原始文档) 解析;
    解析不到的字段从 gt_patient/gt_clinical (患者卡片 always-available 数据) 补充。
    """
    # 原始文档
    radiology = clinical_data.get("radiology_report", "")
    pathology = clinical_data.get("pathology_report", "")
    notes = clinical_data.get("previous_notes", [])
    psa_trend = clinical_data.get("psa_trend", [])
    labs = clinical_data.get("laboratory_results", [])
    fam_hx = clinical_data.get("family_history", "")

    # 解析
    psa, psap, psav, months = _extract_psa(psa_trend, labs)
    radio_fields = _parse_radiology(radiology)
    path_fields = _parse_pathology(pathology)

    # notes 拼接
    notes_text = " ".join(n.get("text", "") for n in notes) if isinstance(notes, list) else str(notes)
    dre = _parse_dre(notes_text)

    # GT 患者卡片数据 (age, psa 是 always-available, 在真实 form 上也是可见的)
    gt_p = gt_patient or {}
    gt_c = gt_clinical or {}

    # 构造 prompt payload
    prompt: dict[str, Any] = {
        "case_id": case_id,
        "id": case_id,
        "pid": case_id,
        "task": task,
        # 患者卡片 (always available)
        "age": gt_p.get("age_years") or gt_p.get("age"),
        "psa": psa or gt_p.get("psa_ng_ml") or gt_p.get("psa"),
        # 从 MRI 报告解析
        "pirads": radio_fields.get("pirads") or str(gt_c.get("pirads", "")) if gt_c.get("pirads") else radio_fields.get("pirads"),
        "psad": radio_fields.get("psad") or gt_c.get("psa_density"),
        "psav": psav or gt_c.get("psa_velocity"),
        "psap": psap or gt_c.get("prior_psa"),
        "vol": radio_fields.get("vol") or gt_c.get("prostate_vol"),
        "months": months,
        "dre": dre or gt_c.get("dre"),
        "bx": gt_c.get("prior_biopsy", "None"),
        "cspca": radio_fields.get("cspca") or gt_c.get("cspca_prob"),
        # 医疗历史
        "medhx": gt_c.get("med_history", "Not reported"),
        "meds": gt_c.get("medication", "None"),
        "notes": gt_c.get("notes", ""),
        "pmhx": [],
        "allergies": [],
        "vitals": {},
        # 就诊信息
        "enc_dept": "Urology - outpatient",
        "enc_date": "",
        "enc_ref": "",
        "enc_type": "Consultation",
        "note_sections": [],
        # 社会史
        "occupation": None,
        "marital": None,
        "living": None,
        "next_of_kin": None,
        "recent_other": [],
        "alcohol": None,
        "exercise": None,
        "ipss": None,
        "admin": None,
        "mri_hospital": radio_fields.get("mri_hospital"),
        # 家族史
        "fh": fam_hx,
    }

    # task 2 额外字段
    if task == 2:
        prompt.update({
            "bx_isup": path_fields.get("bx_isup") or gt_c.get("bx_isup") or (gt_clinical or {}).get("isup_grade"),
            "bx_gl_prim": path_fields.get("bx_gl_prim"),
            "bx_gl_sec": path_fields.get("bx_gl_sec"),
            "bx_gl_tert": None,
            "bx_isup_pred": None,
            "ct": gt_c.get("clinical_stage") or gt_clinical.get("clinical_stage"),
            "cores_positive": None,
            "cores_total": None,
            "max_core_pct": None,
            "growth_pattern": None,
            "high_risk_patterns": None,
            "pni": None,
            "lvi": None,
            "tumor_location": None,
            "active_treatment_flag": 0,
        })

    # task 3 额外字段
    if task == 3:
        # task3 无 GT patient 数据，从临床文档提取
        if not prompt.get("psa") and radio_fields.get("psad") and radio_fields.get("vol"):
            # PSA = PSA density * prostate volume
            prompt["psa"] = round(radio_fields["psad"] * radio_fields["vol"], 1)
        # 从文本搜索 age
        if not prompt.get("age"):
            all_text = " ".join(filter(None, [radiology, pathology, notes_text,
                                               clinical_data.get("surgical_pathology_report", "")]))
            age_match = re.search(r"(\d+)[- ]year[- ]old", all_text, re.IGNORECASE)
            if age_match:
                prompt["age"] = int(age_match.group(1))
        prompt.update({
            "active_treatment_prior_to_surgery": None,
        })

    return prompt


def _load_gt_for_case(case_id: str, task: int) -> tuple[dict | None, dict | None]:
    """从 ground_truth 读取 patient 和 clinical_data (always-available 患者卡片数据)。

    task1/2: pathologist_response.json → records[0].patient / records[0].clinical_data
    task3: 无 pathologist_response，只有 outcome 文件 (event, months)，无 patient 数据
    """
    if task == 3:
        # task3 GT 只有 outcome (event, months)，无 patient/clinical_data
        return None, None

    gt_file = GT_BASE / f"task{task}" / case_id / "pathologist_response.json"
    if not gt_file.exists():
        return None, None
    data = json.loads(gt_file.read_text())
    records_key = "biopsy_decision" if task == 1 else "treatment_decision"
    records = data.get(records_key, [])
    if not records:
        return None, None
    r = records[0]
    return r.get("patient"), r.get("clinical_data")


def prepare_all() -> dict[str, int]:
    """准备所有 6 个官方 case 的输入数据。"""
    jobs = json.loads(PRED_FILE.read_text())
    pk_map = _pk_to_case_map()

    counts: dict[str, int] = {"total": 0, "ok": 0, "err": 0}

    for job in jobs:
        pk = job.get("pk")
        case_path = pk_map.get(pk)
        if not case_path:
            continue

        task = _detect_task(case_path)
        case_id = _case_id(case_path)
        if task not in (1, 2, 3):
            continue

        counts["total"] += 1

        try:
            # 提取临床数据 (input socket)
            clin_socket_slug = TASK_INPUT_SLUGS[task]
            clinical_data = None
            for inp in job.get("inputs", []):
                if inp.get("socket", {}).get("slug") == clin_socket_slug:
                    clinical_data = inp.get("value", {})
                    break

            if not clinical_data:
                print(f"[error] {case_id}: no clinical data in inputs")
                counts["err"] += 1
                continue

            # 从 GT 读取 always-available 患者卡片数据
            gt_patient, gt_clinical = _load_gt_for_case(case_id, task)

            # 构造 structured-prompt.json
            prompt = _build_structured_prompt(case_id, task, clinical_data, gt_patient, gt_clinical)

            # 写入
            out_dir = OUT_BASE / f"task{task}" / "agent_input_official" / case_id
            out_dir.mkdir(parents=True, exist_ok=True)

            # structured-prompt.json
            (out_dir / "structured-prompt.json").write_text(
                json.dumps(prompt, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
            )

            # clinical-data.json (MCP 工具读取)
            clin_filename = TASK_CLINICAL_FILENAMES[task]
            (out_dir / clin_filename).write_text(
                json.dumps(clinical_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            # 占位: neural-representations (agent 不用, 但保持目录完整)
            (out_dir / "prostate-modality-level-neural-representations.json").write_text(
                json.dumps({}), encoding="utf-8"
            )

            counts["ok"] += 1
            print(f"[ok] task{task}/{case_id}: psa={prompt.get('psa')} age={prompt.get('age')} pirads={prompt.get('pirads')} psad={prompt.get('psad')}")

        except Exception as exc:
            print(f"[error] {case_id}: {exc}")
            counts["err"] += 1

    print(f"\n准备完成: {counts['ok']}/{counts['total']} ok, {counts['err']} errors")
    print(f"输出: {OUT_BASE}/task{{N}}/agent_input_official/<case_id>/")
    return counts


if __name__ == "__main__":
    prepare_all()
