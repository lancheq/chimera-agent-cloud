# 11 月 10 日夜间服务器消融

启动时间：2026-11-10 23:09:42（Asia/Shanghai）。用户授权整晚运行；旧计划仅作参考，按现场证据调整。运行上限 8 小时，约至 11 月 11 日 07:09，实际进度以状态文件为准。

## 现场发现与处理

- “planner 导致主 system prompt 丢失”的猜想被实际 LangGraph 接线测试推翻，取消该修复/消融组。
- planner 每条输入只保留前 500 字的行为已由测试证实；新增 `agent.plan_context_chars=null` 对照完整输入。
- 真实冒烟确认 MCP SDK 默认只继承少量 OS 环境变量，未继承独立 embedding socket、RAG 开关和 PYTHONPATH。新增 `_mcp_environment()`，只传必要环境，不传模型 API 凭据；真实 MCP 子进程测试通过。
- 配置的 `model/reranker_model` 不完整，既有运行实际回退到 RRF。所有实验显式 `CHIMERA_RERANKER=off`，保留共同回退路径；本次不把修复 reranker 混入架构比较。MCP 环境修复对四组全部应用，不能与历史分数直接配对解释。
- 两张空闲 3090 已分别载入同一份 Qwen3.6-35B-A3B Q4 模型，监听 127.0.0.1:8765 / 8766。均使用 16K 上下文、q8 KV cache、单 slot。没有停止其他 GPU 任务或服务。

## 固定对照

| 组 | 模式 | planner 可见输入 | 指南工具 |
|---|---|---|---|
| A_planned_500 | 当前规划式 ReAct | 前 500 字/条 | 可用 |
| B_planned_full | 规划式 ReAct | 完整病例输入 | 可用 |
| C_react | 直接 ReAct | 无 planner | 可用，模型按需调用 |
| D_react_no_guidelines | 直接 ReAct | 无 planner | 从可选工具集中移除 |

预注册三个比较：B−A（截断影响）、C−B（移除 planner 整体）、D−C（指南工具可用性的影响）。planner 移除同时取消它内部的 few-shot 与预执行工具行为，不能将 C−B 解释为单一抽象“自主性”的因果效应。D−C 衡量工具可用性的整体作用，不按“实际是否检索”的事后子集挑选病例。

同一批已接触的 91 个 T1 有标签病例，每组重新生成预测，共计划 364 个任务-病例推理。固定 temperature 0.3、请求 seed 20261110；保留 predictor、T1 v2 决策层、原子校验、输出校准。T1 v2 默认仍会改变 LLM 决策，因此分别报告后处理前与最终决策，避免把后处理变化归因于取证策略。

这批数据被历史调参/训练使用过，few-shot 索引也来自历史 GT。结果属于开发筛查，不是 OOF 或未见测试成绩；单 seed 和多重比较的不确定性需要后续复核。

## 隔离与运行方式

运行根目录：`output/round2_night_20261110/`。

- `snapshot/`：冻结源码、配置、模板、predictor、索引、官方评测器及输出边界；大 embedding 权重以只读使用的本地路径引用。
- `cohort/`：复制的固定 91 例输入与已有标签，不随主数据目录变化。
- `protocol.json` / `frozen_hashes.json`：实验定义与内容 hash；结束时复核冻结文件。
- `work/<arm>/`：独立 cwd、embedding socket、Chroma 副本，避免并行写入和状态串组。
- `arms/<arm>/`：预测、逐例 trace、每次尝试日志、命令及显式环境配置。
- `status.json`：原子更新的阶段、PID、成功预测数、缺失病例与失败原因。

GPU0 依次执行 A、D；GPU1 依次执行 C、B。服务崩溃时仅尝试重启本次拥有的对应服务，运行中每次尝试最多两次服务恢复；每组最多两轮运行，第二轮只补缺失/失败预测。8 小时到限则终止本次的推理进程组，保留已完成产物。

不复用历史预测作为新架构结果，不修改模型/阈值，不查看新 test 标签，不作平台提交。正式开始前，直接 ReAct 与完整 planner 真实冒烟均完成；环境传递修复后 24 项测试通过。

## 自动评测与汇总

各组调用冻结的原样官方 evaluator，先校验内容 pin，再通过冻结的实际容器输出边界转换预测。第一轮关闭 rationale judge；不会把诊断分声称为完整平台分数。

自动汇总输出：

- `arms/<arm>/official_diagnostic/report.json` 和 `task1.csv`：固定全部 91 个目标，缺失预测计失败。
- `comparison.json`：官方聚合输出、逐例配对差值、修复/破坏数、探索性区间、耗时及工具使用。
- `SUMMARY.md`：四组结果总表。

成本主要用整例真实耗时。当前 agent token 计数不覆盖 planner、form_fill 和 fact-check 的全部 LLM 调用，汇总明确标记为部分 token 计数。

## 本地材料

已在 `git_auto_pending/` 发布材料收集请求，包含明确的 Mac 路径、单独暂存和文件 manifest；不进行项目代码 rsync。当前未收到本机 watcher 返回，不能声称材料已取得。

夜间后台同时监控 DONE：成功返回后将资料复制到 `local_materials/`，提取 DOCX 正文，记录 `materials_status.json`，保留“正文尚待内容审查”状态。实验本身不依赖材料通道；若本机 watcher 未运行，此通道会保持等待。
