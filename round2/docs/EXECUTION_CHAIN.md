# 当前执行链与改动边界

> 2026-11-13接续：最新状态及信息／成本缺口见[当前交接](ROUND2_CURRENT_STATE_20261113.md)，后续顺序以[阶段总计划](ROUND2_MASTER_PLAN_20261113.md)为准。本文保留入口与首轮重构证据；组件审计事件不等于预算账本已实现。

2026-11-11 建立。以实际代码、运行配置和产物为依据；历史计划、材料中的生产开关仅作待核对证据。

## 入口与模块身份

- 本地：`python -m chimera_agent_baseline.run` → Hydra 配置 → `run_agent()`。
- 提交：Dockerfile 的 `python inference.py` → socket 文件适配 → 同一个 `chimera_agent_baseline.run.run_agent()` → 输出 socket。
- 统一包名 `chimera_agent_baseline`，提交入口不再同时导入 `src.chimera_agent_baseline` 副本，避免相同代码拥有两套模块级状态。
- 实验只通过明确的 config/env 改变流程，每组单独输出目录；`configs/experiment/` 与 `tools/run_round2_night.py` 属实验入口，不是提交入口。
- `src_afix/`、备份、AppleDouble 和历史日志不属于该入口的正常 Python 模块路径。静态未引用不等于可删除；本轮只盘点并从 Docker 构建上下文排除历史/元数据，未移动源文件。

执行链：

```text
病例输入 → planner（可选）→ ReAct ↔ MCP 工具
         → predictor 节点 → 表单生成与解析
         → 核验 → 决策修正 → 数值/权重/置信度校准
         → schema 装配 → prediction.json
提交入口额外执行：reasoning_align + 平台字段过滤 → decision/reasoning sockets
```

## 表单拆分

| 模块 | 当前职责 |
|---|---|
| `agent/form_fill.py` | 表单提示、模型调用/重试、核验编排、最终装配；保留旧 helper 导入和规则函数兼容历史脚本 |
| `agent/form_parsing.py` | JSON 提取、正文选择、合法决策抢救与原默认值；从旧文件机械提取 |
| `agent/postprocess.py` | 明确的后处理执行顺序与阶段记录；依赖通过 `PostprocessOps` 显式传入 |
| `runtime_audit.py` | 组件状态、阶段快照、严格模式失败策略，不包含诊断规则 |

这是第一轮拆分，尚未将所有旧规则/校准函数搬出 `form_fill.py`；约 2401 行减少至约 2060 行。先建立可验证接口，再逐块继续整理，避免一次性搬动全部规则和资源缓存。

## 资源来源

`paths.data_root/resource_dir/model_dir/embedding_model_dir` 来自 Hydra；提交入口将其改为挂载/镜像路径。部分旧 helper 仍从代码所在仓库的 `resources/` 读取系数和权重表，并不全服从 `paths.resource_dir`。本轮保留这些行为，manifest 记录配置路径与实际系数路径/哈希，让差异可见；尚未统一全部资源解析。

`run_manifest.json.execution_identity` 记录 Python、关键源码/预测器/决策系数的路径和 SHA256、配置路径、实际 T1 决策层模式和严格开关。没有把历史材料里的 `T1_DECISION_LAYER=0` 自动写成服务器默认；服务器原 v2 默认保持，v15 精确运行配置仍待核实。

## 审计产物与严格模式

- `<output>/runtime_components.jsonl`：组件的 configured、status、reason、fallback、required，以及可用的任务/病例/资源来源。覆盖预测器加载/推理、病例工具取证、RAG/QA dense、重排器及表单/决策/档位表主要降级路径。
- `<output>/taskN/<case>/processing_audit.json`：parsed_judgment、verified_judgment、predictor_override、T1 决策层、T2 floor、T3 event/time、权重/置信度、最终装配的前后值与对应 warning。异常前已写入的阶段仍保留。
- `trace.json.processing_stages`：成功病例的同一阶段序列；不加入提交 schema。

默认 `CHIMERA_STRICT_RUNTIME=0`：保留既有抢救、默认诊断和资源回退，只增加审计。

显式 `CHIMERA_STRICT_RUNTIME=1`：关键组件降级、默认/抢救诊断、必要资源缺失会失败；病例预测不完整使实验以异常结束。故意关闭重排器应使用 `CHIMERA_RERANKER=off`，记录为 disabled，不等同于“配置开启但加载失败”。MCP 显式传递严格开关及输出目录。

该覆盖不是对所有历史 helper 的全面认证：原子分解内部的细粒度 fallback、每个旧字段解析器/资源 loader 的全部路径，仍需逐项核查。严格模式也不能替代输入语义、患者独立性或临床证据质量验证。

## 打包检查

Dockerfile 显式 COPY `reasoning_align.py` 并加入 import gate；`.dockerignore` 排除历史数据/产物、备份及 AppleDouble。`tools/check_submission_imports.py` 按 COPY 声明隔离源文件和 import-time JSON 资源，验证导入来自暂存目录及入口使用同一个 runner。

这是主机依赖下的隔离导入检查，不是实际 Docker build/run。当前服务器没有 Docker/Podman，CUDA、依赖 ABI、离线模型加载与 Phase 2 容器端到端仍未验证。
