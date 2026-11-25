"""Predictor 通用组件 —— 从 src 包 re-export，保证 joblib 反序列化引用稳定。

模型 pickle 中 VectorReducer 的类引用必须指向可导入模块。训练脚本在
scripts/ 下运行，而模型加载在 src 包内（容器 PYTHONPATH=/opt/app/src），
因此统一以 chimera_agent_baseline.predictor.VectorReducer 为准。
"""

from chimera_agent_baseline.predictor import PCA_DIM, VectorReducer

__all__ = ["PCA_DIM", "VectorReducer"]
