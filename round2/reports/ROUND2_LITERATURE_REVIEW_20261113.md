# 面向当前问题的定向文献检索

日期：2026-11-13。目的：先明确已有方法如何拆问题，再构建完整候选。不是系统综述；优先核对原论文、作者代码、官方规则和本地冻结 evaluator。没有把其他任务的论文收益直接外推到 CHIMERA。RP 的引文仅作检索线索，未核实的不作为依据。

| 来源 | 核对到的方法／结论 | 对本项目的采用方式与限制 |
|---|---|---|
| [ReAct, ICLR 2023](https://arxiv.org/abs/2210.03629) | 交错推理、行动与观察；根据观察更新行动计划 | 保留规划能力，比较是否允许观察后调整。论文不是“删除 planner 必然提高”的依据 |
| [ReWOO, 2023](https://arxiv.org/abs/2305.18323) | 将规划推理与外部观察分开以减少冗余计算 | 固定预规划是有文献基础的候选；其 token 效率结果不能当本项目检查费用收益 |
| [Joint Active Feature Acquisition and Classification, NeurIPS 2018](https://papers.nips.cc/paper_files/paper/2018/hash/e5841df2166dd424a57127423d276bbe-Abstract.html) | 以成本敏感方式选择再取得特征或停止预测，联合训练分类与取证策略 | 借鉴“可见特征集合＋动作成本＋停止”的问题定义。策略训练留到 S5，不照搬其分类器或立即开展 RL |
| [MedAgentBench, 2025](https://arxiv.org/abs/2501.14654) | 通过可交互、FHIR 兼容病历环境评估临床 agent 的操作任务 | 工具调用和完成任务应在环境中实际验证；不能因 JSON 合法就认为机制有效。不是本项目真实检查价格表 |
| [Self-RAG, ICLR 2024](https://arxiv.org/abs/2310.11511) | 按需检索与自我反思；原方法训练专用反思标记 | 支持研究“何时需要查”，但当前冻结模型的提示方案不是复现训练版 Self-RAG |
| [RAGChecker, NeurIPS 2024](https://arxiv.org/abs/2408.08067)；[全文](https://arxiv.org/html/2408.08067v1) | 以声明为单位区分检索召回、上下文利用和噪声敏感性；固定生成器比较检索器 | 拆成检索质量、证据到诊断、整体 agent 三层。临床适用条件／例外需自建标注；自动声明核验也会错 |
| [ALCE, EMNLP 2023](https://arxiv.org/abs/2305.14627)；[作者代码](https://github.com/princeton-nlp/ALCE) | 分别评估回答正确性、表达与引用质量 | 将“引用可追溯／引用支持声明”与“诊断是否正确”分开，不把有引用当提分 |
| [QLoRA, NeurIPS 2023](https://arxiv.org/abs/2305.14314) | 冻结量化基座、训练低秩适配器 | 澄清量化不等于绝对不能训练；当前 GGUF 推理配置没有开展这类训练 |
| [scikit-learn 官方数据泄漏说明](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage) | 训练／预处理不能使用部署时不可得信息；测试部分不能用于拟合 | 对评测输入清理的推论：用测试 GT 选择冲突数值，会把答案信息写回输入。输入清理规则须独立于验证标签冻结 |

## 官方环境与成本：实物优先于概念

核对 [官方评测仓库](https://github.com/DIAGNijmegen/CHIMERA-agent/tree/main/evaluation)、[工具与资源要求](https://chimera-agent.grand-challenge.org/baseline-and-evaluation-repositories/)、[规则](https://chimera-agent.grand-challenge.org/rules/)。本次挑战站点部分页面直接访问 403，规则／资源要求参考检索可见摘要；精确计分以已下载冻结 evaluator 为本次依据，不能据网页摘要宣称最新评测版本完全一致。

官方检索摘要要求走官方 MCP 接口、提交资源全部本地封装且离线运行。因此“低成本 RAG vs 付费检查”应表述为动作成本差异，不是允许线上付费 API 或替代 MCP 通信。平台工具主要揭示预存信息，没有查到真实检查货币价格表。后续只能将有来源的价目与声明为研究代理的成本分别报告。

本地 `output/round2_20261110/official_evaluator/evaluate.py` 的 `cost_aware_tool_score`（1059 行起）按与医生共同打开的资料栏计算工具使用精度；并不按价格收费、不限制运行预算。此精确公式来自代码核查，不是把论文中的成本定义套在比赛上。

## 本次采用的设计判断

先把可见信息与费用记录变成执行事实，再比较固定计划与可修订计划；后面研究指南检索时先固定患者信息与生成器。这个组合是本项目提出的实施设计，须经本项目对照支持，文献只提供结构与测量方法。
