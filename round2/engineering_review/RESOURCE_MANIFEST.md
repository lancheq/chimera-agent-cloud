# RESOURCE_MANIFEST — 模型与资源身份

全部为 2026-11-25 在服务器 `/home/hedongze/CHIMERA-agent` 实测，不含权重文件本身。

## 一、主模型与推理后端

### 实际运行命令（取自 `ps aux`，非文档）

```bash
CUDA_VISIBLE_DEVICES=0,1 nohup ~/llama-build-avx2/bin/llama-server \
  -m model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8765 \
  -ngl 99 \
  --ctx-size 32768 \
  --split-mode layer \
  > /tmp/qwen8765.log 2>&1 &
```

| 项 | 值 |
|---|---|
| 权重文件 | `model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` |
| 权重 SHA256 | `ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61` |
| 量化 | GGUF `UD-Q4_K_M`（Unsloth Dynamic Q4_K_M） |
| GPU 分层 | `-ngl 99`（全部层卸载到 GPU），双卡 `CUDA_VISIBLE_DEVICES=0,1`，`--split-mode layer` |
| 服务上下文 | `--ctx-size 32768` |
| llama.cpp 版本 | `0.4.0-dev (build 0, commit unknown)`，GNU 11.4.0 编译 |
| tool parser | `gemma4`（见 `candidate_execution_config.json`） |
| 运行时端口 | worker0 → `8765`，worker1 → `8766`（两进程并行） |

### 两处必须指出的不一致

**1. `host_smoke.py` 校验的二进制不是实际在跑的那个。**

| 文件 | SHA256 | 大小 | 时间 |
|---|---|---|---|
| `~/llama-build/bin/llama-server` | `5e8d6905…`（**= `host_smoke.py` 的 `BINARY_SHA`**） | 216,565,472 | 09-12 |
| `~/llama-build-avx2/bin/llama-server` | `90eae07a…`（**实际进程用的**） | 216,436,864 | 11-19 |

后果：`host_smoke.py` 现在跑仍会"通过"，因为它校验的旧二进制确实存在且未变；但它验证的不是生产推理进程所用的文件。**这项环境验证当前处于失效状态**，不能作为"运行环境已验证"的依据。

**2. `--ctx-size 32768` 与代码层 `context_limit: 16384` 不同。**

服务端给了 32768，`initial_plan.json` 的 `options.context_limit` 是 16384。即代码主动把自己限制在服务能力的一半。这解释 `context_budget.context_limit: 16384`——预算按 16K 算，不是按服务的 32K 算。留余量本身合理，但要知道差额存在：若提示某次因超限被截断，实际服务端并未到顶。

## 二、Embedding

| 项 | 值 |
|---|---|
| 路径 | `resources/embedding_model` |
| 架构 | `Gemma3TextModel`（`config.json` → `architectures`） |
| 隐藏维 | 768（`hidden_size`），输出向量 768 维 |
| 层配置 | `_sliding_window_pattern: 6`，`head_dim: 256` |
| 结构 | sentence-transformers 格式（`1_Pooling` / `2_Dense` / `3_Dense` / `4_Normalize` 四段） |
| 代码中名称 | `rag.py` docstring：`embeddinggemma-300m` |
| 调用方式 | 独立子进程 + Unix socket（`EmbeddingService`，`rag.py:76-110`） |

`sentence-transformers 5.6.1` 加载。具体 HF 仓库名与下载日期在仓库中无记录。

## 三、Reranker

| 项 | 值 |
|---|---|
| 路径 | `resources/reranker_model` |
| 架构 | `XLMRobertaForSequenceClassification` |
| `_name_or_path` | `xlm-roberta-base` |
| 隐藏维 | 768 |
| 分词器 | SentencePiece（`sentencepiece.bpe.model`） |
| 附带格式 | `model.safetensors` + `pytorch_model.bin` + `onnx/` |
| 最大输入 | 代码按 512 截断（`v2_search_adapter.py` docstring 记 `max_len 512`） |
| 降级 | `CHIMERA_RERANKER=off` 或模型缺失时回退 RRF 分数（`rag.py:371-393`） |

XLM-R base 规模的 cross-encoder，与 `bge-reranker-base` 系同构。确切模型名未记录。

## 四、Predictor

| 项 | 值 |
|---|---|
| 依赖 | `scikit-learn 1.9.0`；**未安装 lightgbm / xgboost**（实测 `importlib.metadata` 无此二包） |
| 参数文件 | `resources/thresholds.json`（17.9 KB）、`weight_calibration.json`（13.3 KB）、`weight_table_t1.json`、`weight_table_t2.json`、`t1_decision_layer.json`、`t1_decision_layer_v2.json` |
| T1 决策层 v2 | 2 特征逻辑回归：`coef=[1.2619, 1.4771]`，`intercept=-1.3880`，`n_cases=91`，`fit_on` = 10×5 折外 OOF 概率 + LLM 决策 |
| 诚实 OOF | `honest_oof_50folds.arm_rank = 0.606`，相对 baseline v1 的 `delta_rank = +0.0357`（`delta_se = 3.38`，`wins 27 / losses 11`） |
| 训练脚本 | `scripts/train_predictor.py`（`round2/scripts/train_predictor.py` 已同步） |
| 模型权重 | `.joblib` 未上传 |

`t1_decision_layer_v2.json` 里还记录了被否掉的备选方案（`rejected_alternatives`），可直接看出取舍理由。

## 五、Python 环境

`/home/hedongze/miniconda3/envs/chimera/bin/python` — Python 3.11.15

| 包 | 版本 |
|---|---|
| torch | 2.4.0+cu121 |
| transformers | 4.51.3 |
| sentence-transformers | 5.6.1 |
| langgraph | 1.2.9 |
| langchain-core | 1.5.1 |
| mcp | 1.28.1 |
| chromadb | 1.5.9 |
| numpy | 2.4.6 |
| scikit-learn | 1.9.0 |
| pydantic | 2.13.4 |
| omegaconf | 2.3.1 |

注意 `numpy 2.4.6` 与 `torch 2.4.0` 的组合——torch 2.4 官方测试对 numpy 2.x 支持有限，如遇 `_ARRAY_API not found` 类报错，优先怀疑这一组合。

## 六、QA 旁路

| 项 | 值 |
|---|---|
| 文件 | `resources/guideline_qa.jsonl`（428 行） |
| 向量 | `resources/guideline_qa.embeddings.npy`（1.3 MB） |
| 字段 | `id, task, question, answer, decision, confidence, evidence_level, grounding, support_quote, _primary_grounding` |
| 来源 | **未记录**。`guideline_dataset/qa/guideline_qa.jsonl` 只有 20 行、字段更少，不是同一份。428 条的生成脚本不在 `guideline_dataset/scripts/` 五个脚本中 |

## 七、知识库身份

| 代 | 路径 | records | vectors | knowledge_snapshot_id |
|---|---|---:|---:|---|
| 生产（旧） | `resources/guidelines_db`（ChromaDB） | 2889 分片 | — | — |
| v1 | `guideline_dataset/rechunk_20261124/` | 1288 | 996×768 | `13fbd478…` |
| v2 | `.../rechunk_20261124_v2/` | 1372 | **1080×768** | — |
| v2r1 | `.../rechunk_20261124_v2r1/` | 1037 | 1037×768 | — |

`13fbd478…` 与 `host_smoke.py` 的 `KNOWLEDGE_SHA` 一致，说明 A 线实验用的是 v1 这一代的指纹。

**生产仍加载旧库**，v1/v2/v2r1 均未切流，详见 `../c_line_analysis/C_LINE_RETRIEVAL_DIVERGENCE.md`。

## 八、仍需补的

| 缺口 | 影响 |
|---|---|
| embedding / reranker 的 HF 仓库名与下载日期 | 换机器无法精确重建 |
| Predictor 各任务实际模型类型与特征列表 | 只能从 `t1_decision_layer_v2.json` 反推 T1 |
| chat template 文件本体（`TEMPLATE_SHA = 55d49314…`） | 未上传，无法核对模板内容 |
| 428 条 QA 的生成链路 | C 线旧库唯一缺源码的环节 |
