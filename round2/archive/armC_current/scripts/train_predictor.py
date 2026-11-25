#!/usr/bin/env python3
"""训练确定性 predictor —— 把"决策"从 LLM 中抽离，迭代从 3 小时/轮降到秒级。

背景
----
当前 baseline 每轮 prompt 优化都要全量重跑 425 个 case（~3 小时），因为决策
依赖 LLM 推理。本脚本用带标签的样本（Task1=91 / Task2=72 / Task3=75）训练
专门的预测模型：

  * Task 1 (活检决策 yes/no)     -> LogisticRegression / HistGB 二分类
  * Task 2 (AS/CS/WW/AT 四分类)  -> 同上多分类
  * Task 3 (event + months)      -> lifelines Cox 生存模型 (C-index)

特征 = structured-prompt 结构化变量 + prostate-modality-level-neural-representations
冻结向量（PCA 降维，仅在训练 fold 上拟合，避免泄漏）。

用法
----
  python scripts/train_predictor.py            # 全部任务
  python scripts/train_predictor.py --task 1   # 单任务
  python scripts/train_predictor.py --save     # 训练后保存模型到 model/predictor/
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import sys

import numpy as np
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import StratifiedKFold, KFold
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.feature_selection import VarianceThreshold

from predictor_utils import PCA_DIM, VectorReducer  # 独立模块保证 joblib 反序列化稳定

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
from chimera_agent_baseline.knn_features import (  # noqa: E402  kNN 相似检索特征（W5-T1）
    KNN_FEATURE_NAMES, CaseKnnIndex, build_records,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("train_predictor")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MODEL_DIR = ROOT / "model" / "predictor"

# 每个 task 的 ground truth 决策文件
DECISION_FILE = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}
# 每个 task 的 clinical-data 文件名（masked EHR，predictor 可经 MCP 工具等价获取）
CLINICAL_FILE = {
    1: "prostate-biopsy-decision-clinical-data.json",
    2: "prostate-treatment-decision-clinical-data.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
}
FEATURES_FILE = "prostate-modality-level-neural-representations.json"

# LLM baseline 分数（exp-006 基线，用于对比）
BASELINE = {
    1: {"acc": 0.692, "f1": 0.794, "label": "Task1 活检决策"},
    2: {"acc": 0.597, "f1": 0.408, "label": "Task2 治疗决策"},
    3: {"c_index": 0.6186, "event_acc": 0.453, "label": "Task3 复发预测"},
}

N_FOLDS = 5
# 向量特征只在 Task3 有用（Task3 临床变量极少）；Task1/2 结构化特征已足够且向量是噪音
VECTOR_TASKS = {3}

_SAVE = False  # --save 时置 True，训练后保存模型到 model/predictor/
_INCLUDE_PSEUDO = False  # --include-pseudo 时置 True，加载 data/pseudo_labels.json 扩充训练集
_INCLUDE_ALL = False  # --include-all 时置 True，额外加载共识伪标签 + 合成变体 + 灰区合成
_INCLUDE_PSEUDO_V2 = False  # --include-pseudo-v2 时置 True，加载 data/pseudo_labels_v2.json（G5 反思式伪数据）
_KNN = False  # --knn-features 时置 True：启用 case 相似检索特征（折内建索引防泄漏，W5-T1）


# ---------------------------------------------------------------------------
# 特征工程
# ---------------------------------------------------------------------------

# 数值型结构化变量（prompt 里直接可用）
NUMERIC_FEATURES = {
    1: ["psa", "age", "psad", "psav", "vol", "cspca", "months", "pirads_int"],
    2: ["psa", "age", "psad", "psav", "vol", "cspca", "months", "bx_gl_prim", "bx_gl_sec", "pirads_int"],
    3: ["psa", "age"],
}

# 分类型结构化变量 -> one-hot
CATEGORICAL_FEATURES = {
    1: ["dre", "bx"],
    2: ["dre", "bx", "ct"],
    3: [],
}

# 有序编码映射
_BX_ORDER = {"none": 0, "negative": 1, "asap/hgpin": 2, "positive": 3, None: 0}
_DRE_ORDER = {"normal": 0, "benign": 0, "nodus": 1, "negative": 0, "suspicious": 2, "abnormal": 2, "not done": 0, None: 0}

# 病灶结构化特征（从 radiology_report 自由文本抽取）
LESION_FEATURES = [
    "lesion_count", "max_lesion_size_mm", "pirads_max", "pirads_sum",
    "zone_PZ", "zone_TZ",
]

# 病理报告特征（从 pathology_report 抽取，T2/T3 关键遗漏补全）
PATHOLOGY_FEATURES = [
    "path_gleason_primary", "path_gleason_secondary", "path_isup",
    "bx_needle_count", "bx_positive_needles",
    "path_svi", "path_epe", "path_margin_pos",
]

# 每灶独立特征（从 radiology_report 抽取，不聚合）
PER_LESION_FEATURES = [
    "lesion1_size_mm", "lesion1_pirads", "lesion1_zone", "lesion1_dwi",
    "lesion2_size_mm", "lesion2_pirads", "lesion2_zone", "lesion2_dwi",
    "lesion3_size_mm", "lesion3_pirads", "lesion3_zone", "lesion3_dwi",
]

# T3 从 clinical-data 文本报告中解析的结构化特征
T3_REPORT_FEATURES = [
    "prostate_volume", "psa_density", "pirads_rad", "cspca_prob",
    "bx_gleason_primary", "bx_gleason_secondary", "bx_isup",
    "sg_gleason_primary", "sg_gleason_secondary", "sg_isup",
    "path_stage", "extraprostatic_ext", "positive_margins",
    "sv_invasion", "lv_invasion", "ln_metastasis", "cci",
]

# T3 派生特征
T3_DERIVED_FEATURES = [
    "log_psa", "psa_age_ratio", "pirads_x_psad", "gleason_sum_sg", "gleason_diff",
]

# T2 派生特征（P1.1 特征工程，2026-09-26）
# 依据：配对重复 CV（10×5=50 折，n_GT=72）逐组消融显示 grade 组是 T2 唯一正贡献特征组——
#   仅+grade  LR Acc 0.658→0.751（配对 Δ+0.094，37 胜 / 3 负）
#   仅+cores/psad/flags 均为负贡献；"全部−grade" 增益消失（Δ−0.026）
# 机制：ISUP 分级组与「主分级≥4」是阈值/非线性变换，LR 无法自行构造，而它们是
#       前列腺癌治疗决策的核心分层变量。动机：平台 dev 上 9/11 错例为 AT→AS。
T2_DERIVED_FEATURES = ["d_gleason_sum", "d_grade_group", "d_gleason_primary4"]

# 从 masked EHR (clinical-data) 提取的派生特征名（数值）
CLINICAL_FEATURES = {
    1: ["fh_yes", "psa_trend_slope", "psa_trend_last", "psa_trend_n", "free_psa_ratio"] + LESION_FEATURES,
    2: ["fh_yes", "psa_trend_slope", "psa_trend_last", "psa_trend_n", "free_psa_ratio"] + LESION_FEATURES + PATHOLOGY_FEATURES + PER_LESION_FEATURES + T2_DERIVED_FEATURES,
    3: ["fh_yes"] + LESION_FEATURES + T3_REPORT_FEATURES + PATHOLOGY_FEATURES + PER_LESION_FEATURES + T3_DERIVED_FEATURES,
}


_LESION_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*mm")
_PIRADS = re.compile(r"PI-?RADS[:\s]*(\d)", re.IGNORECASE)
_ZONE = re.compile(
    r"(transition zone|peripheral zone|TZ|PZ|central zone|CZ|"
    r"anterior fibromuscular stroma|AFS)",
    re.IGNORECASE,
)
_LESION_KW = re.compile(r"lesion|nodule|spot|suspicious area", re.IGNORECASE)


def lesion_features(radiology_report: str) -> dict[str, float]:
    """从 radiology_report 自由文本抽取病灶结构化特征。

    病变位置/大小/数量/带区/每灶 PI-RADS 全丢的根因修复——
    predictor 直读 clinical-data.json 的 radiology_report（与 get_mri_report 工具同源）。
    """
    txt = radiology_report or ""
    sizes = [float(m) for m in _LESION_NUM.findall(txt)]
    pirads_vals = [int(m) for m in _PIRADS.findall(txt)]
    zones = _ZONE.findall(txt)
    n_lesion = len(_LESION_KW.findall(txt))
    return {
        "lesion_count": float(max(n_lesion, len(sizes), len(pirads_vals)) if (sizes or pirads_vals or n_lesion) else 0),
        "max_lesion_size_mm": float(max(sizes)) if sizes else 0.0,
        "pirads_max": float(max(pirads_vals)) if pirads_vals else 0.0,
        "pirads_sum": float(sum(pirads_vals)) if pirads_vals else 0.0,
        "zone_PZ": 1.0 if any("PZ" in z.upper() or "peripheral" in z.lower() for z in zones) else 0.0,
        "zone_TZ": 1.0 if any("TZ" in z.upper() or "transition" in z.lower() for z in zones) else 0.0,
    }


# DWI 扩散受限模式
_DWI_RESTRICTED = re.compile(
    r"diffusion.*restrict|restricted diffusion|DWI.*(?:high|restrict)|low ADC|reduced ADC|high signal.*DWI",
    re.IGNORECASE,
)


def pathology_features(pathology_report: str) -> dict[str, float]:
    """从 pathology_report 抽取: Gleason 主/次、ISUP、活检针数、阳性针数、SVI、EPE、切缘。"""
    out: dict[str, float] = {}
    txt = pathology_report or ""
    if not txt:
        return out
    tl = txt.lower()
    if "missing" in tl:
        for f in PATHOLOGY_FEATURES:
            out[f] = np.nan
        return out

    # Gleason primary/secondary
    m = re.search(r"Gleason\s*(?:score\s*(?:is|of)?\s*)?(\d+)\s*\+\s*(\d+)", txt)
    if m:
        out["path_gleason_primary"] = float(m.group(1))
        out["path_gleason_secondary"] = float(m.group(2))
    else:
        out["path_gleason_primary"] = np.nan
        out["path_gleason_secondary"] = np.nan

    # ISUP grade group
    m = re.search(r"(?:ISUP\s+)?grade group\s*(\d+)", txt, re.IGNORECASE)
    out["path_isup"] = float(m.group(1)) if m else np.nan

    # Biopsy needle/session count
    biopsy_marks = re.findall(r"biopsy\s*(?:\d+|session)", tl)
    timepoint_marks = re.findall(r"timepoint\s*\d+", tl)
    session_count = max(len(biopsy_marks), len(timepoint_marks))
    if session_count == 0 and "biopsy" in tl:
        session_count = 1
    out["bx_needle_count"] = float(session_count) if session_count > 0 else np.nan

    # Positive needles
    sections = re.split(r"(?=biopsy\s*\d+|timepoint\s*\d+)", txt, flags=re.IGNORECASE)
    positive_count = sum(
        1 for s in sections if "adenocarcinoma" in s.lower() or "gleason" in s.lower()
    )
    out["bx_positive_needles"] = float(positive_count) if positive_count > 0 else np.nan

    # SVI
    if "seminal vesicle" in tl and (
        "invasion" in tl or "invaded" in tl or "located in the seminal" in tl
    ):
        out["path_svi"] = 1.0
    elif "seminal vesicle" in tl:
        out["path_svi"] = 0.0
    else:
        out["path_svi"] = np.nan

    # EPE
    if "extraprostatic extension was present" in tl or "extracapsular extension" in tl:
        out["path_epe"] = 1.0
    elif "no extraprostatic extension" in tl:
        out["path_epe"] = 0.0
    else:
        out["path_epe"] = np.nan

    # Margin status
    if "surgical margins were positive" in tl:
        out["path_margin_pos"] = 1.0
    elif "surgical margins were negative" in tl:
        out["path_margin_pos"] = 0.0
    else:
        out["path_margin_pos"] = np.nan

    return out


def lesion_features_per_lesion(radiology_report: str) -> dict[str, float]:
    """从 radiology_report 抽取每灶独立 size/zone/PI-RADS/DWI（不聚合，最多 3 灶）。"""
    txt = radiology_report or ""
    out: dict[str, float] = {}
    if not txt:
        for i in range(1, 4):
            out[f"lesion{i}_size_mm"] = 0.0
            out[f"lesion{i}_pirads"] = 0.0
            out[f"lesion{i}_zone"] = 0.0
            out[f"lesion{i}_dwi"] = 0.0
        return out

    sizes = [float(m) for m in _LESION_NUM.findall(txt)]
    pirads_vals = [int(m) for m in _PIRADS.findall(txt)]
    zones = _ZONE.findall(txt)
    dwi_restricted = bool(_DWI_RESTRICTED.search(txt))

    for i in range(3):
        idx = i + 1
        out[f"lesion{idx}_size_mm"] = sizes[i] if i < len(sizes) else 0.0
        out[f"lesion{idx}_pirads"] = float(pirads_vals[i]) if i < len(pirads_vals) else 0.0
        if i < len(zones):
            zl = zones[i].lower()
            if "peripheral" in zl or "pz" in zl:
                out[f"lesion{idx}_zone"] = 1.0
            elif "transition" in zl or "tz" in zl:
                out[f"lesion{idx}_zone"] = 2.0
            elif "central" in zl or "cz" in zl:
                out[f"lesion{idx}_zone"] = 3.0
            else:
                out[f"lesion{idx}_zone"] = 0.0
        else:
            out[f"lesion{idx}_zone"] = 0.0
        out[f"lesion{idx}_dwi"] = 1.0 if dwi_restricted else 0.0

    return out


def _parse_radiology_report(text: str) -> dict[str, float]:
    """从 radiology_report 解析前列腺体积、PSA 密度、PI-RADS、csPCa 概率。"""
    out: dict[str, float] = {}
    if not text:
        return out
    m = re.search(r"Prostate volume:\s*(\d+\.?\d*)", text, re.IGNORECASE)
    out["prostate_volume"] = float(m.group(1)) if m else np.nan
    m = re.search(r"PSA density:\s*(\d+\.?\d*)", text, re.IGNORECASE)
    out["psa_density"] = float(m.group(1)) if m else np.nan
    m = re.search(r"PI-RADS:\s*(\d+)", text, re.IGNORECASE)
    out["pirads_rad"] = float(m.group(1)) if m else np.nan
    m = re.search(r"clinically significant prostate cancer.*?:\s*(\d+\.?\d*)", text, re.IGNORECASE)
    out["cspca_prob"] = float(m.group(1)) if m else np.nan
    return out


def _parse_pathology_report(text: str) -> dict[str, float]:
    """从 pathology_report 解析活检 Gleason 和 ISUP。"""
    out: dict[str, float] = {}
    if not text:
        return out
    if "missing" in text.lower():
        out["bx_gleason_primary"] = np.nan
        out["bx_gleason_secondary"] = np.nan
        out["bx_isup"] = np.nan
        return out
    m = re.search(r"Gleason\s*(?:score\s*(?:is|of)?\s*)?(\d+)\s*\+\s*(\d+)", text)
    if m:
        out["bx_gleason_primary"] = float(m.group(1))
        out["bx_gleason_secondary"] = float(m.group(2))
    else:
        out["bx_gleason_primary"] = np.nan
        out["bx_gleason_secondary"] = np.nan
    m = re.search(r"ISUP grade group\s*(\d+)", text, re.IGNORECASE)
    out["bx_isup"] = float(m.group(1)) if m else np.nan
    return out


def _parse_surgical_pathology_report(text: str) -> dict[str, float]:
    """从 surgical_pathology_report 解析 Gleason、ISUP、分期、切缘、侵犯。"""
    out: dict[str, float] = {}
    if not text:
        return out
    tl = text.lower()
    m = re.search(r"Gleason\s*(?:score\s*(?:is|of)?\s*)?(\d+)\s*\+\s*(\d+)", text)
    if m:
        out["sg_gleason_primary"] = float(m.group(1))
        out["sg_gleason_secondary"] = float(m.group(2))
    else:
        out["sg_gleason_primary"] = np.nan
        out["sg_gleason_secondary"] = np.nan
    m = re.search(r"ISUP grade group\s*(\d+)", text, re.IGNORECASE)
    out["sg_isup"] = float(m.group(1)) if m else np.nan
    m = re.search(r"pT(\d[a-z]?)", text)
    if m:
        s = m.group(1)
        sn = int(s[0])
        out["path_stage"] = {2: 0.0, 3: (1.0 if "a" in s else 2.0), 4: 3.0}.get(sn, 0.0)
    else:
        out["path_stage"] = np.nan
    out["extraprostatic_ext"] = 1.0 if "extraprostatic extension was present" in tl else 0.0
    out["positive_margins"] = 1.0 if "surgical margins were positive" in tl else 0.0
    out["sv_invasion"] = 1.0 if "seminal vesicles were invaded" in tl else 0.0
    out["lv_invasion"] = 1.0 if "lymphovascular invasion was present" in tl else 0.0
    if "lymph node metastasis was present" in tl:
        out["ln_metastasis"] = 1.0
    elif "no lymph nodes were removed" in tl:
        out["ln_metastasis"] = np.nan
    else:
        out["ln_metastasis"] = 0.0
    return out


def _parse_previous_notes(text) -> dict[str, float]:
    """从 previous_notes 解析 CCI。"""
    out: dict[str, float] = {}
    if not text:
        return out
    if isinstance(text, list):
        text = " ".join(str(t.get("text", "")) if isinstance(t, dict) else str(t) for t in text)
    m = re.search(r"Charlson Comorbidity Index.*?(\d+)", text, re.IGNORECASE)
    out["cci"] = float(m.group(1)) if m else np.nan
    return out


def _parse_lab(lab: list) -> dict[str, float]:
    """从 laboratory_results 提取数值化指标（Free PSA ratio 等）。"""
    vals: dict[str, list[float]] = {}
    for item in lab or []:
        name = (item.get("name") or "").lower()
        raw = item.get("val") or ""
        num = _num(raw.split(" ")[0])
        if np.isfinite(num):
            vals.setdefault(name, []).append(num)
    out = {}
    if "free psa" in vals and "psa" in vals:
        f, p = vals["free psa"][-1], vals["psa"][-1]
        if p > 0:
            out["free_psa_ratio"] = f / p
    return out


def _psa_trend_features(trend: list) -> dict[str, float]:
    """psa_trend 时间序列 -> 斜率 / 最新值 / 点数。"""
    if not trend:
        return {"psa_trend_slope": 0.0, "psa_trend_last": 0.0, "psa_trend_n": 0.0}
    try:
        vals = [_num(t.get("val")) for t in trend]
        vals = [v for v in vals if np.isfinite(v)]
        n = len(vals)
        if n < 2:
            return {"psa_trend_slope": 0.0, "psa_trend_last": vals[0] if vals else 0.0, "psa_trend_n": float(n)}
        x = np.arange(n)
        slope = float(np.polyfit(x, vals, 1)[0])
        return {"psa_trend_slope": slope, "psa_trend_last": vals[-1], "psa_trend_n": float(n)}
    except Exception:
        return {"psa_trend_slope": 0.0, "psa_trend_last": 0.0, "psa_trend_n": 0.0}


def clinical_features(cd: dict, task: int) -> dict[str, float]:
    """从 masked EHR 提取数值特征。predictor 运行时可等价通过 MCP 工具获取。"""
    out: dict[str, float] = {}
    fh = str(cd.get("family_history") or "").strip().lower()
    out["fh_yes"] = 1.0 if fh in ("yes", "true") else 0.0
    out.update(_psa_trend_features(cd.get("psa_trend") or []))
    out.update(_parse_lab(cd.get("laboratory_results") or []))
    out.update(lesion_features(cd.get("radiology_report") or ""))
    out.update(lesion_features_per_lesion(cd.get("radiology_report") or ""))

    if task >= 2:
        out.update(pathology_features(cd.get("pathology_report") or ""))

    if task == 3:
        out.update(_parse_radiology_report(cd.get("radiology_report") or ""))
        out.update(_parse_pathology_report(cd.get("pathology_report") or ""))
        out.update(_parse_surgical_pathology_report(cd.get("surgical_pathology_report") or ""))
        out.update(_parse_previous_notes(cd.get("previous_notes") or ""))

    return out


def _task3_extra(p: dict) -> dict:
    dre = str(p.get("dre") or "")
    extra = {"dre_tstage": 0}
    if "t3" in dre.lower() or "t4" in dre.lower():
        extra["dre_tstage"] = 1
    at = p.get("active_treatment_prior_to_surgery")
    extra["active_treatment"] = 1 if (at and str(at).strip() and str(at).lower() != "none") else 0
    return extra


def _num(v):
    """宽容的数值转换，None/空 -> nan"""
    if v is None or v == "" or v == "None":
        return np.nan
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def _pool_slices(arr: np.ndarray) -> np.ndarray:
    """per-slide 池化：mean + max + std 拼接，替代单一 mean-pool。"""
    return np.concatenate([arr.mean(0), arr.max(0), arr.std(0)])


def build_vector_features(features: dict, task: int) -> np.ndarray | None:
    """把冻结向量拼接/池化为一个 1D 向量；无向量返回 None。

    每个模态的 per-slide 信息用 mean+max+std 拼接保留（低成本增强），
    替代原先的 mean-pool（per-slide 信息被平均掉）。
    """
    vecs = []
    if task in (1, 2, 3):
        mri = features.get("MRI image") or []
        if mri:
            vecs.append(_pool_slices(np.asarray(mri, dtype=float)))
    if task in (2, 3):
        bx = features.get("Biopsy slide") or []
        if bx:
            vecs.append(_pool_slices(np.asarray(bx, dtype=float)))
    if task == 3:
        surg = features.get("Prostatectomy slide") or []
        if surg:
            vecs.append(_pool_slices(np.asarray(surg, dtype=float)))
    if not vecs:
        return None
    # 拼接所有 origin 的池化向量；维度不齐时 pad 到最大维
    max_dim = max(v.shape[0] for v in vecs)
    padded = [np.pad(v, (0, max_dim - v.shape[0])) for v in vecs]
    return np.concatenate(padded)


def _pirads_int(v) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return np.nan


def build_case_features(case_dir: Path, task: int) -> tuple[dict, np.ndarray | None]:
    """构建单个 case 的特征（训练/推理共用，保证一致性）。"""
    prompt = json.loads((case_dir / "structured-prompt.json").read_text())

    row = {}
    for f in NUMERIC_FEATURES[task]:
        if f == "pirads_int":
            row[f] = _pirads_int(prompt.get("pirads"))  # prompt 字段名是 pirads
        else:
            row[f] = _num(prompt.get(f))
    for f in CATEGORICAL_FEATURES[task]:
        row[f] = prompt.get(f)

    cd_file = case_dir / CLINICAL_FILE[task]
    clin = {}
    if cd_file.exists():
        clin = clinical_features(json.loads(cd_file.read_text()), task)
    for f in CLINICAL_FEATURES[task]:
        row[f] = clin.get(f, 0.0)

    if task == 2:
        # T2 派生特征（P1.1）：ISUP 分级组 / Gleason 和 / 主分级≥4 阈值标志。
        # 与推理侧 predictor.build_case_features 必须逐字一致。
        gp = row.get("bx_gl_prim", np.nan)
        gs = row.get("bx_gl_sec", np.nan)
        if np.isfinite(gp) and np.isfinite(gs):
            row["d_gleason_sum"] = gp + gs
            row["d_grade_group"] = float(int(np.clip(gp - 3, 0, 2) + np.clip(gs - 3, 0, 2)))
            row["d_gleason_primary4"] = 1.0 if gp >= 4 else 0.0

    if task == 3:
        row.update(_task3_extra(prompt))
        # 派生特征 (P1-F 特征工程)
        psa = row.get("psa", 0) or 0
        age = max(row.get("age", 1) or 1, 1)
        psad = row.get("psa_density", np.nan)
        pirads = row.get("pirads_rad", np.nan)
        sg_prim = row.get("sg_gleason_primary", np.nan)
        sg_sec = row.get("sg_gleason_secondary", np.nan)
        bx_prim = row.get("bx_gleason_primary", np.nan)
        bx_sec = row.get("bx_gleason_secondary", np.nan)
        row["log_psa"] = np.log1p(psa)
        row["psa_age_ratio"] = psa / age
        row["pirads_x_psad"] = pirads * psad if np.isfinite(pirads) and np.isfinite(psad) else np.nan
        row["gleason_sum_sg"] = sg_prim + sg_sec if np.isfinite(sg_prim) and np.isfinite(sg_sec) else np.nan
        row["gleason_diff"] = (
            (sg_prim + sg_sec) - (bx_prim + bx_sec)
            if np.isfinite(sg_prim) and np.isfinite(sg_sec) and np.isfinite(bx_prim) and np.isfinite(bx_sec)
            else np.nan
        )

    feat_file = case_dir / FEATURES_FILE
    v = None
    if feat_file.exists():
        v = build_vector_features(json.loads(feat_file.read_text()), task)
    return row, v


def build_prompt_features(task: int, prompt: dict) -> tuple[dict, None]:
    """从纯结构化 prompt（合成 case 无真实 case_dir / clinical-data）直接构造特征。

    只复用 build_case_features 的 structured-prompt 部分；clinical/向量特征缺失置 NaN，
    由 fold 内 impute 处理（合成数据仅进训练折，不进评估池）。
    """
    row = {}
    for f in NUMERIC_FEATURES[task]:
        if f == "pirads_int":
            row[f] = _pirads_int(prompt.get("pirads"))
        else:
            row[f] = _num(prompt.get(f))
    for f in CATEGORICAL_FEATURES[task]:
        row[f] = prompt.get(f)
    for f in CLINICAL_FEATURES[task]:
        row[f] = np.nan

    if task == 3:
        row.update(_task3_extra(prompt))
        psa = row.get("psa", 0) or 0
        age = max(row.get("age", 1) or 1, 1)
        psad = row.get("psa_density", np.nan)
        pirads = row.get("pirads_rad", np.nan)
        sg_prim = row.get("sg_gleason_primary", np.nan)
        sg_sec = row.get("sg_gleason_secondary", np.nan)
        bx_prim = row.get("bx_gleason_primary", np.nan)
        bx_sec = row.get("bx_gleason_secondary", np.nan)
        row["log_psa"] = np.log1p(psa)
        row["psa_age_ratio"] = psa / age
        row["pirads_x_psad"] = pirads * psad if np.isfinite(pirads) and np.isfinite(psad) else np.nan
        row["gleason_sum_sg"] = sg_prim + sg_sec if np.isfinite(sg_prim) and np.isfinite(sg_sec) else np.nan
        row["gleason_diff"] = (
            (sg_prim + sg_sec) - (bx_prim + bx_sec)
            if all(np.isfinite(x) for x in (sg_prim, sg_sec, bx_prim, bx_sec))
            else np.nan
        )
    return row, None


def _append_case(rows, labels, vecs, times, case_ids, is_gt, seen,
                 task: int, case_id: str, row: dict, v, decision) -> bool:
    """向训练集追加一个非-GT case；按 case_id 去重（GT 优先已记账，重复跳过）。
    返回是否实际追加。task3 从 decision dict 拆出 event / months。"""
    if case_id in seen:
        return False
    seen.add(case_id)
    rows.append(row)
    vecs.append(v)
    case_ids.append(case_id)
    is_gt.append(False)
    if task == 3:
        labels.append(int(decision["event"]))
        times.append(float(decision["months_to_recurrence"]))
    else:
        labels.append(decision)
    return True


def _find_case_dir(task: int, cid: str) -> Path | None:
    """按 case_id 定位 case 目录。

    T1/T2 伪数据 case_id 在各自 task 目录；T3 伪数据（g1 口径：全部 unlabeled
    均有 T3 标签）的 case_id 可能位于 task1/task2/task3 任一目录。
    """
    dirs = [DATA / f"task{task}" / "agent_input"]
    if task == 3:
        dirs += [DATA / "task1" / "agent_input", DATA / "task2" / "agent_input"]
    for d in dirs:
        c = d / cid
        if c.exists():
            return c
    return None


def load_task_data(
    task: int, include_pseudo: bool = False, include_all: bool = False,
    include_pseudo_v2: bool = False,
) -> tuple[list[dict], list, list, list, list[str], list[bool]]:
    """返回 (rows, labels, vecs, times, case_ids, is_gt)。

    include_pseudo=True: 加载 data/pseudo_labels.json 伪标签，加入训练集。
    include_all=True:    额外加载共识伪标签(pseudo_labels_consensus.json) +
                         GT 变体(data/synthetic/gt_variants.jsonl) +
                         灰区合成(data/synthetic/t1_grayzone.jsonl, 仅 task1)。
                         全部仅进训练折，不进评估池；按 case_id 去重（GT 优先，共识优先于
                         旧单模型伪标签）。任务3 的主力伪标签仍由 pseudo_labels.json 提供
                         （共识仅有 2 条），故 include_all 必保 pseudo 分支。
    include_pseudo_v2=True: 加载 data/pseudo_labels_v2.json（G5 反思式伪数据），
                         优先级最高（v2 > 共识 > 旧 pseudo），同样仅进训练折。
    """
    task_dir = DATA / f"task{task}" / "agent_input"
    rows, labels, vecs, times = [], [], [], []
    case_ids, is_gt = [], []

    # GT 评估池：agent_input 下带决策文件的 case（is_gt=True），case_id 记入 seen
    seen: set[str] = set()
    for case_dir in sorted(task_dir.iterdir()):
        if not case_dir.is_dir():
            continue
        dec_file = case_dir / DECISION_FILE[task]
        if not dec_file.exists():
            continue
        row, v = build_case_features(case_dir, task)
        dec = json.loads(dec_file.read_text())
        rows.append(row)
        vecs.append(v)
        case_ids.append(case_dir.name)
        is_gt.append(True)
        seen.add(case_dir.name)
        if task == 3:
            labels.append(int(dec["event"]))
            times.append(float(dec["months_to_recurrence"]))
        else:
            labels.append(dec)

    # --- include_pseudo_v2: G5 反思式伪数据（优先级最高，先于共识/旧伪标签占用 case_id） ---
    if include_pseudo_v2:
        v2_file = DATA / "pseudo_labels_v2.json"
        if v2_file.exists():
            v2 = json.loads(v2_file.read_text())
            n_v2 = 0
            for item in v2.get(f"task{task}", []):
                cid = item["case_id"]
                if cid in seen:
                    continue
                case_dir = _find_case_dir(task, cid)
                if case_dir is None:
                    continue
                row, v = build_case_features(case_dir, task)
                dec = item["decision"]
                rows.append(row)
                vecs.append(v)
                case_ids.append(cid)
                seen.add(cid)
                is_gt.append(False)
                if task == 3:
                    labels.append(int(dec["event"]))
                    times.append(float(dec["months_to_recurrence"]))
                else:
                    labels.append(dec)
                n_v2 += 1
            log.info("Task %d: pseudo_v2 -> %d entries added", task, n_v2)
        else:
            log.warning("include_pseudo_v2=True but %s not found", v2_file)

    # --- include_all: 共识伪标签（优先于旧伪标签），再 GT 变体，最后灰区合成 ---
    if include_all:
        n_cons = n_var = n_gz = 0
        # 1) 多教师共识伪标签（有真实 case_dir，按 pseudo 分支方式 build_case_features）
        cons_file = DATA / "pseudo_labels_consensus.json"
        if cons_file.exists():
            cons = json.loads(cons_file.read_text())
            for item in cons.get(f"task{task}", []):
                cid = item["case_id"]
                cdir = _find_case_dir(task, cid)
                if cdir is None:
                    continue
                row, v = build_case_features(cdir, task)
                if _append_case(rows, labels, vecs, times, case_ids, is_gt, seen,
                                task, cid, row, v, item["decision"]):
                    n_cons += 1

        # 2) GT 变体（无 case_dir，直接从 jsonl 的 structured_prompt 构造特征）
        var_file = DATA / "synthetic" / "gt_variants.jsonl"
        if var_file.exists():
            for line in var_file.open():
                d = json.loads(line)
                if d.get("task") != task:
                    continue
                row, v = build_prompt_features(task, d["structured_prompt"])
                if _append_case(rows, labels, vecs, times, case_ids, is_gt, seen,
                                task, "syn_" + d["case_id"], row, v, d["decision"]):
                    n_var += 1

        # 3) 灰区合成 T1（仅 task1，biopsy_decision 即标签）
        if task == 1:
            gz_file = DATA / "synthetic" / "t1_grayzone.jsonl"
            if gz_file.exists():
                for li, line in enumerate(gz_file.open()):
                    d = json.loads(line)
                    prompt = {k: d.get(k) for k in
                              ("psa", "age", "pirads", "psad", "vol", "dre", "bx", "cspca")}
                    row, v = build_prompt_features(task, prompt)
                    if _append_case(rows, labels, vecs, times, case_ids, is_gt, seen,
                                    task, f"syn_gz_{li}", row, v, d["biopsy_decision"]):
                        n_gz += 1
        log.info("Task %d: include_all -> consensus=%d variants=%d grayzone=%d",
                 task, n_cons, n_var, n_gz)

    # --- include_pseudo: 旧单模型伪标签（include_all 时共识已优先占用重叠 case_id，这里自动去重） ---
    if include_pseudo:
        pseudo_file = DATA / "pseudo_labels.json"
        if pseudo_file.exists():
            pseudo = json.loads(pseudo_file.read_text())
            for item in pseudo.get(f"task{task}", []):
                cid = item["case_id"]
                if cid in seen:
                    continue
                case_dir = _find_case_dir(task, cid)
                if case_dir is None:
                    continue
                row, v = build_case_features(case_dir, task)
                dec = item["decision"]
                rows.append(row)
                vecs.append(v)
                case_ids.append(cid)
                seen.add(cid)
                is_gt.append(False)
                if task == 3:
                    labels.append(int(dec["event"]))
                    times.append(float(dec["months_to_recurrence"]))
                else:
                    labels.append(dec)
            n_pseudo = sum(1 for g in is_gt if not g)
            log.info("Task %d: %d GT + %d non-GT = %d total",
                     task, sum(is_gt), n_pseudo, len(rows))
        else:
            log.warning("include_pseudo=True but %s not found", pseudo_file)

    return rows, labels, vecs, times, case_ids, is_gt


# ---------------------------------------------------------------------------
# kNN 相似检索特征（W5-T1）——折内建索引，防泄漏
# ---------------------------------------------------------------------------


def _build_knn_ctx(task, rows, case_ids, is_gt, labels=None, times=None):
    """构建 kNN 查询上下文：records 按 case_id 索引；仅 GT 带 outcome（可入索引）。"""
    if not _KNN:
        return None
    return {
        "case_ids": list(case_ids),
        "is_gt": list(is_gt),
        "records": build_records(task, rows, case_ids, is_gt, labels, times),
    }


def _knn_fold_arrays(task, knn_ctx, tr, te):
    """折内建索引（仅训练折 GT，评估 case 不入库）-> (knn_tr, knn_te) 特征矩阵。

    防泄漏红线：索引成员 = 训练折 GT；评估 case 的 kNN 特征由该折内索引生成；
    训练折内 GT 查询时排除自身（exclude_id）；伪标签 case 永不入索引。
    """
    recs, ids, is_gt = knn_ctx["records"], knn_ctx["case_ids"], knn_ctx["is_gt"]
    index = CaseKnnIndex(task, [recs[ids[i]] for i in tr if is_gt[i]])
    names = KNN_FEATURE_NAMES[task]

    def _mat(idxs):
        return np.array([
            [index.query(recs[ids[i]]["sim_vec"], exclude_id=ids[i])[n] for n in names]
            for i in idxs
        ], dtype=float)

    return _mat(tr), _mat(te)


def _knn_full_index(task, knn_ctx):
    """部署口径（仅 --save）：全量 GT 索引，全部行查询并自排除。返回 (matrix, index)。

    线上推理无泄漏：查询 case（unlabeled）不在索引；索引随 joblib 下发，
    由 predictor.Predictor.predict 现算查询。
    """
    recs, ids, is_gt = knn_ctx["records"], knn_ctx["case_ids"], knn_ctx["is_gt"]
    index = CaseKnnIndex(task, [recs[ids[i]] for i in range(len(ids)) if is_gt[i]])
    names = KNN_FEATURE_NAMES[task]
    mat = np.array([
        [index.query(recs[ids[i]]["sim_vec"], exclude_id=ids[i])[n] for n in names]
        for i in range(len(ids))
    ], dtype=float)
    return mat, index


# ---------------------------------------------------------------------------
# 训练与评估
# ---------------------------------------------------------------------------

# P0（2026-09-28）：来源分组 / 防跨折泄漏
# ---------------------------------------------------------------------------


def _row_groups(task: int, case_ids: list[str]) -> "np.ndarray":
    """每行的「来源分组键」，用于把父子/同源行锁在同一折。

    - GT / 共识伪标 / 旧伪标：分组键 = 自身 case_id（无派生关系）
    - **GT 变体**（`syn_<variant_case_id>`）：分组键 = `original_case_id` ← 必须与父同折
    - **灰区合成**（`syn_gz_*`，凭空合成、无来源）：返回 `__nogroup__:` 前缀 →
      调用方会将其排除（按项目纪律「答不出分折键 → 不许进训练集」）
    """
    var_map: dict[str, str] = {}
    vf = DATA / "synthetic" / "gt_variants.jsonl"
    if vf.exists():
        for line in vf.open():
            d = json.loads(line)
            if d.get("task") != task:
                continue
            key = "syn_" + d["case_id"]
            var_map[key] = d.get("original_case_id") or key
    nogroup = set()
    if task == 1:
        gf = DATA / "synthetic" / "t1_grayzone.jsonl"
        if gf.exists():
            for li, _ in enumerate(gf.open()):
                nogroup.add(f"syn_gz_{li}")
    out = []
    for cid in case_ids:
        if cid in var_map:
            out.append(var_map[cid])
        elif cid in nogroup:
            out.append(f"__nogroup__:{cid}")
        else:
            out.append(cid)
    return np.array(out, dtype=object)


def _train_fold_with_extras(tr_gt, pseudo_idx, groups, te):
    """训练折 = GT 训练折 ∪ {来源分组 ∉ 评估折的扩充行}。

    修掉旧写法 `concat(gt_train, pseudo_idx)`：它把**全部**变体塞进每个训练折，
    当变体的父 case 落在评估折时即造成标签泄漏。
    """
    tr = tr_gt
    if pseudo_idx is None or len(pseudo_idx) == 0:
        return tr
    te_groups = set(groups[te].tolist())
    keep = [int(i) for i in pseudo_idx
            if not str(groups[i]).startswith("__nogroup__") and str(groups[i]) not in te_groups]
    n_drop = len(pseudo_idx) - len(keep)
    if n_drop:
        log.info("fold: 扩充行 %d → 保留 %d（剔除 %d：来源分组跨折或无来源键）",
                 len(pseudo_idx), len(keep), n_drop)
    if keep:
        tr = np.concatenate([tr, np.array(keep, dtype=int)])
    return tr


def run_task1_2(task: int) -> None:
    rows, labels, vecs, _, case_ids, is_gt = load_task_data(
        task, include_pseudo=_INCLUDE_PSEUDO, include_all=_INCLUDE_ALL,
        include_pseudo_v2=_INCLUDE_PSEUDO_V2)
    y = np.array(labels)
    log.info("Task %d: %d labeled cases, labels=%s", task, len(y), {c: int((y == c).sum()) for c in set(y)})

    knn_ctx = _build_knn_ctx(task, rows, case_ids, is_gt, labels)
    knn_names = KNN_FEATURE_NAMES[task] if knn_ctx is not None else []

    # 修复 1: 预处理移入 fold 循环，防止测试折统计量泄漏
    num_names = NUMERIC_FEATURES[task] + CLINICAL_FEATURES[task]
    raw_num = np.array([[r.get(n, np.nan) for n in num_names] for r in rows], dtype=float)
    cat_names = CATEGORICAL_FEATURES[task]
    has_vecs = task in VECTOR_TASKS and any(v is not None for v in vecs)

    label_names = sorted(set(y))
    y_idx = np.array([label_names.index(v) for v in y])

    # --- honest CV -----------------------------------------------------------
    # Pseudo rows may enter TRAIN folds only; evaluation stays GT-only.
    #
    # Bug fixed 2026-09-14: `y`/`y_idx` include the pseudo rows appended by
    # load_task_data(), and `skf.split(raw_num, y_idx)` therefore placed
    # pseudo-labeled (LLM-generated) rows into the EVALUATION folds as well --
    # `is_gt` was never consulted in this loop.  That inflated every
    # --include-* number, and the tell was the "多数类基线" moving from 0.431 to
    # 0.553 purely because the *evaluation set* had changed.  Any conclusion of
    # the form "pseudo data lifts CV" was an artifact of this, not a result.
    is_gt_arr = np.array(is_gt, dtype=bool)
    gt_idx = np.where(is_gt_arr)[0]
    pseudo_idx = np.where(~is_gt_arr)[0]
    # P0（2026-09-28）：来源分组键（GT 变体 → original_case_id），防父子跨折
    groups = _row_groups(task, case_ids)
    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=42)
    clfs = {
        "LR": LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced"),
        "MLP": MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-3, max_iter=500, random_state=42),
        "HistGB": HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, random_state=42),
        # P1.4 (2026-09-26)：浅层随机森林。n=72 小样本 + 混合型临床特征下，
        # 模型族选择的影响远大于特征工程：50 折配对实测
        #   LR C=0.5           Acc 0.762（参照）
        #   RF md=4 nobal      Acc 0.853（配对 Δ+0.091，40 胜 / 0 负）
        #   ExtraTrees md=4    Acc 0.839（独立佐证，非单一模型偶然）
        # max_depth 3/4/5/6/None 分别为 0.849/0.844/0.841/0.841/0.841 —— 浅即最优；
        # class_weight=None 优于 balanced（Acc 0.853 vs 0.844，F1 0.783 vs 0.747）。
        "RF": RandomForestClassifier(
            n_estimators=400, max_depth=4, min_samples_leaf=3,
            class_weight=None, random_state=42, n_jobs=-1,
        ),
    }
    # 选头准则（P1.2，2026-09-26）
    # Task2 的 gate 决策由 predictor 直接给出（configs: decision_override_tasks=[2,3]），
    # 决策错 -> 整例 case_score 归零，故目标函数是 **accuracy**，不是 Macro-F1。
    # 以 Macro-F1 选头会选中 Acc 更低但少数类 F1 更高的头（实测 MLP 0.735 vs LR 0.750）。
    # Task1 的 predictor 只作 prompt 内的临床参考（T1 gate 仍是纯 LLM），无此约束，沿用 Macro-F1。
    select_by_acc = (task == 2)
    crit = "Acc" if select_by_acc else "Macro-F1"
    print(f"\n{'='*64}\nTask {task} — {BASELINE[task]['label']}  (基线: Acc={BASELINE[task]['acc']:.3f}, F1={BASELINE[task]['f1']:.3f})")
    print(f"选头准则: {crit}{'  ← gate 决策目标' if select_by_acc else ''}\n{'='*64}")
    best_name, best_score, best_f1 = None, -1.0, -1.0
    for name, clf in clfs.items():
        accs, f1s = [], []
        for tr_gt, te_gt in skf.split(raw_num[gt_idx], y_idx[gt_idx]):
            te = gt_idx[te_gt]
            # P0（2026-09-28）：只并入「来源分组不在评估折」的扩充行（旧写法无差别全并入）
            tr = _train_fold_with_extras(gt_idx[tr_gt], pseudo_idx, groups, te)
            # 每折单独 fit 预处理（仅训练折），避免测试折统计量泄漏
            imp = SimpleImputer(strategy="median")
            num_tr = imp.fit_transform(raw_num[tr])
            num_te = imp.transform(raw_num[te])
            if knn_ctx is not None:
                knn_tr, knn_te = _knn_fold_arrays(task, knn_ctx, tr, te)
                num_tr = np.hstack([num_tr, knn_tr])
                num_te = np.hstack([num_te, knn_te])
            scaler = StandardScaler()
            num_tr = scaler.fit_transform(num_tr)
            num_te = scaler.transform(num_te)
            X_parts_tr, X_parts_te = [num_tr], [num_te]
            if cat_names:
                cat_encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
                cat_tr = cat_encoder.fit_transform([[rows[i].get(c) for c in cat_names] for i in tr])
                cat_te = cat_encoder.transform([[rows[i].get(c) for c in cat_names] for i in te])
                X_parts_tr.append(cat_tr)
                X_parts_te.append(cat_te)
            if has_vecs:
                reducer = VectorReducer()
                vec_tr = reducer.fit_transform([vecs[i] for i in tr])
                vec_te = reducer.transform([vecs[i] for i in te])
                X_parts_tr.append(vec_tr)
                X_parts_te.append(vec_te)
            X_tr = np.hstack(X_parts_tr)
            X_te = np.hstack(X_parts_te)
            clf.fit(X_tr, y_idx[tr])
            pred = clf.predict(X_te)
            accs.append(accuracy_score(y_idx[te], pred))
            f1s.append(f1_score(y_idx[te], pred, average="macro", zero_division=0))
        print(f"  {name:8s} CV Acc={np.mean(accs):.3f}±{np.std(accs):.3f}  Macro-F1={np.mean(f1s):.3f}±{np.std(f1s):.3f}")
        print(f"           vs 基线 Acc={BASELINE[task]['acc']:.3f}  F1={BASELINE[task]['f1']:.3f}")
        score = np.mean(accs) if select_by_acc else np.mean(f1s)
        if score > best_score:
            best_score = score
            best_f1 = np.mean(f1s)
            best_name = name

    # 多数类基线（无脑预测最频繁类）—— 只在 GT 行上算，否则加伪数据会把它一起改掉
    maj = np.bincount(y_idx[gt_idx]).argmax()
    maj_acc = (y_idx[gt_idx] == maj).mean()
    print(f"  多数类基线 Acc={maj_acc:.3f}")
    print(f"  >> Best head: {best_name} (CV {crit}={best_score:.3f}, Macro-F1={best_f1:.3f})")

    if _SAVE:
        # 部署口径：全量数据 fit 预处理（CV 是评估口径，--save 是部署口径，两者分离）
        knn_index = None
        if knn_ctx is not None:
            knn_mat, knn_index = _knn_full_index(task, knn_ctx)
            raw_num = np.hstack([raw_num, knn_mat])
        imp = SimpleImputer(strategy="median")
        num_mat = imp.fit_transform(raw_num)
        scaler = StandardScaler()
        num_mat = scaler.fit_transform(num_mat)
        cat_encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
        X_parts = [num_mat]
        if cat_names:
            cat_mat = cat_encoder.fit_transform([[r.get(c) for c in cat_names] for r in rows])
            X_parts.append(cat_mat)
        reducer = None
        if has_vecs:
            reducer = VectorReducer()
            X_parts.append(reducer.fit_transform(vecs))
        X = np.hstack(X_parts)
        log.info("Task %d: feature matrix %s", task, X.shape)
        # 用 CV 选出的最佳头部训练全量模型
        best_clf = clfs[best_name]
        log.info("Task %d: saving best head '%s' (CV %s=%.3f, Macro-F1=%.3f)",
                 task, best_name, crit, best_score, best_f1)
        best_clf.fit(X, y_idx)
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        import joblib

        joblib.dump(
            {
                "kind": "classifier",
                "task": task,
                "num_names": num_names + knn_names,
                "cat_names": cat_names,
                "imp": imp,
                "scaler": scaler,
                "cat_encoder": cat_encoder if cat_names else None,
                "reducer": reducer,
                "clf": best_clf,
                "clf_name": best_name,
                "label_names": label_names,
                "knn_index": knn_index,
            },
            MODEL_DIR / f"predictor_task{task}.joblib",
        )
        log.info("Saved model -> %s", MODEL_DIR / f"predictor_task{task}.joblib")


def _c_index(time: np.ndarray, risk: np.ndarray, event: np.ndarray) -> float:
    """Harrell C-index：风险分越大 -> 事件越早。只数可比较对。"""
    n = len(time)
    conc, perm = 0, 0
    for i in range(n):
        if event[i] != 1:
            continue
        for j in range(n):
            if i == j:
                continue
            if time[i] < time[j] or (time[i] == time[j] and event[j] == 1):
                perm += 1
                if risk[i] > risk[j]:
                    conc += 1
    return conc / perm if perm else 0.5


def _preprocess_fold_t3(raw_num, tr, te, vecs, has_vecs, knn_tr=None, knn_te=None):
    """每折单独 fit 预处理（仅训练折），返回 X_tr, X_te。

    流程：impute → [hstack kNN 特征（无缺失，W5-T1）] → VarianceThreshold（去零方差列）→ scale。
    VarianceThreshold 在训练折上 fit，丢弃全零/零方差特征（如 fh_yes 全为0），
    避免 Cox 优化器因奇异 Hessian 而产生 NaN。
    """
    imp = SimpleImputer(strategy="median")
    num_tr = imp.fit_transform(raw_num[tr])
    num_te = imp.transform(raw_num[te])
    if knn_tr is not None:
        num_tr = np.hstack([num_tr, knn_tr])
        num_te = np.hstack([num_te, knn_te])
    vt = VarianceThreshold(threshold=0.0)
    num_tr = vt.fit_transform(num_tr)
    num_te = vt.transform(num_te)
    scaler = StandardScaler()
    num_tr = scaler.fit_transform(num_tr)
    num_te = scaler.transform(num_te)
    X_parts_tr, X_parts_te = [num_tr], [num_te]
    if has_vecs:
        reducer = VectorReducer()
        vec_tr = reducer.fit_transform([vecs[i] for i in tr])
        vec_te = reducer.transform([vecs[i] for i in te])
        X_parts_tr.append(vec_tr)
        X_parts_te.append(vec_te)
    return np.hstack(X_parts_tr), np.hstack(X_parts_te)


def _preprocess_full_t3(raw_num, vecs, has_vecs):
    """全量数据 fit 预处理（部署口径），返回 X, imp, scaler, reducer, vt。"""
    imp = SimpleImputer(strategy="median")
    num_mat = imp.fit_transform(raw_num)
    vt = VarianceThreshold(threshold=0.0)
    num_mat = vt.fit_transform(num_mat)
    scaler = StandardScaler()
    num_mat = scaler.fit_transform(num_mat)
    reducer = None
    X_parts = [num_mat]
    if has_vecs:
        reducer = VectorReducer()
        X_parts.append(reducer.fit_transform(vecs))
    return np.hstack(X_parts), imp, scaler, reducer, vt


def run_task3() -> None:
    """Task3 用 lifelines Cox 生存模型（修复 3）+ 预处理移入 fold（修复 1）。

    Cox 同时建模 event+time，预期 C-index 0.72+。
    Cox 失败时 fallback 到 sklearn 组合（HistGB + GBM）。
    """
    rows, labels, vecs, times, case_ids, is_gt = load_task_data(
        3, include_pseudo=_INCLUDE_PSEUDO, include_all=_INCLUDE_ALL,
        include_pseudo_v2=_INCLUDE_PSEUDO_V2)
    y_event = np.array(labels)
    y_time = np.array(times)
    log.info("Task 3: %d labeled cases, events=%d/%d", len(rows), y_event.sum(), len(y_event))

    # 派生特征
    for r in rows:
        r["log_psa"] = np.log1p(r.get("psa", 0) or 0)
        r["psa_age_ratio"] = (r.get("psa", 0) or 0) / max(r.get("age", 1) or 1, 1)

    knn_ctx = _build_knn_ctx(3, rows, case_ids, is_gt, labels, times)
    knn_names = KNN_FEATURE_NAMES[3] if knn_ctx is not None else []
    knn_full_mat, knn_index_full = (None, None)

    num_names = NUMERIC_FEATURES[3] + CLINICAL_FEATURES[3] + ["dre_tstage", "active_treatment", "log_psa", "psa_age_ratio"]
    raw_num = np.array([[r.get(n, np.nan) for n in num_names] for r in rows], dtype=float)
    has_vecs = any(v is not None for v in vecs)

    kf = KFold(n_splits=N_FOLDS, shuffle=True, random_state=42)

    # P0（2026-09-28）：评估折只用 GT 行；扩充行仅进训练折且来源分组不得跨折。
    # 旧写法 `kf.split(raw_num)` 对**全行**分折 → 伪标/变体会落在评估折里。
    is_gt_arr3 = np.array(is_gt, dtype=bool)
    gt_idx3 = np.where(is_gt_arr3)[0]
    pseudo_idx3 = np.where(~is_gt_arr3)[0]
    groups3 = _row_groups(3, case_ids)

    # --- 修复 3: Cox 主路径 ---
    cox_ok = False
    try:
        import pandas as pd
        from lifelines import CoxPHFitter
        from lifelines.utils import concordance_index

        c_indices, event_accs, maes = [], [], []
        for tr_gt, te_gt in kf.split(raw_num[gt_idx3]):
            te = gt_idx3[te_gt]
            tr = _train_fold_with_extras(gt_idx3[tr_gt], pseudo_idx3, groups3, te)
            # 修复 1: 预处理移入 fold（仅训练折 fit）
            knn_tr, knn_te = (None, None)
            if knn_ctx is not None:
                knn_tr, knn_te = _knn_fold_arrays(3, knn_ctx, tr, te)
            X_tr, X_te = _preprocess_fold_t3(raw_num, tr, te, vecs, has_vecs, knn_tr, knn_te)

            feat_names = [f"f{i}" for i in range(X_tr.shape[1])]
            df_tr = pd.DataFrame(X_tr, columns=feat_names)
            df_tr["duration"] = y_time[tr]
            df_tr["event"] = y_event[tr]

            cph = CoxPHFitter(penalizer=0.5)
            cph.fit(df_tr, duration_col="duration", event_col="event")

            cox_beta = np.atleast_1d(np.asarray(cph.params_))
            risk_te = X_te @ cox_beta
            # concordance_index: higher score = longer survival; negate risk
            ci = concordance_index(y_time[te], -risk_te, y_event[te])
            c_indices.append(ci)

            risk_tr = X_tr @ cox_beta
            ev1 = y_event[tr] == 1
            median_risk = float(np.median(risk_tr[ev1])) if ev1.any() else 0.0
            pred_event = (risk_te >= median_risk).astype(int)
            event_accs.append(accuracy_score(y_event[te], pred_event))

            # Months MAE: median survival time for event=1 cases
            ev_te = y_event[te] == 1
            if ev_te.any():
                med_te_val = cph.predict_median(pd.DataFrame(X_te, columns=feat_names))
                med_te = np.atleast_1d(np.asarray(med_te_val)).flatten()
                med_te = np.where(np.isinf(med_te), float(y_time.max()), med_te)
                med_te = np.clip(med_te, 1, 120)
                maes.append(np.mean(np.abs(med_te[ev_te] - y_time[te][ev_te])))

        print(f"\n{'='*64}\nTask 3 — {BASELINE[3]['label']}  (基线: C-index={BASELINE[3]['c_index']:.4f}, EventAcc={BASELINE[3]['event_acc']:.3f})\n{'='*64}")
        print(f"  Cox CV C-index={np.mean(c_indices):.4f}±{np.std(c_indices):.4f}   (vs 基线 {BASELINE[3]['c_index']:.4f})")
        print(f"  Cox CV EventAcc={np.mean(event_accs):.3f}±{np.std(event_accs):.3f}   (vs 基线 {BASELINE[3]['event_acc']:.3f})")
        if maes:
            print(f"  Cox CV MonthsMAE={np.mean(maes):.2f} 月 (event=1 子集)")
        print(f"  多数类(0) EventAcc={(1 - y_event.mean()):.3f}")
        cox_ok = True
    except Exception as exc:
        log.warning("Cox fit failed (%s), falling back to sklearn combination", exc)

    if not cox_ok:
        # Fallback: sklearn 组合（HistGB event + GBM months），预处理移入 fold（修复 1）
        c_indices, event_accs, maes = [], [], []
        for tr_gt, te_gt in kf.split(raw_num[gt_idx3]):
            te = gt_idx3[te_gt]
            tr = _train_fold_with_extras(gt_idx3[tr_gt], pseudo_idx3, groups3, te)
            knn_tr, knn_te = (None, None)
            if knn_ctx is not None:
                knn_tr, knn_te = _knn_fold_arrays(3, knn_ctx, tr, te)
            X_tr, X_te = _preprocess_fold_t3(raw_num, tr, te, vecs, has_vecs, knn_tr, knn_te)
            clf = HistGradientBoostingClassifier(max_depth=2, learning_rate=0.05, max_iter=150, random_state=42)
            clf.fit(X_tr, y_event[tr])
            proba = clf.predict_proba(X_te)[:, 1]
            risk = proba
            ci = _c_index(y_time[te], risk, y_event[te])
            c_indices.append(ci)
            thr = np.median(proba)
            pred_event = (proba > thr).astype(int)
            event_accs.append(accuracy_score(y_event[te], pred_event))
            ev_tr_mask = y_event[tr] == 1
            ev_te_mask = y_event[te] == 1
            if ev_tr_mask.sum() >= 5 and ev_te_mask.any():
                from sklearn.ensemble import GradientBoostingRegressor
                reg = GradientBoostingRegressor(
                    loss="quantile", alpha=0.5,
                    max_depth=2, learning_rate=0.05, n_estimators=150, random_state=42,
                )
                reg.fit(X_tr[ev_tr_mask], y_time[tr][ev_tr_mask])
                pred_m = reg.predict(X_te[ev_te_mask])
                maes.append(np.mean(np.abs(pred_m - y_time[te][ev_te_mask])))
        print(f"\n{'='*64}\nTask 3 — {BASELINE[3]['label']}  (基线: C-index={BASELINE[3]['c_index']:.4f}, EventAcc={BASELINE[3]['event_acc']:.3f})\n{'='*64}")
        print(f"  [fallback] CV C-index={np.mean(c_indices):.4f}±{np.std(c_indices):.4f}")
        print(f"  [fallback] CV EventAcc={np.mean(event_accs):.3f}±{np.std(event_accs):.3f}")
        if maes:
            print(f"  [fallback] CV MonthsMAE={np.mean(maes):.2f} 月")
        print(f"  多数类(0) EventAcc={(1 - y_event.mean()):.3f}")

    # --- MLP CV comparison ---
    mlp_c_indices = []
    for tr_gt, te_gt in kf.split(raw_num[gt_idx3]):
        te = gt_idx3[te_gt]
        tr = _train_fold_with_extras(gt_idx3[tr_gt], pseudo_idx3, groups3, te)
        knn_tr, knn_te = (None, None)
        if knn_ctx is not None:
            knn_tr, knn_te = _knn_fold_arrays(3, knn_ctx, tr, te)
        X_tr, X_te = _preprocess_fold_t3(raw_num, tr, te, vecs, has_vecs, knn_tr, knn_te)
        mlp = MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-3, max_iter=500, random_state=42)
        mlp.fit(X_tr, y_event[tr])
        proba = mlp.predict_proba(X_te)[:, 1]
        mlp_c_indices.append(_c_index(y_time[te], proba, y_event[te]))
    mlp_ci = float(np.mean(mlp_c_indices))
    print(f"  MLP CV C-index={mlp_ci:.4f}±{np.std(mlp_c_indices):.4f}   (vs 基线 {BASELINE[3]['c_index']:.4f})")

    # Determine best model for save
    cox_ci = float(np.mean(c_indices)) if cox_ok else 0.0
    fb_ci = float(np.mean(c_indices)) if not cox_ok and c_indices else 0.0
    best_t3 = "mlp" if mlp_ci >= max(cox_ci, fb_ci) else ("cox" if cox_ok else "histgb")
    print(f"  >> Best T3 head: {best_t3}")

    if _SAVE:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        import joblib

        if knn_ctx is not None:
            knn_full_mat, knn_index_full = _knn_full_index(3, knn_ctx)
            raw_num = np.hstack([raw_num, knn_full_mat])

        if best_t3 == "cox" and cox_ok:
            # 部署口径：全量数据 fit Cox，导出纯 numpy 系数（容器无需 lifelines）
            X, imp, scaler, reducer, vt = _preprocess_full_t3(raw_num, vecs, has_vecs)
            log.info("Task 3: feature matrix %s", X.shape)

            import pandas as pd
            from lifelines import CoxPHFitter

            feat_names = [f"f{i}" for i in range(X.shape[1])]
            df = pd.DataFrame(X, columns=feat_names)
            df["duration"] = y_time
            df["event"] = y_event

            cph = CoxPHFitter(penalizer=0.5)
            cph.fit(df, duration_col="duration", event_col="event")

            cox_beta = np.atleast_1d(np.asarray(cph.params_))
            risk_all = X @ cox_beta
            risk_ev1 = risk_all[y_event == 1]
            median_risk = float(np.median(risk_ev1)) if len(risk_ev1) > 0 else 0.0

            baseline = cph.baseline_survival_
            baseline_times = baseline.index.values
            baseline_survival = baseline.values.flatten()

            joblib.dump(
                {
                    "kind": "cox",
                    "task": 3,
                    "num_names": num_names,
                    "all_num_names": num_names + knn_names,
                    "vt": vt,
                    "cat_names": [],
                    "imp": imp,
                    "scaler": scaler,
                    "cat_encoder": None,
                    "reducer": reducer,
                    "cox_beta": cox_beta,
                    "median_risk": median_risk,
                    # P3: months 连续化所需的 tie-break 统计量（推理侧 predictor._predict_cox 使用）
                    "risk_mu": float(np.mean(risk_all)),
                    "risk_sd": float(np.std(risk_all)),
                    "baseline_survival": baseline_survival,
                    "baseline_times": baseline_times,
                    "knn_index": knn_index_full,
                },
                MODEL_DIR / "predictor_task3.joblib",
            )
            log.info("Saved Cox model -> %s", MODEL_DIR / "predictor_task3.joblib")
        else:
            # Save sklearn survival model (MLP or HistGB fallback)
            X, imp, scaler, reducer, vt = _preprocess_full_t3(raw_num, vecs, has_vecs)
            if best_t3 == "mlp":
                clf = MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-3, max_iter=500, random_state=42)
            else:
                clf = HistGradientBoostingClassifier(max_depth=2, learning_rate=0.05, max_iter=150, random_state=42)
            log.info("Task 3: saving %s as survival model", best_t3)
            clf.fit(X, y_event)
            ev = np.where(y_event == 1)[0]
            reg = None
            if len(ev) >= 10:
                from sklearn.ensemble import GradientBoostingRegressor
                reg = GradientBoostingRegressor(
                    loss="quantile", alpha=0.5,
                    max_depth=2, learning_rate=0.05, n_estimators=150, random_state=42,
                )
                reg.fit(X[ev], y_time[ev])
            joblib.dump(
                {
                    "kind": "survival",
                    "task": 3,
                    "num_names": num_names,
                    "all_num_names": num_names + knn_names,
                    "vt": vt,
                    "cat_names": [],
                    "imp": imp,
                    "scaler": scaler,
                    "cat_encoder": None,
                    "reducer": reducer,
                    "clf_event": clf,
                    "reg_months": reg,
                    "event_rate": float(y_event.mean()),
                    "knn_index": knn_index_full,
                },
                MODEL_DIR / "predictor_task3.joblib",
            )
            log.info("Saved sklearn fallback model -> %s", MODEL_DIR / "predictor_task3.joblib")


# ---------------------------------------------------------------------------
# LOOCV comparison: GT-only vs GT+pseudo
# ---------------------------------------------------------------------------

def _loocv_classification(task, rows, labels, vecs, is_gt, clf_factory=None, knn_ctx=None):
    """LOOCV for classification (T1/T2). Only GT cases are test cases.

    clf_factory: callable returning a fresh sklearn classifier instance.
                 None → LogisticRegression（兼容旧调用）。
    knn_ctx: W5-T1 kNN 相似检索特征上下文（None=不启用）。每折内建索引
             （仅训练折 GT，测试 case 不入库），训练折内 GT 查询自排除。
    """
    if clf_factory is None:
        clf_factory = lambda: LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced")
    y = np.array(labels)
    label_names = sorted(set(y))
    y_idx = np.array([label_names.index(v) for v in y])
    is_gt_arr = np.array(is_gt)
    gt_indices = np.where(is_gt_arr)[0]

    num_names = NUMERIC_FEATURES[task] + CLINICAL_FEATURES[task]
    raw_num = np.array([[r.get(n, np.nan) for n in num_names] for r in rows], dtype=float)
    cat_names = CATEGORICAL_FEATURES[task]
    has_vecs = task in VECTOR_TASKS and any(v is not None for v in vecs)

    preds, trues = [], []
    for test_idx in gt_indices:
        train_idx = np.array([i for i in range(len(y)) if i != test_idx])

        imp = SimpleImputer(strategy="median")
        num_tr = imp.fit_transform(raw_num[train_idx])
        num_te = imp.transform(raw_num[[test_idx]])
        if knn_ctx is not None:
            knn_tr, knn_te = _knn_fold_arrays(task, knn_ctx, train_idx, [test_idx])
            num_tr = np.hstack([num_tr, knn_tr])
            num_te = np.hstack([num_te, knn_te])
        scaler = StandardScaler()
        num_tr = scaler.fit_transform(num_tr)
        num_te = scaler.transform(num_te)
        X_parts_tr, X_parts_te = [num_tr], [num_te]
        if cat_names:
            cat_encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
            cat_tr = cat_encoder.fit_transform([[rows[i].get(c) for c in cat_names] for i in train_idx])
            cat_te = cat_encoder.transform([[rows[test_idx].get(c) for c in cat_names]])
            X_parts_tr.append(cat_tr)
            X_parts_te.append(cat_te)
        if has_vecs:
            reducer = VectorReducer()
            vec_tr = reducer.fit_transform([vecs[i] for i in train_idx])
            vec_te = reducer.transform([vecs[test_idx]])
            X_parts_tr.append(vec_tr)
            X_parts_te.append(vec_te)
        X_tr = np.hstack(X_parts_tr)
        X_te = np.hstack(X_parts_te)

        clf = clf_factory()
        clf.fit(X_tr, y_idx[train_idx])
        pred = clf.predict(X_te)[0]
        preds.append(pred)
        trues.append(y_idx[test_idx])

    preds = np.array(preds)
    trues = np.array(trues)
    acc = accuracy_score(trues, preds)
    f1 = f1_score(trues, preds, average="macro", zero_division=0)
    return acc, f1


def _loocv_survival(rows, labels, vecs, times, is_gt, model_type="cox", knn_ctx=None):
    """LOOCV for survival (T3). Only GT cases are test cases.

    model_type: 'cox' (lifelines), 'mlp' (MLPClassifier), or 'histgb'.
    knn_ctx: W5-T1 kNN 相似检索特征上下文（None=不启用），折内建索引防泄漏。
    """
    y_event = np.array(labels)
    y_time = np.array(times)
    is_gt_arr = np.array(is_gt)
    gt_indices = np.where(is_gt_arr)[0]

    for r in rows:
        r["log_psa"] = np.log1p(r.get("psa", 0) or 0)
        r["psa_age_ratio"] = (r.get("psa", 0) or 0) / max(r.get("age", 1) or 1, 1)

    num_names = NUMERIC_FEATURES[3] + CLINICAL_FEATURES[3] + ["dre_tstage", "active_treatment", "log_psa", "psa_age_ratio"]
    raw_num = np.array([[r.get(n, np.nan) for n in num_names] for r in rows], dtype=float)
    has_vecs = any(v is not None for v in vecs)

    risk_scores = np.zeros(len(rows))
    event_preds = np.zeros(len(rows), dtype=int)
    months_preds = np.zeros(len(rows))

    cox_ok = False
    if model_type == "cox":
        try:
            import pandas as pd
            from lifelines import CoxPHFitter
            from lifelines.utils import concordance_index

            for test_idx in gt_indices:
                train_idx = np.array([i for i in range(len(rows)) if i != test_idx])
                knn_tr, knn_te = (None, None)
                if knn_ctx is not None:
                    knn_tr, knn_te = _knn_fold_arrays(3, knn_ctx, train_idx, [test_idx])
                X_tr, X_te = _preprocess_fold_t3(raw_num, train_idx, np.array([test_idx]), vecs, has_vecs, knn_tr, knn_te)

                feat_names = [f"f{i}" for i in range(X_tr.shape[1])]
                df_tr = pd.DataFrame(X_tr, columns=feat_names)
                df_tr["duration"] = y_time[train_idx]
                df_tr["event"] = y_event[train_idx]

                cph = CoxPHFitter(penalizer=0.5)
                cph.fit(df_tr, duration_col="duration", event_col="event")

                cox_beta = np.atleast_1d(np.asarray(cph.params_))
                risk_scores[test_idx] = (X_te @ cox_beta)[0]

                risk_tr = X_tr @ cox_beta
                ev1 = y_event[train_idx] == 1
                median_risk = float(np.median(risk_tr[ev1])) if ev1.any() else 0.0
                event_preds[test_idx] = int(risk_scores[test_idx] >= median_risk)

                if y_event[test_idx] == 1:
                    med_val = cph.predict_median(pd.DataFrame(X_te, columns=feat_names))
                    med = float(np.atleast_1d(np.asarray(med_val))[0])
                    if np.isinf(med):
                        med = float(y_time.max())
                    months_preds[test_idx] = float(np.clip(med, 1, 120))

            cox_ok = True
            ci = concordance_index(y_time[gt_indices], -risk_scores[gt_indices], y_event[gt_indices])
            ev_acc = accuracy_score(y_event[gt_indices], event_preds[gt_indices])
            ev_gt = y_event[gt_indices] == 1
            mae = float(np.mean(np.abs(months_preds[gt_indices][ev_gt] - y_time[gt_indices][ev_gt]))) if ev_gt.any() else float('nan')
            return ci, ev_acc, mae, cox_ok
        except Exception as exc:
            log.warning("LOOCV Cox failed (%s), using sklearn fallback", exc)

    # sklearn fallback (also used directly when model_type != 'cox')
    if model_type == "mlp":
        clf_factory = lambda: MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-3, max_iter=500, random_state=42)
    else:
        clf_factory = lambda: HistGradientBoostingClassifier(max_depth=2, learning_rate=0.05, max_iter=150, random_state=42)

    for test_idx in gt_indices:
        train_idx = np.array([i for i in range(len(rows)) if i != test_idx])
        knn_tr, knn_te = (None, None)
        if knn_ctx is not None:
            knn_tr, knn_te = _knn_fold_arrays(3, knn_ctx, train_idx, [test_idx])
        X_tr, X_te = _preprocess_fold_t3(raw_num, train_idx, np.array([test_idx]), vecs, has_vecs, knn_tr, knn_te)
        clf = clf_factory()
        clf.fit(X_tr, y_event[train_idx])
        proba = clf.predict_proba(X_te)[0, 1]
        risk_scores[test_idx] = proba
        thr = float(np.median(clf.predict_proba(X_tr)[:, 1]))
        event_preds[test_idx] = int(proba > thr)

    ci = _c_index(y_time[gt_indices], risk_scores[gt_indices], y_event[gt_indices])
    ev_acc = accuracy_score(y_event[gt_indices], event_preds[gt_indices])
    return ci, ev_acc, float('nan'), False


def run_loocv_comparison(task):
    """LOOCV 对比：GT-only vs GT+pseudo（v2 模式下三列：GT-only | GT+旧伪 | GT+v2）。

    伪标签 case 加入训练，不进评估池。
    """
    rows_gt, labels_gt, vecs_gt, times_gt, ids_gt, isgt_gt = load_task_data(task, include_pseudo=False)
    knn_ctx_gt = _build_knn_ctx(task, rows_gt, ids_gt, isgt_gt, labels_gt, times_gt)
    knn_tag = "+kNN" if _KNN else ""

    if _INCLUDE_PSEUDO_V2:
        # 三列对比：GT-only | GT+旧伪数据(pseudo_labels.json) | GT+v2(pseudo_labels_v2.json)
        rows_old, labels_old, vecs_old, times_old, ids_old, is_gt_old = load_task_data(
            task, include_pseudo=True)
        rows_v2, labels_v2, vecs_v2, times_v2, ids_v2, is_gt_v2 = load_task_data(
            task, include_pseudo_v2=True)
        knn_ctx_old = _build_knn_ctx(task, rows_old, ids_old, is_gt_old, labels_old, times_old)
        knn_ctx_v2 = _build_knn_ctx(task, rows_v2, ids_v2, is_gt_v2, labels_v2, times_v2)
        n_old = len(rows_old) - len(rows_gt)
        n_v2 = len(rows_v2) - len(rows_gt)
        print(f"\n{'='*72}")
        print(f"Task {task} — LOOCV 3-way{knn_tag}  (GT={len(rows_gt)}, +old={n_old}, +v2={n_v2})")
        print(f"{'='*72}")

        if task == 3:
            ci_b, ev_b, mae_b, _ = _loocv_survival(
                [r.copy() for r in rows_gt], labels_gt, vecs_gt, times_gt, [True]*len(rows_gt),
                knn_ctx=knn_ctx_gt)
            ci_o, ev_o, mae_o, _ = _loocv_survival(
                [r.copy() for r in rows_old], labels_old, vecs_old, times_old, is_gt_old,
                knn_ctx=knn_ctx_old)
            ci_v, ev_v, mae_v, _ = _loocv_survival(
                [r.copy() for r in rows_v2], labels_v2, vecs_v2, times_v2, is_gt_v2,
                knn_ctx=knn_ctx_v2)
            print(f"  {'Metric':<20s} {'GT-only':>12s} {'GT+old':>12s} {'GT+v2':>12s}")
            print(f"  {'-'*58}")
            print(f"  {'C-index':<20s} {ci_b:>12.4f} {ci_o:>12.4f} {ci_v:>12.4f}")
            print(f"  {'Event Acc':<20s} {ev_b:>12.3f} {ev_o:>12.3f} {ev_v:>12.3f}")
            if not np.isnan(mae_b) and not np.isnan(mae_v):
                print(f"  {'Months MAE':<20s} {mae_b:>12.2f} {mae_o:>12.2f} {mae_v:>12.2f}")
            adopted = ci_v >= ci_b
            verdict = "ADOPT" if adopted else "REJECT"
            reason = "v2 C-index improved/maintained vs GT-only" if adopted else "v2 C-index degraded"
        else:
            acc_b, f1_b = _loocv_classification(
                task, [r.copy() for r in rows_gt], labels_gt, vecs_gt, [True]*len(rows_gt),
                knn_ctx=knn_ctx_gt)
            acc_o, f1_o = _loocv_classification(
                task, [r.copy() for r in rows_old], labels_old, vecs_old, is_gt_old,
                knn_ctx=knn_ctx_old)
            acc_v, f1_v = _loocv_classification(
                task, [r.copy() for r in rows_v2], labels_v2, vecs_v2, is_gt_v2,
                knn_ctx=knn_ctx_v2)
            print(f"  {'Metric':<20s} {'GT-only':>12s} {'GT+old':>12s} {'GT+v2':>12s}")
            print(f"  {'-'*58}")
            print(f"  {'Accuracy':<20s} {acc_b:>12.3f} {acc_o:>12.3f} {acc_v:>12.3f}")
            print(f"  {'Macro-F1':<20s} {f1_b:>12.3f} {f1_o:>12.3f} {f1_v:>12.3f}")
            adopted = acc_v >= acc_b and f1_v >= f1_b
            verdict = "ADOPT" if adopted else "REJECT"
            reason = ("v2 Acc & F1 improved/maintained vs GT-only" if adopted
                      else "v2 Acc or F1 degraded")

        print(f"\n  Decision: {verdict} ({reason})")
        print(f"{'='*72}")
        return adopted

    rows_all, labels_all, vecs_all, times_all, ids_all, is_gt_all = load_task_data(
        task, include_pseudo=_INCLUDE_PSEUDO, include_all=_INCLUDE_ALL)
    knn_ctx_all = _build_knn_ctx(task, rows_all, ids_all, is_gt_all, labels_all, times_all)

    n_gt = len(rows_gt)
    n_pseudo = len(rows_all) - n_gt

    mode_label = ("GT+All" if _INCLUDE_ALL else "GT+Pseudo") + knn_tag
    print(f"\n{'='*64}")
    print(f"Task {task} — LOOCV Comparison  (GT={n_gt}, {mode_label} extra={n_pseudo})")
    print(f"{'='*64}")

    if task == 3:
        ci_b, ev_b, mae_b, cox_b = _loocv_survival(
            [r.copy() for r in rows_gt], labels_gt, vecs_gt, times_gt, [True]*n_gt,
            knn_ctx=knn_ctx_gt)
        ci_a, ev_a, mae_a, cox_a = _loocv_survival(
            [r.copy() for r in rows_all], labels_all, vecs_all, times_all, is_gt_all,
            knn_ctx=knn_ctx_all)

        print(f"  {'Metric':<20s} {'GT-only':>12s} {'GT+Pseudo':>12s} {'Delta':>10s}")
        print(f"  {'-'*54}")
        print(f"  {'C-index':<20s} {ci_b:>12.4f} {ci_a:>12.4f} {ci_a-ci_b:>+10.4f}")
        print(f"  {'Event Acc':<20s} {ev_b:>12.3f} {ev_a:>12.3f} {ev_a-ev_b:>+10.3f}")
        if not np.isnan(mae_b) and not np.isnan(mae_a):
            print(f"  {'Months MAE':<20s} {mae_b:>12.2f} {mae_a:>12.2f} {mae_a-mae_b:>+10.2f}")
        adopted = ci_a >= ci_b
        verdict = "ADOPT" if adopted else "REJECT"
        reason = "C-index improved/maintained" if adopted else "C-index degraded"
    else:
        acc_b, f1_b = _loocv_classification(
            task, [r.copy() for r in rows_gt], labels_gt, vecs_gt, [True]*n_gt,
            knn_ctx=knn_ctx_gt)
        acc_a, f1_a = _loocv_classification(
            task, [r.copy() for r in rows_all], labels_all, vecs_all, is_gt_all,
            knn_ctx=knn_ctx_all)

        print(f"  {'Metric':<20s} {'GT-only':>12s} {mode_label:>12s} {'Delta':>10s}")
        print(f"  {'-'*54}")
        print(f"  {'Accuracy':<20s} {acc_b:>12.3f} {acc_a:>12.3f} {acc_a-acc_b:>+10.3f}")
        print(f"  {'Macro-F1':<20s} {f1_b:>12.3f} {f1_a:>12.3f} {f1_a-f1_b:>+10.3f}")
        adopted = acc_a >= acc_b and f1_a >= f1_b
        verdict = "ADOPT" if adopted else "REJECT"
        reason = "Acc & F1 improved/maintained" if adopted else "Acc or F1 degraded"

    print(f"\n  Decision: {verdict} ({reason})")
    print(f"{'='*64}")
    return adopted


def run_loocv_retest(task: int) -> dict:
    """LOOCV 重测：LR vs MLP vs HistGB（含病灶特征 + per-slide 增强池化）。

    输出对比表，判定 T1/T2 是否仍"无收益"：
    若新头部下 macro-F1 ≥ LLM 基线，标记启用 override。
    """
    rows, labels, vecs, times, _, is_gt = load_task_data(task, include_pseudo=False)
    n = len(rows)
    print(f"\n{'='*64}")
    print(f"Task {task} — LOOCV Retest (lesion features + max+mean+std pool + multi-head)")
    print(f"  N={n} GT cases")
    print(f"{'='*64}")

    results = {}

    if task == 3:
        models = [("Cox", "cox"), ("MLP", "mlp"), ("HistGB", "histgb")]
        print(f"  {'Model':<12s} {'C-index':>10s} {'EventAcc':>10s}")
        print(f"  {'-'*32}")
        for name, mtype in models:
            ci, ev, mae, cox_ok = _loocv_survival(
                [r.copy() for r in rows], labels, vecs, times, is_gt, model_type=mtype)
            results[name] = {"c_index": ci, "event_acc": ev, "mae": mae}
            print(f"  {name:<12s} {ci:>10.4f} {ev:>10.3f}")
            if not np.isnan(mae):
                print(f"  {'':12s} MonthsMAE={mae:.2f}")
        print(f"  LLM 基线  C-index={BASELINE[3]['c_index']:.4f}  EventAcc={BASELINE[3]['event_acc']:.3f}")
        best_name = max(results, key=lambda k: results[k]["c_index"])
        best_ci = results[best_name]["c_index"]
        override = best_ci >= BASELINE[3]["c_index"]
    else:
        clf_factories = {
            "LR": lambda: LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced"),
            "MLP": lambda: MLPClassifier(hidden_layer_sizes=(64, 32), alpha=1e-3, max_iter=500, random_state=42),
            "HistGB": lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, random_state=42),
        }
        print(f"  {'Model':<12s} {'Acc':>10s} {'Macro-F1':>10s}  vs LLM基线(Acc={BASELINE[task]['acc']:.3f}, F1={BASELINE[task]['f1']:.3f})")
        print(f"  {'-'*64}")
        for name, factory in clf_factories.items():
            acc, f1 = _loocv_classification(
                task, [r.copy() for r in rows], labels, vecs, is_gt, clf_factory=factory)
            results[name] = {"acc": acc, "f1": f1}
            print(f"  {name:<12s} {acc:>10.3f} {f1:>10.3f}")
        print(f"  LLM 基线  Acc={BASELINE[task]['acc']:.3f}  F1={BASELINE[task]['f1']:.3f}")
        best_name = max(results, key=lambda k: results[k]["f1"])
        best_f1 = results[best_name]["f1"]
        override = best_f1 >= BASELINE[task]["f1"]

    if task < 3:
        best_val = f"F1={best_f1:.3f}"
    else:
        best_val = f"C-index={best_ci:.4f}"
    verdict = "ENABLE override" if override else "维持纯 LLM"
    reason = f"Best head {best_name} {best_val} {'≥' if override else '<'} LLM baseline"
    print(f"\n  >> Best: {best_name} | Decision: {verdict} ({reason})")
    print(f"{'='*64}")

    results["_best"] = best_name
    results["_override"] = override
    return results


def main():
    global _SAVE, _INCLUDE_PSEUDO, _INCLUDE_ALL, _INCLUDE_PSEUDO_V2, _KNN
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", type=int, choices=[1, 2, 3])
    ap.add_argument("--save", action="store_true", help="训练后保存模型到 model/predictor/")
    ap.add_argument("--include-pseudo", action="store_true",
                    help="加载 data/pseudo_labels.json 扩充训练集（不进评估池）")
    ap.add_argument("--include-all", action="store_true",
                    help="加载共识伪标签 + GT 变体 + 灰区合成扩充训练集（含 pseudo，不进评估池）")
    ap.add_argument("--include-pseudo-v2", action="store_true",
                    help="LOOCV 三列对比：GT-only vs GT+旧伪数据 vs GT+v2（G5 反思式伪数据）")
    ap.add_argument("--loocv-compare", action="store_true",
                    help="LOOCV 对比：扩充前 vs 扩充后")
    ap.add_argument("--loocv-retest", action="store_true",
                    help="LOOCV 重测：LR vs MLP vs HistGB（含病灶特征 + 新头部）")
    ap.add_argument("--knn-features", action="store_true",
                    help="启用 kNN 相似病例检索特征（W5-T1；折内建索引防泄漏）")
    args = ap.parse_args()
    _SAVE = args.save
    _INCLUDE_PSEUDO = args.include_pseudo
    _INCLUDE_ALL = args.include_all
    _INCLUDE_PSEUDO_V2 = args.include_pseudo_v2
    _KNN = args.knn_features

    if args.loocv_retest:
        tasks = [args.task] if args.task else [1, 2, 3]
        for t in tasks:
            run_loocv_retest(t)
        return

    if args.loocv_compare:
        tasks = [args.task] if args.task else [1, 2, 3]
        for t in tasks:
            run_loocv_comparison(t)
        return

    tasks = [args.task] if args.task else [1, 2, 3]
    for t in tasks:
        if t == 3:
            run_task3()
        else:
            run_task1_2(t)


if __name__ == "__main__":
    main()
