"""确定性 predictor —— 决策从 LLM 抽离的推理侧实现（容器内可用）。

训练在 scripts/train_predictor.py（--save 产出 model/predictor/predictor_task{N}.joblib）。
本模块是推理侧：加载模型 -> 构建特征 -> 输出决策，毫秒级。

特征构建与训练脚本保持一致（单 case 粒度）。模型文件与特征定义若变更需同步两处。
"""

from __future__ import annotations

import json
import logging
import os
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

# NOTE (2026-09-13, round 27): do NOT replace the task-3 censored (event=0)
# months with a constant, even though `recurrence_time_score` is
# `min(1, pred / follow_up_time)` for censored cases and therefore rewards
# over-prediction.  Measured on the 75 labeled cases, forcing event=0 to a large
# constant scores time 0.8243 vs 0.8407 for the Cox quantile: the model's
# `event` and `months` outputs are coupled, so on the 9 cases where a true
# recurrence is misclassified as censored the quantile still emits a *small*
# (partially correct) value, while a constant turns those nine into hard zeros
# (19-event subgroup: 0.5230 -> 0.3063).  The 110-month clip is a real ceiling
# but it is not worth breaking this coupling.

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

# T2 派生特征（P1.1 特征工程，2026-09-26）
# 依据：配对重复 CV（10×5=50 折，n_GT=72）逐组消融显示 grade 组是 T2 唯一正贡献特征组——
#   仅+grade  LR Acc 0.658→0.751（配对 Δ+0.094，37 胜 / 3 负）
#   仅+cores/psad/flags 均为负贡献；"全部−grade" 增益消失（Δ−0.026）
# 机制：ISUP 分级组与「主分级≥4」是阈值/非线性变换，LR 无法自行构造，而它们是
#       前列腺癌治疗决策的核心分层变量。动机：平台 dev 上 9/11 错例为 AT→AS。
T2_DERIVED_FEATURES = ["d_gleason_sum", "d_grade_group", "d_gleason_primary4"]

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
    2: ["fh_yes", "psa_trend_slope", "psa_trend_last", "psa_trend_n", "free_psa_ratio"] + LESION_FEATURES + PATHOLOGY_FEATURES + PER_LESION_FEATURES + T2_DERIVED_FEATURES,
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


#: Inputs the task-2 "treatment floor" guard consumes.
FLOOR_FEATURE_KEYS = ("path_isup", "pirads_max", "psa_trend_last")


def _floor_inputs_from_clinical(cd: dict) -> dict[str, float | None]:
    """Structured ISUP / PI-RADS / PSA for the task-2 treatment-floor guard.

    Values come from the same parsers :func:`clinical_features` uses, applied to
    the *clinical record* (``pathology_report`` / ``radiology_report`` /
    ``psa_trend``) -- not to the model's own ReAct transcript.

    Measured on the 69 labeled task-2 cases (2026-09-13): the shipped guard read
    ISUP / PI-RADS / PSA with regexes over the transcript and fired exactly
    twice, both times on cases whose structured ISUP is 0 and whose ground truth
    is ``continued_surveillance`` (T2-042, T2-053), turning two correct
    decisions into wrong ones.  Fed from the structured record the same
    thresholds fire on none of the 44 eligible cases, so the guard stays
    available but can no longer be tripped by numbers the model wrote itself.

    A value that is absent or unparsed is returned as ``None``.  Callers must
    read that as "do not fire": a missing report is absence of evidence, not
    evidence of low risk.
    """
    feats: dict[str, float] = {}
    feats.update(lesion_features(cd.get("radiology_report") or ""))
    feats.update(pathology_features(cd.get("pathology_report") or ""))
    feats.update(_psa_trend_features(cd.get("psa_trend") or []))
    out: dict[str, float | None] = {}
    for key in FLOOR_FEATURE_KEYS:
        val = feats.get(key)
        out[key] = float(val) if val is not None and np.isfinite(val) else None
    return out


# ---------------------------------------------------------------------------
# Task-2 active_treatment prior correction (2026-09-14)
# ---------------------------------------------------------------------------
# The platform development result (submission 2026-09-13 22:29) showed the
# deterministic predictor calling `active_treatment` on only 6 of 36 task-2
# cases (17%) against a ground-truth prevalence of 16/36 (44%) -- a 2.7x
# under-prediction -- and 11 of the 12 task-2 errors were missed AT
# (9 AT->active_surveillance, 2 AT->watchful_waiting).  Locally the same
# direction is present but milder: argmax calls AT on 25/72 (35%) vs 31/72 (43%)
# truth.
#
# Because a wrong gate decision zeroes the whole case, the decision objective is
# plain accuracy, not Macro-F1 -- which is why the earlier threshold study
# (calibrated against Macro-F1) found only +0.010 and was dismissed.  Optimised
# for accuracy instead, lowering the AT threshold trades a few false AT calls for
# many recovered true ones.  Measured on the 72 labeled task-2 cases:
#
#   argmax            accuracy 60/72 = 0.833   (AT called 25x, 23 correct)
#   tau = 0.30        accuracy 64/72 = 0.889   (AT called 35x, 30 correct)
#
# and 5-fold CV with tau chosen on the TRAINING folds only (never on the test
# fold): 0.832 +/- 0.110 -> 0.861 +/- 0.051, worst fold 0.643 -> 0.786.
#
# Honest caveat: the +0.029 CV gain is below one fold sigma (0.051), so this is
# a prior-matching correction motivated by the platform's own measured class
# distribution, not a locally proven win.  It only moves a decision when
# p(AT) >= tau while argmax picked something else, so the blast radius is small
# and fully auditable via the `at_threshold` field recorded per case.
# --- T2 AT 先验修正：RF 头下已关闭（2026-09-26，P1.4） ----------------------
# 历史上的 τ=0.30 是**为 LR 概率尺度**校准的（见下方原始说明），其动机是平台 dev 上
# LR 只调用 AT 6/36（17%）而 GT 为 44% 的 2.7x 漏报。
#
# P1.4 把 T2 头部换成浅层随机森林后，该前提消失。诚实 50 折 OOF 实测（n_GT=72）：
#   τ=0.00（关闭）  Acc 0.853  F1 0.783 | AT 调用 324/360 精确率 0.846 召回 0.884
#   τ=0.30（原值）  Acc 0.844  F1 0.774 | ΔAcc −0.0098（6 胜 / 11 负）← 已变为负贡献
#   τ=0.35          Acc 0.856  F1 0.785 | ΔAcc +0.0028（5 胜 / 3 负）← 不显著
#   τ=0.25          Acc 0.798  F1 0.734 | ΔAcc −0.0552（6 胜 / 27 负）← 严重过度触发
# RF 的 AT 调用率（44%）已与 GT（43%）匹配，先验修正的目的由更校准的头部自然达成。
# 故默认关闭；如需重新启用，设 CHIMERA_T2_AT_THRESHOLD>0 即可（机制保留）。
T2_AT_THRESHOLD = float(os.environ.get("CHIMERA_T2_AT_THRESHOLD", "0.0"))

# ---------------------------------------------------------------------------
# Task-2 active_treatment prior correction（历史记录，2026-09-14，τ 已默认关闭）
# ---------------------------------------------------------------------------

# --- T3 months 连续化 + 风险 tie-break（P3，2026-09-26） ---------------------
# 背景：T3 的 ranking_score **精确等于 concordance_index**（由三份平台 metrics.json
# 的 aggregates 逐位复核：v8/v12/v13 的 task3.ranking_score == concordance_index）。
# 因此 C-index 是 T3 唯一有效杠杆，而 time_score / event_acc 不进入 T3 排名。
#
# 原实现把 months 取在 baseline_times 网格上（实测 dev 上 23 例只落在 10 个不同取值），
# 并列在 C-index 里只算 0.5 分；且 event=0 用 S(t)=0.75 交叉点（比 event=1 的 0.5 更早），
# 使删失病例的 months 偏短，排序信息被量化破坏。
#
# 改为「模型自身生存曲线在 S(t)=q 处的**连续插值**」作为主值，再减去
# eps * 标准化 risk 做 tie-break，使 months 对 risk **严格单调递减、无并列**。
# 该值同时也是模型对复发时间的直接点估计，故 time_score 不降反升。
#
# 多 seed 配对判定（8 seed × 5 折 = 40 折，逐折 C-index 配对求差）：
#   current (q1=.5, q0=.75 网格)  C-index 0.8298 ± 0.112 | time 0.699
#   medsurv q=0.6 + tie-break     C-index 0.8518 ± 0.102 | time 0.734
#   ΔC = +0.0219（配对 σ=0.036，标准误 0.0057 ≈ 3.8 SE），胜/负 23/10
# 注：C-index 必须**逐折**计算再平均——各折 Cox 的 risk 尺度不同，汇总后计算会被跨折
#     尺度差混淆（曾因此得到误导性的 0.812 vs 0.745）。
T3_MONTHS_Q = float(os.environ.get("CHIMERA_T3_MONTHS_Q", "0.6"))
T3_MONTHS_TIEBREAK_EPS = float(os.environ.get("CHIMERA_T3_TIEBREAK_EPS", "0.5"))
_T3_MONTHS_CLIP = (1.0, 110.0)
# tie-break 预留的 z 跨度（z 典型落在 ±3 内；取 5 留余量），
# 使低风险病例在到达 110 上限前仍保留 tie-break 的区分度。
_T3_TIEBREAK_HEAD_Z = 5.0


def _cox_months_continuous(surv: np.ndarray, baseline_times: np.ndarray, q: float) -> float:
    """生存曲线 S(t)=q 处的**连续插值**时间（取代原网格取点）。

    原实现 `baseline_times[searchsorted(...)]` 只能落在 baseline_times 的离散取值上，
    并列在 C-index 中只得 0.5 分。这里在跨越 S=q 的相邻两个基线时间点之间做线性插值，
    得到连续、随 risk 严格单调的 months。
    """
    surv = np.asarray(surv, dtype=float)
    times = np.asarray(baseline_times, dtype=float)
    n = len(times)
    i = int(np.searchsorted(-surv, -q))
    if i <= 0:
        return float(times[0])
    if i >= n:
        return float(times[n - 1])
    s0, s1 = float(surv[i - 1]), float(surv[i])
    t0, t1 = float(times[i - 1]), float(times[i])
    frac = 0.0 if s0 == s1 else (s0 - q) / (s0 - s1)
    frac = min(max(frac, 0.0), 1.0)
    return t0 + frac * (t1 - t0)


def _t2_at_prior_correction(task: int, label: str, proba: dict[str, float]) -> str:
    """Return the task-2 label after the AT prior correction (no-op elsewhere)."""
    if task != 2 or T2_AT_THRESHOLD <= 0:
        return label
    if label == "active_treatment":
        return label
    if proba.get("active_treatment", 0.0) >= T2_AT_THRESHOLD:
        return "active_treatment"
    return label


def treatment_floor_inputs(case_dir: Path) -> dict[str, float | None]:
    """Read one case's structured treatment-floor inputs (task 2 only).

    Best-effort: returns ``{}`` when the case record is missing or unreadable,
    which makes the guard a no-op rather than an error.
    """
    from .tools.base import CASE_DATA_FILENAMES_BY_TASK  # local import: avoid cycle

    path = Path(case_dir) / CASE_DATA_FILENAMES_BY_TASK[2]
    if not path.exists():
        log.warning("treatment_floor_inputs: no case record at %s", path)
        return {}
    try:
        return _floor_inputs_from_clinical(json.loads(path.read_text()))
    except Exception as exc:  # noqa: BLE001 - a guard must never fail a run
        log.warning("treatment_floor_inputs: unreadable %s: %s", path, exc)
        return {}


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


def build_case_features(case_dir: Path, task: int,
                         allowed_sections: set[str] | None = None,
                         observed_t1_fields: dict | None = None) -> tuple[dict, np.ndarray | None]:
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

    if observed_t1_fields is not None:
        from .agent.evidence_features import T1_OBSERVED_FEATURES
        if task != 1 or set(observed_t1_fields) - T1_OBSERVED_FEATURES:
            raise ValueError("Observed feature bridge is restricted to the four supported T1 fields")
        for name, value in observed_t1_fields.items():
            value = float(value)
            if not np.isfinite(value):
                raise ValueError(f"Non-finite observed feature: {name}")
            if value < 0 or name == "vol" and value == 0 or name == "pirads_int" and value not in (1, 2, 3, 4, 5):
                raise ValueError(f"Invalid observed feature domain: {name}={value}")
            if not np.isfinite(row[name]):
                row[name] = value

    cd_file = case_dir / CLINICAL_FILE[task]
    clin = {}
    if cd_file.exists():
        cd = json.loads(cd_file.read_text())
        # P0-2b 成本对齐：allowed_sections 非 None 时只消费其中列出的段落
        # （= LLM 已经揭示过、已为 tool 分付过费的那些）。None = 历史行为。
        if allowed_sections is not None:
            cd = {k: v for k, v in cd.items() if k in allowed_sections}
        clin = clinical_features(cd, task)
    for f in CLINICAL_FEATURES[task]:
        row[f] = clin.get(f, 0.0)

    if task == 2:
        # T2 派生特征（P1.1）：ISUP 分级组 / Gleason 和 / 主分级≥4 阈值标志。
        # 与训练侧 train_predictor.build_case_features 必须逐字一致。
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
        self._repair_legacy_estimators()

    def _repair_legacy_estimators(self) -> None:
        """Restore the ``multi_class`` attribute stripped by cross-version unpickling.

        The shipped bundles were pickled by scikit-learn **1.9.0** (which had
        already removed the ``multi_class`` parameter), while the container runs
        **1.6.1** (which still declares it). ``LogisticRegression.get_params()``
        re-derives every declared parameter via ``getattr(self, name)`` without a
        default, so on the loaded object it raises

            AttributeError: 'LogisticRegression' object has no attribute 'multi_class'

        and ``predict_proba`` calls ``get_params`` internally -- so *every*
        predictor call fails, not just probability reporting. On GC this surfaced
        only as ``Task 1: predictor failed for gc-case: ...`` and silently
        degraded the deterministic predictor to a fallback.

        ``multi_class="auto"`` is exactly the value these models were trained
        under (it was 1.9.0's default), so pinning it explicitly restores the
        training-time semantics instead of changing them. Retraining the bundles
        under 1.6.1 would also fix this; that is a larger change and is tracked
        separately.
        """
        for task, bundle in self._models.items():
            for key in ("clf", "clf_event", "reg_months"):
                est = bundle.get(key) if isinstance(bundle, dict) else None
                if est is not None and hasattr(est, "get_params") and not hasattr(est, "multi_class"):
                    est.multi_class = "auto"
                    log.info(
                        "Predictor task%d: restored %s.multi_class='auto' "
                        "(cross-version unpickle repair)",
                        task, type(est).__name__,
                    )

    @property
    def available_tasks(self) -> list[int]:
        return sorted(self._models)

    def predict(self, case_dir: Path, task: int,
                reveal_mask: set[str] | None = None, observed_t1_fields: dict | None = None) -> dict:
        """返回决策 dict；无模型时抛 FileNotFoundError。

        reveal_mask（P0-2b 成本对齐）：非 None 时只允许消费其中列出的
        clinical-data 段落（= LLM 已揭示的），None = 读全文件（历史行为）。
        """
        if task not in self._models:
            raise FileNotFoundError(f"No predictor model for task {task}")
        m = self._models[task]
        row, vec = build_case_features(case_dir, task, allowed_sections=reveal_mask, observed_t1_fields=observed_t1_fields)
        # W5-T1: kNN 相似病例检索特征（模型带 knn_index 时启用）。
        # 线上查询 case 不在索引中 -> 无泄漏；exclude_id 仅为防御性自排除。
        knn_index = m.get("knn_index")
        if knn_index is not None:
            f = knn_index.query(sim_vector_from_row(row, task), exclude_id=case_dir.name)
            row.update({n: float(f[n]) for n in KNN_FEATURE_NAMES[task]})
        X = _feat_matrix(row, vec, m)

        if m["kind"] == "classifier":
            names = [str(x) for x in m["label_names"]]
            proba_arr = m["clf"].predict_proba(X)[0]
            proba = {k: float(v) for k, v in zip(names, proba_arr.tolist())}
            label = names[int(proba_arr.argmax())]
            label = _t2_at_prior_correction(task, label, proba)
            return {
                "case_id": case_dir.name, "task": task, "decision": label,
                "probabilities": proba,
                # Auditable: the threshold actually applied to this case.
                "at_threshold": T2_AT_THRESHOLD if task == 2 else None,
            }
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

    def explain(self, case_dir: Path, task: int,
                reveal_mask: set[str] | None = None, observed_t1_fields: dict | None = None) -> dict:
        """返回本 case 的 predictor 输入审计信息（只读，不改变 predict() 行为）。

        与 predict() 走完全相同的装配路径：build_case_features → knn_index → 特征名清单。
        用途：把"模型实际消费了什么"落盘，使单例可审计、死特征可发现、泄漏可检测。
        """
        if task not in self._models:
            raise FileNotFoundError(f"No predictor model for task {task}")
        m = self._models[task]
        row, vec = build_case_features(case_dir, task, allowed_sections=reveal_mask, observed_t1_fields=observed_t1_fields)

        def _ok(v) -> bool:
            if v is None:
                return False
            try:
                return bool(np.isfinite(float(v)))
            except (TypeError, ValueError):
                return True  # 非数值（如类别串）视为有值

        num_names = [str(n) for n in m["num_names"]]
        cat_names = [str(n) for n in (m.get("cat_names") or [])]
        names = KNN_FEATURE_NAMES.get(task) or []
        knn_index = m.get("knn_index")

        knn_block: dict = {"enabled": False, "names": [], "values": {}, "neighbors": []}
        if knn_index is not None and names:
            f = knn_index.query(sim_vector_from_row(row, task), exclude_id=case_dir.name)
            knn_block = {
                "enabled": True,
                "names": list(names),
                "values": {n: float(f[n]) for n in names},
                "neighbors": [str(x) for x in (f.get("_neighbor_ids") or [])],
                "self_excluded": bool(f.get("knn_self_excluded", 0)),
            }
            row.update({n: float(f[n]) for n in names})
        elif knn_index is None:
            knn_block["reason"] = "bundle has no knn_index"
        else:
            knn_block["reason"] = "task has no KNN_FEATURE_NAMES"

        # 本任务实际消费的 clinical-data 段落（与 clinical_features() 逐条对应）
        consumed = {
            1: ["radiology_report", "psa_trend", "laboratory_results", "family_history"],
            2: ["radiology_report", "psa_trend", "laboratory_results", "family_history",
                "pathology_report"],
            3: ["radiology_report", "psa_trend", "laboratory_results", "family_history",
                "pathology_report", "surgical_pathology_report", "previous_notes"],
        }.get(task, [])

        return {
            "model_version": {
                "num_features": len(num_names),
                "cat_features": len(cat_names),
                "has_vector": m.get("reducer") is not None,
                "kind": m.get("kind"),
                "head": type(m.get("clf", m.get("clf_event"))).__name__,
            },
            "feature_values": {n: (float(row[n]) if isinstance(row.get(n), (int, float, np.floating))
                                   and _ok(row.get(n)) else row.get(n))
                               for n in num_names if _ok(row.get(n))},
            "feature_missing": [n for n in num_names if not _ok(row.get(n))],
            "categorical": {n: row.get(n) for n in cat_names},
            "knn": knn_block,
            "consumed_sections": consumed,
            "reveal_mask_applied": (sorted(reveal_mask) if reveal_mask is not None else None),
            "masked_out_sections": (sorted(set(consumed) - set(reveal_mask))
                                    if reveal_mask is not None else []),
        }

    @staticmethod
    def _predict_cox(case_dir: Path, m: dict, X: np.ndarray) -> dict:
        """Cox 模型预测：风险分 → event + months。

        纯 numpy 计算，不依赖 lifelines（提交容器无需安装）。

        months（P3 起）= 生存曲线在 S(t)=T3_MONTHS_Q 处的**连续插值**，
        再减去 eps * 标准化 risk 做 tie-break（详见 T3_MONTHS_Q 处注释）。
        兼容旧 bundle：缺 risk_mu/risk_sd 时退化为不做 tie-break。
        """
        risk = float(X[0] @ m["cox_beta"])
        event = 1 if risk >= m.get("median_risk", 0.0) else 0

        # 生存函数 S(t|X) = S_0(t)^exp(risk)
        baseline_surv = m["baseline_survival"]
        baseline_times = m["baseline_times"]
        partial_hazard = float(np.exp(risk))
        surv = np.power(baseline_surv, partial_hazard)

        months = _cox_months_continuous(surv, baseline_times, T3_MONTHS_Q)

        risk_mu, risk_sd = m.get("risk_mu"), m.get("risk_sd")
        if risk_mu is not None and risk_sd:
            # ⚠️ 必须先给 tie-break **预留头部空间**再裁剪：
            # 若先把主值裁到 110 再减 eps*z，低风险那一半会被裁回 110，
            # tie-break 被整段抹平（实测 73 例只剩 25 个取值，且中位=110）。
            head = T3_MONTHS_TIEBREAK_EPS * _T3_TIEBREAK_HEAD_Z
            lo, hi = _T3_MONTHS_CLIP
            base = float(np.clip(months, lo, max(hi - head, lo)))
            z = (risk - float(risk_mu)) / float(risk_sd)
            months = base - T3_MONTHS_TIEBREAK_EPS * z
        months = float(np.clip(months, *_T3_MONTHS_CLIP))

        p_event = float(1.0 / (1.0 + np.exp(-risk)))
        return {
            "case_id": case_dir.name,
            "task": 3,
            "event": event,
            "p_event": p_event,
            "months_to_recurrence": months,
        }
