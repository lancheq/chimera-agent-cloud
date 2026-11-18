# A02｜Reflection历史证据与答案消费链审查

2026-11-18完成本轮有限只读任务；沿用任务卡20261117产物名。状态：`A02_DELIVERED_AWAIT_PLANNING_REVIEW`。本轮仅交材料；生产修复、G1R集成验收与临床有效性均未完成，默认Reflection保持现状。无模型端点调用、真实临床生成、GPU、训练、上传、跨会话消息或新代理；无需等待后台任务。

结论：G0已证实外部`refined_transcript`未被填表消费，本轮直接复用。该工程缺口不能证明历史曾提出医学正确改判。30例原始证据组没有标签改判的既有结论保持；本轮定向4条历史链也未找到“医学正确纠正却未消费”的真实正例，该命题仍未知。独立审核研究链中，接纳与写出能够闭合，不能把审核拒绝误称写回失败。

## 复用与新增证据

先查输出目录和报告，均不存在，故本轮新建，没有覆盖旧A02产物。先读AGENTS、G0规划审查/机制清单及三份历史复核报告；未重跑6例分歧、47次早停、30例全量生成、120分支或106次审核。机械消费工程没有另开文献综述。

| 问题 | 结论与证据等级 |
|---|---|
| 外部reflect是否送达填表 | 已回答，复用[G0审查](ROUND2_P01_G0_PLANNING_REVIEW_20261117.md)及[G0消费定位](../output/p01_mechanism_acceptance_20261117/entry_chain.json) S13/S14；不再重证整个入口 |
| 30例证据组是否提出标签纠正 | 没有；复用[机制价值报告](T1_REVIEW_MECHANISM_VALUE_20261112.md)，不因消费缺口改口 |
| 固定提议与审核的分母 | 60份旧提议×2契约=120策略分支；每契约7份程序引用拒绝、53次真实审核，共106次；无新患者/新诊断。复用[固定审核分析](T1_AUDIT_CONTRACT_FIXED_RESULT_20261112.md) |
| 有提议但保留原值是否写回失败 | 本轮4链的拒绝版本均完整等于原输出，符合合同；不是消费失败 |
| 标签不变是否说明没有消费 | 否。两条同标签链在typed接纳后，free_text与variable_weights确实进入研究final_output |
| 是否有正确纠正被吞掉 | 本轮没有支持链。原报告已判错误的反转提议不能充当正例；同标签修改的医学正确性不另裁定 |
| 后处理覆写是否解释这些研究结果 | 不能。这些研究runner不走生产form_fill/postprocess/align。G0末端覆盖事实单独保留，不作逐例因果归因 |

详细的已回答/未回答矩阵与固定分母见[gap_matrix](../output/planning_A02_reflection_20261117/gap_matrix.json)。原始30例研究是30名触发患者各两组、60份生成提议；总队列91例、未触发61例保留。上下文组2份改判均为原报告所评分的错误改判，历史放行1份；证据组0份改判。历史真实审核合成筛查10份夹具的新/旧10/10与8/10是工程关系检查，21次历史请求含格式重试，本轮没有重跑，不能当医学准确率。

## 4条定向链的增量核查

选择依据为既有报告已具名的过程状态：明确反转且审核状态不同、保持标签且理由/权重改变、过程意见归属被拒后接纳。未加载GT文件、未按GT搜索正确正例；4链涉及2位既有患者，不是新增评测分母。每链跟踪原始生成、旧审核匹配重跑、新审核匹配重跑三份版本，共12份存量结果；这些版本不算12名患者或12次本轮生成。

| 私有链代号 | 原生成 → 旧审核匹配重跑 → typed匹配重跑 | 实际消费结论 |
|---|---|---|
| C01：明确反转提议 | 拒绝 → 接纳 → 拒绝 | 接纳版本确实写出反转候选；typed以缺失资料反向推断为由拒绝，拒绝版本完整保留原值 |
| C02：明确反转提议 | 接纳 → 拒绝 → 拒绝 | 原生成版确实写出反转；匹配重审拒绝完整保留原值。不能把波动当作写回失败 |
| C03：同标签，理由/权重修改 | 拒绝 → 拒绝 → 接纳 | typed接纳候选完整写入研究final_output；revised=false仅表示标签没变 |
| C04：同标签，涉及过程意见归属 | 拒绝 → 拒绝 → 接纳 | 同上；不能从审核接纳直接推出修改医学正确 |

12份版本的case/提议/来源/过程上下文digest绑定及完整final_output检查均通过；接纳版本完整等于审核输入的候选，拒绝版本完整等于packet原输出。两条证据组typed接纳版本均改变free_text与variable_weights；标签相同没有掩盖字段消费。两条明确反转提议沿用原报告的错误评分，未重新作医学判定。4链过程证据已饱和，主动收束，未凑满6链。

逐链路径、输入、结果、日志SHA及检查保存在私有[historical_chains_private.json](../output/planning_A02_reflection_20261117/historical_chains_private.json)，此报告不复制患者原文、标签或逐例临床内容。研究`result.json.final_output`闭合不等于生产最终两个socket闭合；对应生产段标`NOT_RUN_IN_THIS_RESEARCH_PATH`。

## 机制角色与身份边界

外部graph reflect输出自由文本；form-fill内层self-refine返回结构化judgment并继续atomic/后处理；医学审核负责来源和内部一致性门禁；predictor分歧只决定复核机会；legacy后处理及align是独立输出变换。五者分别列于[mechanism_roles](../output/planning_A02_reflection_20261117/mechanism_roles.json)。S4 v2对其关闭不能当作其效果实验，默认关闭也不是永久删除许可。

历史事实绑定两批各自`output/t1_disagreement_review_20261112_ready/snapshot/`与`output/t1_audit_contract_fixed_20261112/snapshot/`。原始runner的`adjudicate`、重审核runner的`assess_review`及其配置、协议、输入、模型日志和产物均定位；不导入或执行历史模型代码。冻结源码明确拒绝保留原值、接纳完整候选且之后不加临床后处理。内层self-refine只读静态返回/异常路径，未补逐例日志，不能据注释中的旧bug数字认定本轮发现了对应历史效果。

[input_manifest](../output/planning_A02_reflection_20261117/input_manifest.json)记录59个定向输入身份，其中25项能与所用历史冻结表核对，均MATCH；其余34项是本轮计算SHA、未在该次所用冻结表注册，不能称为已完成历史644项全复核。G0身份与其既有独立验收结论直接复用。当前HEAD观察为`7301642d0eedda2f143ba44b93d42f948249d813`；当前文件身份另存[baseline_identity](../output/planning_A02_reflection_20261117/baseline_identity.json)，只代表本轮读取时状态，不用于倒推历史或指控并发G1A变化。

## 最小方案及验收契约

仅一套方案：[repair_contract](../output/planning_A02_reflection_20261117/repair_contract.md)。复用既有schema、primary/process分型及digest门禁，增加无模型候选选择器和消费回执。状态逐步追溯：初始判断 → 检测/带据提议 → 审核接纳/拒绝/未知 → 授权候选 → 唯一form-fill选择与真实消费 → atomic/postprocess → align/投影 → 两个最终字段文件。每段记录真实值与摘要，拒绝和异常完整回退原判断；原值非法则失败，不默认生成诊断。

外部自由文本本身不能无条件写回临床字段。维持“不改Prompt、不增调用”时，缺完整结构候选或绑定审核就必须显式保留`detected_no_executable_proposal/blocked_missing_authorization`。规划若要求其实际产生临床改判，须先冻结可复用的结构与审核来源；不能把仅读到文本称为消费修复完成。内层schema合法也不自动等于医学审核通过。此未决项在契约中保留，不静默禁用或替换机制。

后处理/align既有行为不在A02改变。获批覆写记`accepted_then_overridden`；未授权覆写/丢字段记`consumption_contract_failed`。原候选审核不能冒称覆盖变更后的最终结果；不以最终分数否认上游修改。G1A审计接口由主会话统一映射，A02只提出最小事件字段，不创建竞争性生产接口。

[synthetic_regression_matrix](../output/planning_A02_reflection_20261117/synthetic_regression_matrix.json)设计18行及参数分支，覆盖正确提议接纳、错误提议拒绝、仅检测、无问题、无变化、审核拒绝、格式错误、审核异常、身份/摘要失配、末端覆写/丢失和异常回退等。夹具只用A/B及虚构来源，正确性来自测试oracle，不外推医学。状态为`DESIGN_ONLY_NOT_EXECUTED`；未运行纯函数演示或集成测试。G1R再由原主会话执行。

建议改动范围为一个契约模块、graph artifact出口、form_fill唯一选择点、既有旁路审计及后处理/align边界适配和合成tests。估算工程3–5小时，接口冻结及模型/医学门槛另计；无需新研究算法、资产或环境。A02没有实施这些改动，也没有向主会话派发G1R。

## 资源、变更与停止

新增文件仅在指定A02输出目录与本报告；不写CURRENT/INDEX、生产实现、历史冻结、配置、标签或Git状态，不运行checkpoint/公开同步。审计脚本为stdlib单工作进程、观测1线程，设置2GiB地址空间上限。审计worker测得CPU约0.073秒、峰值RSS 15MiB、定向检查墙钟约0.025秒；这些数字只覆盖该worker，早期目录/文本读取CPU未计量，不冒充整个会话CPU。完整指标见[audit_worker_metrics](../output/planning_A02_reflection_20261117/audit_worker_metrics.json)。

资源偏差如实记录：初始发现阶段曾一次并行提交3个短只读shell查询，超过本卡1进程要求；发现后所有查询与审计均串行，未启动后台任务。没有模型/临床API/检索端点调用、GPU、训练、安装、下载或研究代理。最初日志手录开始时间有误，随后追加更正为Goal记录对应的北京时间2026-11-18 10:17:54；不以错误值估算工作量。执行模型只记录用户要求的gpt-6.1-sol/high，未取得运行时模型身份回执，不冒称已核验。

交付摘要、产物hash、最终身份复核及未解决项见[handoff.json](../output/planning_A02_reflection_20261117/handoff.json)。本有限Goal在交付后停止；后续由规划审查，再决定G1R，不自动启用Reflection或追加临床实验。没有后台任务，无需等待或安排重新唤醒。
