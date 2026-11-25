# Docker 打包前置审计清单（0830/0831 合并 · GATE 补作业）

> 生成: 2026-08-31（任务 TASK-0831-M1 Phase 2）
> 模式: L0 静态审计，零推理、零构建、零改码
> 代码基线: HEAD = `190c903`（P6）+ `13d8b1e`（P5）+ `08c6e73`（config 排雷）
> 读到的即最新态（P5/P6 已 commit）

---

## 0. 结论摘要

| 项 | 结论 |
|---|---|
| 非 root 运行 | ✅ Dockerfile L10/L45 `USER user` |
| 逐 case 调用 | ✅ ENTRYPOINT=inference.py，1 容器调用=1 case |
| 模型进镜像 | ⚠️ 当前 `COPY model/ → /opt/ml/model`（仅 GGUF）；官方另支持 tarball 单独上传 |
| 依赖一致性 | ⚠️ 容器以 requirements.txt 为准；.venv 有 15 个版本漂移（.venv 更新） |
| GPU 约束 | ⚠️ 官方 T4(16GB)/A10G(24GB)；**35B 任何量化都放不下 T4**；Q4_K_M 在 A10G 上勉强/有 OOM 风险 |
| τ 硬编码 | ✅ TAU=24.0（"train-calibrated; do NOT retune"）保持 |
| T3-004 兜底 | ✅ 在 form_fill 主流程（容器路径自带） |
| config 排雷 | ✅ decision_override_tasks=[] + predictor.enabled=false |
| predictor 排除 | ✅ 运行时 0 加载；建议构建时排除 model/predictor/（~1.9MB） |
| T2 数字混入 | ⚠️ 61.1% 为 P6 前数字，提交配置需 P6 后重跑（待办） |

---

## 1. D1 · Dockerfile_Baseline 审计

### 1.1 用户（官方要求非 root）✅
- Dockerfile L10 `groupadd -r user && useradd -m --no-log-init -r -g user user`；L45 `USER user`。
- 非 root 运行已满足。

### 1.2 ENTRYPOINT / 逐 case ✅
- L47 `ENTRYPOINT ["python3", "inference.py"]`。
- `inference.py`：读 `/input/inputs.json` → 按 clinical-data socket slug 识别 task（T1/T2/T3）→ 把 socket 物化为单个 `gc-case` 的 `task<N>/agent_input/<case>/` 树 → `run_agent`（同一套 model+MCP+ReAct+form_fill）→ 写 `/output` 的 decision + reasoning 两个 socket。**1 次容器调用 = 1 case**，GC 平台聚合。

### 1.3 模型 20.6GiB 进镜像方式 ⚠️（关键）
- Dockerfile L43 `COPY --chown=user:user model/ /opt/ml/model/`。当前 `model/` 内容：`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`（20.6GiB）+ README + `predictor/`（~1.9MB joblib）。
- **官方另支持**（model/README.md）：模型可作为独立 tarball（.tar.gz）在 GC 平台「Your algorithm > Models」上传，运行时自动解压到 `/opt/ml/model/`。→ 大模型进镜像的两种路径：COPY（镜像 ~21GB+）或 tarball（镜像小，模型单独传）。
- 加载链路：`inference.py` 把 `paths.model_dir` 覆盖为 `/opt/ml/model`；`models/__init__._resolve_model_path` 目录非空 → 返回**目录**给 vLLM（`LLM(model=/opt/ml/model, ...)`）。
- **风险 R1（vLLM×GGUF）**：vLLM 0.25 从**目录**加载单个 `.gguf` 的自动识别行为需验证（通常要求 model 参数直接指向 `.gguf` 文件路径或目录含 config.json）。构建后必须做单 case 冒烟确认。
- **风险 R2（容器 config 与 35B 不匹配）**：容器加载 `configs/config.yaml` 默认值——`model.provider=vllm`、`model_id=google/gemma-4-E2B-it`、`tool_parser=gemma4`、`max_model_len=32768`、`gpu_memory_utilization=0.9`。这是挑战模板默认（Gemma-4-E2B-it 2B）。**与 35B 提交不符**：tool_parser=gemma4 与 Qwen 工具格式不匹配，ReAct 工具调用在容器内可能失效。打包前必须覆盖容器 config（谁改/何时改 = 决策项）。

### 1.4 依赖一致性 ⚠️
- 容器以 `requirements.txt` 为准（Dockerfile `pip install -r requirements.txt`），46 个全 pinned；`pip install --no-deps .` 装包。
- .venv 与 requirements 比对：**15 个版本不一致**（.venv 均更新）：hydra-core 1.3.5vs1.3.3、langchain 1.3.14vs1.3.11、langchain-core 1.5.3vs1.4.8、langchain-openai 1.4.3vs1.3.3、langgraph 1.2.10vs1.2.6、langgraph-checkpoint 4.2.0vs4.1.1、langsmith 0.10.17vs0.9.1、narwhals 2.24vs2.22.1、onnxruntime 1.28vs1.27、pymupdf 1.28.2vs1.27.2.3、sentence-transformers 5.7vs5.6、transformers 5.15vs5.12.1、uuid_utils、xxhash、kubernetes。
- .venv 缺 `lifelines`（requirements 有；仅训练侧用，容器装了无妨）。
- 影响：容器行为可复现（以 pinned 为准）；本地/容器小版本漂移记录在案，冒烟时留意。

---

## 2. D2 · GPU / 量化风险定量（本任务最重要产出）

### 2.1 官方 GPU 明示
- `docs/models.md` L183-184：「Grand Challenge offers **T4 (16 GB) or A10G (24 GB)**」。
- 官方评估侧 README（data_backup/upload_evaluation_official/…/evaluation/README.md L73-74）：judge `gemma4:e4b` ~10GB、「intended for a **24 GB-class GPU**」——官方评估环境按 24GB 类 GPU 设计。
- repo 内**未找到**「测试集 GPU 型号固定为哪种」的更明确指定 → 视为调度器在 T4/A10G 间分配，提交方不可选。

### 2.2 当前模型与备选
- `model/` 仅有 `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`（22,134,528,992 B ≈ **20.6GiB**）。全盘搜索**无 Q3_K_M / IQ4_XS 备选**（仅 ollama 的 gemma-4-E4B judge，非候选）。
- 架构（GGUF 元数据实测）：`qwen35moe`，40 层 hybrid MoE+SSM，`full_attention_interval=4`（→10 层全注意力），`head_count_kv=2`、`head_dim=256`，`expert_count=256 / used=8`，`context_length=262144`。

### 2.3 显存测算（vLLM，估算）
| 分量 | Q4_K_M @32k | Q4_K_M @16k | Q3_K_M @32k |
|---|---|---|---|
| 权重 | ~20.6 GiB | ~20.6 GiB | ~15.0 GiB |
| KV+SSM 状态 | ~0.7 GiB | ~0.4 GiB | ~0.7 GiB |
| 激活/CUDA graphs/MoE 缓冲 | ~1.5-2 GiB | ~1.5-2 GiB | ~1.5-2 GiB |
| **合计（A10G 24GB）** | **~22.8-23.3** | **~22.5-23.0** | **~17.2-17.7** |
| util=0.9 → 21.6GiB 上限 | ❌ OOM | ❌ OOM | ✅ |
| util=0.95 → 22.8GiB 上限 | ⚠️ 临界 | ⚠️ 临界 | ✅ |
| T4 16GB | ❌ 不可行 | ❌ 不可行 | ❌ 不可行（权重已 15GiB+） |

### 2.4 三方案对比表
| 方案 | 内容 | A10G | T4 | 质量风险 | 需新增下载 |
|---|---|---|---|---|---|
| **A. 仅 A10G 赌一把** | Q4_K_M + max_model_len=16384 + gpu_memory_utilization=0.95 | ⚠️ 临界/高 OOM 风险 | ❌ | ctx 16k 可能截断长 ReAct 轨迹（工具多时）；无额外质量损失（同 Q4） | 无 |
| **B. 双量化按 GPU 自选** | 镜像/模型带 Q4_K_M + Q3_K_M，运行时按显存选 | Q4 临界 / Q3 稳 | ❌ 仍不可行 | Q3 决策质量需重验（T3 阈值/决策可能漂移） | 需下载 Q3_K_M（~15GiB，无现成） |
| **C. 降级 Q3** | 仅 Q3_K_M | ✅ 稳（~17.5GiB） | ❌ | Q3 vs Q4 质量损失需全量重验（T1/T2/T3 + GEval） | 需下载 Q3_K_M |

**核心结论**：
1. **T4(16GB) 对 35B 提交不可支持**——Q4 20.6GiB 权重即超；Q3 ~15GiB 权重+KV/激活也超。三方案都只能在 A10G 上成立。
2. A10G 上 Q4_K_M 也是「临界」，vLLM 实际占用受激活峰值（MoE 路由 + 长工具轨迹）影响，运行中可能 OOM。
3. 真实选项收敛为：**A（Q4 硬上 A10G + 瘦身 ctx）/ B（Q3 保稳 A10G，需下载+重验）/ C（换能进 T4 的模型，如 config 默认 Gemma-4-E2B-it——等于换提交，质量完全不同）**。

---

## 3. D3 · τ 与后处理封装确认
- `_apply_t3_tau`（form_fill.py L648-676）：`TAU = 24.0`（L660）**硬编码**，docstring 明确「train-calibrated; do NOT retune」。→ **保持硬编码，打包不改**。
- T3-004 型兜底（form_fill.py L350-354）：`event==0 and months_to_recurrence is None → 36.0` + warning `t3_months_fallback`。在 form_fill 主流程内 → **容器路径自带**。
- config.yaml L91 `decision_override_tasks: []` + L80 `predictor.enabled: false`（commit 08c6e73 排雷）→ **打包用此版本**，无需再动。

## 4. D4 · predictor 排除确认
- `inference.py` 无任何 predictor import（仅 `rag` / `run` / `utils`）。
- `run.py` L41 模块级 `from ...predictor import Predictor`（仅类导入，不加载 joblib）；L366-370 `needs_predictor = enabled or decision_override or decision_override_tasks`——当前全 false → **Predictor 不实例化、joblib 0 加载**（与 0830 日志 "Predictor loaded" 0 命中一致）。
- `model/predictor/`（~1.9MB joblib）会被 `COPY model/` 带进镜像但运行时惰性（无泄漏通道，config 已排雷）。
- **建议**：构建时用 `.dockerignore` 或构建脚本排除 `model/predictor/`，镜像省 ~1.9MB 且物理杜绝通道。

## 5. D5 · T2 混入风险确认 + 待办
- 本审计基于 0830 代码（HEAD 含 P5+P6），**T2 全量数字 61.1% 是 P6 前**（P6 修复了 decision 兜底，会改变 fallback 率与决策）。
- **待办（提交前必做）**：P6 后 T2 重跑（72 GT，双关闭），验证 acc/F1 vs 61.1% + fallback 率（期望 21/72 显著下降）；Docker 提交配置中的 T2 数字一律用重跑后版本。

---

## 6. 分步打包计划（每步验证命令）

| 步骤 | 动作 | 验证命令 |
|---|---|---|
| 0 | 用户拍板：GPU 方案（A/B/C）+ 模型进镜像方式（COPY vs tarball）+ 容器 config 覆盖 | — |
| 1 | 准备 `/opt/ml/model` 内容（按方案；建议仅 `.gguf`，排除 predictor/） | `ls -lh model/` |
| 2 | 容器 config 覆盖（Qwen tool_parser/model 路径/ctx/util） | `git diff configs/config.yaml` |
| 3 | 依赖确认（以 requirements.txt 为准） | `pip install -r requirements.txt --dry-run` |
| 4 | 构建镜像 | `bash do_build.sh`（tag=chimera_agent_baseline_debug） |
| 5 | 镜像内 import 冒烟（CPU 侧，不跑推理） | `docker run --rm chimera_agent_baseline_debug python3 -c "import chimera_agent_baseline, src.chimera_agent_baseline.run; print('import ok')"` |
| 6 | 单 case 冒烟（需 GPU）：用 `test/input_gc` 样例 | `docker run --gpus all -v $PWD/test/input_gc:/input -v /tmp/out:/output chimera_agent_baseline_debug` → 检查 `/output/prostate-*.json` 格式 |
| 7 | 冒烟检查点：decision 值合法、reasoning 含 free_text/confidence/variable_weights/reveal_sequence、T3 months 非 None | `python3 -c "import json; d=json.load(open('/tmp/out/prostate-*.json'))"` |
| 8 | 三门槛复验：官方 evaluate.py 对容器输出评估 | `bash evaluation/do_test_run.sh task1 task2 task3` |
| 9 | 上传：模型 tarball（如选）+ 容器镜像 → GC | GC 控制台 |

## 7. 风险表

| 风险 | 等级 | 缓解 |
|---|---|---|
| A10G OOM（Q4_K_M @32k，util=0.9） | 高 | ctx→16k + util→0.95；或改 Q3（方案 B/C） |
| T4 上 35B 必 OOM | 高 | 提交声明只支持 A10G；赌调度；或换小模型（质量不可比） |
| vLLM 目录加载单个 .gguf 不确定 | 中 | 构建后单 case 冒烟（步骤 6）必做；必要时模型路径指向 .gguf 文件 |
| tool_parser=gemma4 vs Qwen 格式不匹配 | 高 | 容器 config 覆盖（决策项），冒烟验证工具调用 |
| ctx 16k 截断长轨迹（方案 A） | 中 | 冒烟抽样长 case；如截断明显改 Q3 保 ctx |
| 依赖漂移（本地 vs 容器） | 低 | 以 requirements.txt 为准；冒烟留痕 |
| Q3 质量损失（方案 B/C） | 中 | 全量重验 T1/T2/T3 + GEval 后再定稿 |
| `scripts/aggregate_predictions.py` 缺失 | 低 | 文档引用缺失（inference.py docstring），本地复建用 test/input_gc 聚合脚本替代 |

## 8. 待用户决策项（需拍板）

1. **GPU 量化方案**：A（Q4 硬上 A10G + ctx 16k）/ B（双量化 Q4+Q3 自选）/ C（仅 Q3 保稳 A10G）——T4 在三者下均不可行。
2. **模型进镜像方式**：`COPY model/`（镜像 +21GB） vs **GC 模型 tarball 单独上传**（推荐，镜像小，官方支持）。
3. **容器 config 覆盖**：谁负责把 configs/config.yaml 对齐 35B（tool_parser=Qwen/hermes、model 路径、ctx、util）——涉及改 config，本审计未代改。
4. **T4 风险声明**：是否接受「提交被调度到 T4 时必 OOM」并写入提交说明。

---

*审计完成 · 零推理 / 零构建 / 零改码（P6 为独立 commit 190c903，见 Phase 1）*
