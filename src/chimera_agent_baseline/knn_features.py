"""kNN 相似病例检索特征（W5-T1）——把"相似历史病例的模式"量化为数字特征喂 predictor。

复用 scripts/build_case_index.py 的相似度方法（结构化临床特征 z-score + cosine），
T1/T2 的相似度特征与其 FEATURE_NAMES 完全对齐；T3 的 structured-prompt 缺少
pirads/vol 等字段，改用临床报告解析特征（pirads_rad/psa_density/gleason_sum 等）。

防泄漏红线（L0 已验证）：
- 评估（LOOCV/CV）时索引只能由训练折 GT case 构成：折内建索引、折内查询，
  绝不能用全量索引给评估折生成特征（见 scripts/train_predictor.py 的折内调用）；
- 查询 case 若在索引中必须排除自身（exclude_id），kNN_self_excluded 标记该行为；
- 只有 GT case 进索引（伪标签 case 只作查询方，永不入库）。

因此 kNN 特征不能预计算成全量索引文件——必须在每折内现算；
唯一例外是 --save 部署口径（全量 GT 索引 + 自排除），索引随 joblib 下发，
推理侧由 predictor.Predictor.predict 现算查询（无泄漏：线上 case 不在索引）。
"""

from __future__ import annotations

import numpy as np

K = 5  # top-k 相似 GT 病例

# 相似度原始特征（取自 build_case_features 的特征 row；T1/T2 与 build_case_index.py 对齐）
SIM_FEATURES = {
    1: ["psa", "psad", "age", "vol", "pirads_int", "cspca", "psav", "psap"],
    2: ["psa", "psad", "age", "vol", "pirads_int", "cspca", "psav", "psap"],
    3: ["psa", "age", "psa_density", "prostate_volume", "pirads_rad",
        "cspca_prob", "gleason_sum_sg", "path_stage", "cci"],
}

# kNN 平均用列
_PIRADS_COL = {1: "pirads_int", 2: "pirads_int", 3: "pirads_rad"}

# T2 结局 = 四类 one-vs-rest 投票（避免人为 ordinal 假设）
T2_CLASSES = ["active_surveillance", "continued_surveillance", "watchful_waiting", "active_treatment"]

# 进特征矩阵的 kNN 特征名（predictor 推理侧同序；不含验证用字段）
KNN_FEATURE_NAMES = {
    1: ["knn_avg_outcome", "knn_avg_sim", "knn_avg_psa", "knn_avg_pirads", "knn_max_sim"],
    2: [f"knn_vote_{c}" for c in T2_CLASSES]
       + ["knn_avg_sim", "knn_avg_psa", "knn_avg_pirads", "knn_max_sim"],
    3: ["knn_avg_event", "knn_avg_months", "knn_avg_sim", "knn_avg_psa", "knn_avg_pirads", "knn_max_sim"],
}


def sim_vector_from_row(row: dict, task: int) -> np.ndarray:
    """从特征 row 抽相似度原始向量（缺失 -> NaN，索引/查询侧统一按均值中性化）。"""
    out = []
    for n in SIM_FEATURES[task]:
        try:
            out.append(float(row.get(n)))
        except (TypeError, ValueError):
            out.append(np.nan)
    return np.array(out, dtype=float)


def outcome_from_label(task: int, label) -> dict:
    """GT 标签 -> 结局字段（仅索引成员需要）。"""
    if task == 1:
        return {"yes": 1.0 if str(label).strip().lower() == "yes" else 0.0}
    if task == 2:
        return {"cls": str(label).strip().lower()}
    raise ValueError(f"task {task} outcome needs (event, months); use build_records")


def build_records(task: int, rows, case_ids, is_gt, labels=None, times=None) -> dict:
    """特征 rows -> {case_id: record}；GT 带 outcome（可入索引），非 GT 仅查询用。"""
    recs: dict[str, dict] = {}
    for i, cid in enumerate(case_ids):
        rec = {"case_id": cid, "sim_vec": sim_vector_from_row(rows[i], task)}
        if is_gt[i]:
            if task == 3:
                rec["outcome"] = {"event": float(labels[i]), "months": float(times[i])}
            else:
                rec["outcome"] = outcome_from_label(task, labels[i])
        recs[cid] = rec
    return recs


class CaseKnnIndex:
    """GT case 相似度索引。归一化统计量只来自索引成员（折内统计，无泄漏）。

    缺失值处理：索引成员缺失特征用索引成员均值填充（z=0 中性）；查询侧同样
    以索引均值填充。cosine 在 z-score 向量上计算（与 build_case_index.py 一致）。
    """

    def __init__(self, task: int, records: list[dict]):
        self.task = task
        self.ids = [r["case_id"] for r in records]
        self.outcomes = [r.get("outcome") for r in records]
        d = len(SIM_FEATURES[task])
        raw = np.array([r["sim_vec"] for r in records], dtype=float) if records else np.zeros((0, d))
        finite = np.isfinite(raw)
        col_mean = np.array(
            [raw[finite[:, j], j].mean() if finite[:, j].any() else 0.0 for j in range(d)]
        )
        self.raw = np.where(finite, raw, col_mean)  # 缺失 -> 索引均值（z=0 中性）
        self.means = self.raw.mean(axis=0) if len(records) else np.zeros(d)
        self.stds = self.raw.std(axis=0) if len(records) else np.ones(d)
        self.stds[~np.isfinite(self.stds) | (self.stds < 1e-9)] = 1.0
        self.norm = (self.raw - self.means) / self.stds
        norms = np.linalg.norm(self.norm, axis=1, keepdims=True)
        self.unit = self.norm / np.where(norms < 1e-12, 1.0, norms)
        self._psa_col = SIM_FEATURES[task].index("psa")
        self._pirads_col = SIM_FEATURES[task].index(_PIRADS_COL[task])

    def query(self, sim_vec, exclude_id: str | None = None, k: int = K) -> dict:
        """返回 kNN 特征 dict（含验证用 knn_self_excluded / _neighbor_ids）。

        exclude_id：查询 case 自身 id——若其在索引中则强制排除（防泄漏）；
        线上查询 case 不在索引时该参数无副作用。
        """
        q = (np.asarray(sim_vec, dtype=float) - self.means) / self.stds
        q = np.where(np.isfinite(q), q, 0.0)
        qn = np.linalg.norm(q)
        q = q / qn if qn > 1e-12 else q
        sims = self.unit @ q if len(self.ids) else np.zeros(0)
        order = np.argsort(-sims)
        picked: list[int] = []
        self_excluded = False
        for i in order:
            if exclude_id is not None and self.ids[i] == exclude_id:
                self_excluded = True
                continue
            picked.append(int(i))
            if len(picked) >= k:
                break

        feats = {n: 0.0 for n in KNN_FEATURE_NAMES[self.task]}
        feats["knn_self_excluded"] = float(self_excluded)  # 仅 L0 验证用，不进特征矩阵
        feats["_neighbor_ids"] = [self.ids[i] for i in picked]
        if not picked:
            return feats
        s = np.array([sims[i] for i in picked])
        psa = self.raw[picked, self._psa_col]
        pirads = self.raw[picked, self._pirads_col]
        feats["knn_avg_sim"] = float(s.mean())
        feats["knn_max_sim"] = float(s.max())
        feats["knn_avg_psa"] = float(psa.mean())
        feats["knn_avg_pirads"] = float(pirads.mean())
        oc = [self.outcomes[i] for i in picked]
        if self.task == 1:
            feats["knn_avg_outcome"] = float(np.mean([o["yes"] for o in oc]))
        elif self.task == 2:
            for c in T2_CLASSES:
                feats[f"knn_vote_{c}"] = float(np.mean([1.0 if o["cls"] == c else 0.0 for o in oc]))
        else:
            feats["knn_avg_event"] = float(np.mean([o["event"] for o in oc]))
            feats["knn_avg_months"] = float(np.mean([o["months"] for o in oc]))
        return feats
