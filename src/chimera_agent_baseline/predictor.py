"""确定性 predictor —— 决策从 LLM 抽离的推理侧实现（容器内可用）。

训练在 scripts/train_predictor.py（--save 产出 model/predictor/predictor_task{N}.joblib）。
本模块是推理侧：加载模型 -> 构建特征 -> 输出决策，毫秒级。

特征构建与训练脚本保持一致（单 case 粒度）。模型文件与特征定义若变更需同步两处。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.decomposition import PCA
from sklearn.neural_network import MLPClassifier

from .knn_features import KNN_FEATURE_NAMES, sim_vector_from_row

log = logging.getLogger(__name__)

PCA_DIM = 30


class VectorReducer(BaseEstimator, TransformerMixin):
    """把向量特征（可能缺失）PCA 降维；缺失行填 0。

    独立类（在 src 包内）保证 joblib 反序列化时引用稳定——训练脚本也复用本类。
    """

    def __init__(self, dim: int = PCA_DIM):
        self.dim = dim

    def _matrix(self, X):
        n = len(X)
        dims = [v.shape[0] for v in X if v is not None]
        d = max(dims) if dims else 0
        M = np.zeros((n, max(d, 1)))
        for i, v in enumerate(X):
            if v is not None:
                M[i, : v.shape[0]] = v
        return M

    def fit(self, X, y=None):
        M = self._matrix(X)
        self.max_dim_ = M.shape[1]
        if M.shape[1] > 1:
            n_comp = min(self.dim, M.shape[0], M.shape[1])
            self.pca_ = PCA(n_components=n_comp, random_state=0)
            self.pca_.fit(M)
        else:
            self.pca_ = None
        return self

    def transform(self, X):
        M = self._matrix(X)
        if M.shape[1] < getattr(self, "max_dim_", M.shape[1]):
            M = np.pad(M, ((0, 0), (0, self.max_dim_ - M.shape[1])))
        if self.pca_ is None:
            return np.zeros((M.shape[0], 1))
        return self.pca_.transform(M)

    def get_feature_names_out(self, input_features=None):
        return [f"vec{i}" for i in range(self.pca_.n_components_)] if self.pca_ else ["vec0"]

# ---------------------------------------------------------------------------
# 特征定义（与 scripts/train_predictor.py 保持一致）
# ---------------------------------------------------------------------------

NUMERIC_FEATURES = {
    1: ["psa", "age", "psad", "psav", "vol", "cspca", "months", "pirads_int"],
    2: ["psa", "age", "psad", "psav", "vol", "cspca", "months", "bx_gl_prim", "bx_gl_sec", "pirads_int"],
    3: ["psa", "age"],
}

CATEGORICAL_FEATURES = {
    1: ["dre", "bx"],
    2: ["dre", "bx", "ct"],
    3: [],
}

CLINICAL_FILE = {
    1: "prostate-biopsy-decision-clinical-data.json",
    2: "prostate-treatment-decision-clinical-data.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
}

FEATURES_FILE = "prostate-modality-level-neural-representations.json"

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

CLINICAL_FEATURES = {
    1: ["fh_yes", "psa_trend_slope", "psa_trend_last", "psa_trend_n", "free_psa_ratio"] + LESION_FEATURES,
    2: ["fh_yes", "psa_trend_slope", "psa_trend_last", "psa_trend_n", "free_psa_ratio"] + LESION_FEATURES + PATHOLOGY_FEATURES + PER_LESION_FEATURES,
    3: ["fh_yes"] + LESION_FEATURES + T3_REPORT_FEATURES + PATHOLOGY_FEATURES + PER_LESION_FEATURES + T3_DERIVED_FEATURES,
}

VECTOR_TASKS = {3}  # 向量只在 Task3 有用（消融结论）


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

    # Biopsy needle/session count — count "Biopsy N" / "Timepoint N" / "session"
    biopsy_marks = re.findall(r"biopsy\s*(?:\d+|session)", tl)
    timepoint_marks = re.findall(r"timepoint\s*\d+", tl)
    session_count = max(len(biopsy_marks), len(timepoint_marks))
    if session_count == 0 and "biopsy" in tl:
        session_count = 1
    out["bx_needle_count"] = float(session_count) if session_count > 0 else np.nan

    # Positive needles — count sections with adenocarcinoma/Gleason
    sections = re.split(r"(?=biopsy\s*\d+|timepoint\s*\d+)", txt, flags=re.IGNORECASE)
    positive_count = sum(
        1 for s in sections if "adenocarcinoma" in s.lower() or "gleason" in s.lower()
    )
    out["bx_positive_needles"] = float(positive_count) if positive_count > 0 else np.nan

    # SVI — seminal vesicle invasion
    if "seminal vesicle" in tl and (
        "invasion" in tl or "invaded" in tl or "located in the seminal" in tl
    ):
        out["path_svi"] = 1.0
    elif "seminal vesicle" in tl:
        out["path_svi"] = 0.0
    else:
        out["path_svi"] = np.nan

    # EPE — extraprostatic extension (typically in surgical pathology)
    if "extraprostatic extension was present" in tl or "extracapsular extension" in tl:
        out["path_epe"] = 1.0
    elif "no extraprostatic extension" in tl:
        out["path_epe"] = 0.0
    else:
        out["path_epe"] = np.nan

    # Margin status (typically in surgical pathology)
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


def _num(v) -> float:
    if v is None or v == "" or v == "None":
        return np.nan
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def _pirads_int(v) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return np.nan


def _parse_lab(lab: list) -> dict[str, float]:
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
    out: dict[str, float] = {}
    fh = str(cd.get("family_history") or "").strip().lower()
    out["fh_yes"] = 1.0 if fh in ("yes", "true") else 0.0
    out.update(_psa_trend_features(cd.get("psa_trend") or []))
    out.update(_parse_lab(cd.get("laboratory_results") or []))
    out.update(lesion_features(cd.get("radiology_report") or ""))
    out.update(lesion_features_per_lesion(cd.get("radiology_report") or ""))

    if task >= 2:
        # T2/T3: 补病理报告特征
        out.update(pathology_features(cd.get("pathology_report") or ""))

    if task == 3:
        # T3: 从文本报告中解析结构化特征
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


# ---------------------------------------------------------------------------
# T3 clinical report 文本解析（P1-F 特征工程）
# ---------------------------------------------------------------------------

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
    # 病理分期 pT2~pT4 -> 数值
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


def _parse_previous_notes(text: str) -> dict[str, float]:
    """从 previous_notes 解析 CCI。"""
    out: dict[str, float] = {}
    if not text:
        return out
    m = re.search(r"Charlson Comorbidity Index.*?(\d+)", text, re.IGNORECASE)
    out["cci"] = float(m.group(1)) if m else np.nan
    return out


def _pool_slices(arr: np.ndarray) -> np.ndarray:
    """per-slide 池化：mean + max + std 拼接，替代单一 mean-pool。"""
    return np.concatenate([arr.mean(0), arr.max(0), arr.std(0)])


def build_vector_features(features: dict, task: int) -> np.ndarray | None:
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
    max_dim = max(v.shape[0] for v in vecs)
    padded = [np.pad(v, (0, max_dim - v.shape[0])) for v in vecs]
    return np.concatenate(padded)


def build_case_features(case_dir: Path, task: int) -> tuple[dict, np.ndarray | None]:
    # GC adapter (inference.py) materialises the prompt as ``prompt.json``;
    # local data trees carry the original ``structured-prompt.json``.
    prompt_path = case_dir / "structured-prompt.json"
    if not prompt_path.exists():
        prompt_path = case_dir / "prompt.json"
    prompt = json.loads(prompt_path.read_text())

    row = {}
    for f in NUMERIC_FEATURES[task]:
        row[f] = _pirads_int(prompt.get("pirads")) if f == "pirads_int" else _num(prompt.get(f))
    for f in CATEGORICAL_FEATURES[task]:
        row[f] = prompt.get(f)

    cd_file = case_dir / CLINICAL_FILE[task]
    clin = {}
    if cd_file.exists():
        clin = clinical_features(json.loads(cd_file.read_text()), task)
    for f in CLINICAL_FEATURES[task]:
        row[f] = clin.get(f, 0.0)

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


# ---------------------------------------------------------------------------
# 预测
# ---------------------------------------------------------------------------


def _feat_matrix(row: dict, vec, m: dict) -> np.ndarray:
    # Cox 模型使用 all_num_names + vt（VarianceThreshold）做特征选择
    if "vt" in m and m.get("all_num_names"):
        all_names = m["all_num_names"]
        num_mat = np.array([[row.get(n, np.nan) for n in all_names]], dtype=float)
        num_mat = m["imp"].transform(num_mat)
        num_mat = m["vt"].transform(num_mat)
        num_mat = m["scaler"].transform(num_mat)
    else:
        num_names = m["num_names"]
        num_mat = m["imp"].transform(np.array([[row.get(n, np.nan) for n in num_names]], dtype=float))
        num_mat = m["scaler"].transform(num_mat)
    parts = [num_mat]
    cat_names = m.get("cat_names") or []
    if cat_names:
        cat_mat = m["cat_encoder"].transform([[row.get(c) for c in cat_names]])
        parts.append(cat_mat.toarray() if hasattr(cat_mat, "toarray") else np.asarray(cat_mat))
    reducer = m.get("reducer")
    if reducer is not None:
        parts.append(reducer.transform([vec]))
    return np.hstack(parts)


class Predictor:
    """加载三任务的确定性模型，提供 predict_case(case_dir, task)。"""

    def __init__(self, model_dir: str | Path):
        import joblib

        self._models: dict[int, dict] = {}
        self._model_dir = Path(model_dir)
        for task in (1, 2, 3):
            f = self._model_dir / f"predictor_task{task}.joblib"
            if f.exists():
                self._models[task] = joblib.load(f)
                log.info("Predictor loaded task%d from %s", task, f)
            else:
                log.warning("Predictor model missing: %s (run scripts/train_predictor.py --save)", f)

    @property
    def available_tasks(self) -> list[int]:
        return sorted(self._models)

    def predict(self, case_dir: Path, task: int) -> dict:
        """返回决策 dict；无模型时抛 FileNotFoundError。"""
        if task not in self._models:
            raise FileNotFoundError(f"No predictor model for task {task}")
        m = self._models[task]
        row, vec = build_case_features(case_dir, task)
        # W5-T1: kNN 相似病例检索特征（模型带 knn_index 时启用）。
        # 线上查询 case 不在索引中 -> 无泄漏；exclude_id 仅为防御性自排除。
        knn_index = m.get("knn_index")
        if knn_index is not None:
            f = knn_index.query(sim_vector_from_row(row, task), exclude_id=case_dir.name)
            row.update({n: float(f[n]) for n in KNN_FEATURE_NAMES[task]})
        X = _feat_matrix(row, vec, m)

        if m["kind"] == "classifier":
            label = str(m["label_names"][int(m["clf"].predict(X)[0])])
            proba = {str(k): float(v) for k, v in zip(m["label_names"], m["clf"].predict_proba(X)[0].tolist())}
            return {"case_id": case_dir.name, "task": task, "decision": label, "probabilities": proba}
        elif m["kind"] == "cox":
            return self._predict_cox(case_dir, m, X)
        else:  # survival (task3, legacy HistGB)
            p_event = float(m["clf_event"].predict_proba(X)[0][1])
            event = 1 if p_event >= m["event_rate"] else 0
            months = None
            if event == 1 and m["reg_months"] is not None:
                months = float(np.clip(m["reg_months"].predict(X)[0], 0, None))
            return {
                "case_id": case_dir.name,
                "task": 3,
                "event": event,
                "p_event": p_event,
                "months_to_recurrence": months,
            }

    @staticmethod
    def _predict_cox(case_dir: Path, m: dict, X: np.ndarray) -> dict:
        """Cox 模型预测：风险分 → event + months。

        纯 numpy 计算，不依赖 lifelines（提交容器无需安装）。
        """
        risk = float(X[0] @ m["cox_beta"])
        event = 1 if risk >= m.get("median_risk", 0.0) else 0

        # 生存函数 S(t|X) = S_0(t)^exp(risk)
        baseline_surv = m["baseline_survival"]
        baseline_times = m["baseline_times"]
        partial_hazard = float(np.exp(risk))
        surv = np.power(baseline_surv, partial_hazard)

        months = None
        if event == 1:
            idx = int(np.searchsorted(-surv, -0.5))
            months = float(baseline_times[min(idx, len(baseline_times) - 1)])
            months = float(np.clip(months, 1.0, 60.0))  # event=1 上限 60 月
        else:
            idx = int(np.searchsorted(-surv, -0.75))
            months = float(baseline_times[min(idx, len(baseline_times) - 1)])
            months = float(np.clip(months, 1.0, 110.0))  # event=0 上限 110 月
        p_event = float(1.0 / (1.0 + np.exp(-risk)))
        return {
            "case_id": case_dir.name,
            "task": 3,
            "event": event,
            "p_event": p_event,
            "months_to_recurrence": months,
        }
