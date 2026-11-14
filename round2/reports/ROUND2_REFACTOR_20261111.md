# 第一轮保持行为的整理：验收记录

本轮按 `ROUND2_PRIORITY_RECONCILIATION_20261111.md` 与用户新优先级执行。旧计划不作为强制路线；先完成基础整理，不并行展开多个提分方向。

## 运行准备与工程验收

完成解析 helper 机械提取、后处理执行顺序独立化、阶段快照、主要降级状态记录和 opt-in 严格实验模式。默认诊断规则、阈值、提示、决策层系数、预测器与输入字段恢复逻辑未调整。旧 helper 保留兼容入口，历史资产未搬动/删除。入口及资源来源见 `docs/EXECUTION_CHAIN.md`。

提交入口改用唯一包名，Dockerfile 显式复制 `reasoning_align.py`。隔离 COPY/import 检查对旧 Dockerfile 返回 1，明确复现 `ModuleNotFoundError: reasoning_align`；新 Dockerfile 返回 0，并验证 runner 身份一致。这不是实际容器验收。

源码静态盘点识别 23 个可达本地模块及 52 个 AppleDouble 文件（不包含忽略目录中的副本）；报告位于 `output/refactor_20261111/execution_surface.json`。普通 compileall 会尝试编译 AppleDouble 并因 NUL 字节失败；排除 `/\._` 后真实 Python 源码编译通过，Docker 构建上下文也排除了这些元数据文件。没有据静态未引用就删除代码。

测试：`PYTHONPATH=src /home/hedongze/venvs/tabpfn/bin/python -m pytest -q tests`，35 项通过；有一个旧校准函数在合成 fixture 上触发的 Pydantic enum 序列化 warning。测试覆盖三任务默认诊断、严格拒绝 fallback、核验与改诊断分离、预测器损坏、重排器显式禁用/缺失、embedding socket 缺失、未知病例工具错误，以及严格模式下不完整实验报错。原有图路由和真实 MCP 环境传递测试保留。

## 初步筛查：离线重放行为等价

工具：`tools/verify_refactor_replay.py`。比较当前重构前源文件与重构后实现，对双方输入同一份保存的初始 judgment、工具顺序、transcript、predictor 结果及病例字段；使用脚本模型返回该 judgment，不生成新判断、不读临床标签计算准确率。固定 reveal 时间戳；此次关闭原子核验，核验阶段的顺序/隔离另由合成测试覆盖。

- 昨晚四臂 364 份 T1 judgment：输出、warning、表单调用提示 0 处差异。
- 三任务历史 429 份 judgment（197/156/76）：输出、warning、表单调用提示 0 处差异。
- 最终复核：`output/refactor_20261111/replay_final_all_tasks/report.json`。

这是重构等价检查，不是重现历史 LLM 生成，也不代表 429 个独立患者或真实泛化验证。严格模式刻意改变失败路径，单独验证，不声称其与默认 fallback 等价。

## 真正的提分研究

本轮没有新调用真实 LLM，没有新架构/新 prompt 试验，没有宣称准确率提高。昨晚四组是已接触旧输入上的开发筛查；Phase 2 prompt 中关键字段缺失的变化仍需通过合法工具取证验证，不能用本轮离线一致性替代 A3/A4 验收。

后续每次只选一个机制，固定触发条件、输入来源、主要指标和副作用指标：先解释作用路径，再做可重复的单变量验证；有效才合入，证据不足或无效则记录并停止该候选，而非为了得到正结果无限调参。下一个研究单元应在已确认的 Phase 2 执行基线上，拆清取证→LLM 判断→决策层修正→平台输出的贡献，优先处理字段实际可达性与决策层交互，而不是继续扩大 prompt/架构搜索。

## 未验证与保留事项

没有 Docker/Podman，未完成真实镜像构建/运行；未复现 v15 精确代码/环境；未完成 Phase 2 全量真实推理、完整 judge-on 评测或 OOF 泛化验证。`form_fill.py` 仍包含不少旧规则，部分资源仍使用旧相对路径；本轮建立边界和追踪，没有声称全部清理完毕。弃用文件仅列为后续归档候选，原始材料保持不变。
