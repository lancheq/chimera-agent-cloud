# P01-G1R｜已授权结构候选的Reflection消费修复与合成验收

[公开版省略逐例记录；汇总与机制结论保留。]

## 有限Goal

在G1A已验收的事件、计量和私有证据接口上，复用A02最小契约，完成“完整结构候选→绑定审核→唯一选择→真实填表赋值→后处理/align→最终sockets”的工程消费闭环，用无患者合成/假模型证明接纳、拒绝、无变化和异常均可追踪。

**本Goal不声称修好外部自由文本的临床纠正生产能力。** 当前缺完整结构提议或绑定审核时必须保留明确blocked状态、合法原判断和未完成项，不能补造授权。真实自然触发、正确性和收益未验；不能把读到了artifact或18项测试通过写成Reflection整体完成。

不启用生产Reflection、不改默认开关、不部署/调用真实模型、不增真实临床批次、训练/下载/构建/上传/平台均0。本阶段只实现既有方案消费工程，无需重做文献综述，不新增医学规则或策略。

## 输入与前置

工作根 `/home/hedongze/CHIMERA-agent`。先完整读AGENTS最新规则、本卡和：

- reports/ROUND2_G1A_A02_PLANNING_REVIEW_20261118.md。
- output/p01_g1a_common_runtime_20261117/handoff/runtime_contract.md、manifest/actual_version.json及handoff。
- output/planning_A02_reflection_20261117/repair_contract.md、synthetic_regression_matrix.json、gap_matrix.json及报告。
- 当前graph/form_fill/self_refine/evidence_review/schema/runtime_audit/entry_profile/inference/reasoning_align，按修改点定向读取。

不再读患者历史案卷/GT、不重审6例/47次、A01/C02/PACK/GIT或A02四链。G1A69源码身份以接续时核对为准，工作树HEAD不代表全量版本。记录dirty/staged、允许文件SHA和原代码快照；不覆盖无关改动、G1A/A02旧证据或冻结资源。

## 本Goal冻结的工程合同

1. 复用现有动态任务schema生成完整合法J0；字段合法null保留，必填decision缺失不能猜填。J0不可用则失败，不制造默认诊断。
2. DraftArtifact、ReflectionArtifact、CorrectionProposal、AuditVerdict、Selection、FinalReceipt分型。外部draft和后来的结构J0分别绑定，审核必须确实覆盖本次base/candidate/source/process/task/case/run/profile和拟改字段；不能事后替旧审核补上新J0摘要。
3. 只有完整schema候选、来源/取得权限和严格绑定的accepted审核，加上代码所有的明确authority，才可选择候选。审核合法与医学正确分开；测试审核为明确stub，不假装真人/LLM医学审核。研究历史回执不作生产授权。
4. 采用A02无模型纯选择器方案，不改入参、不拼补候选字段。完整接纳或完整保留J0；多候选有歧义默认ambiguous并保留J0，不自选最激进结果。候选合法不自动获得审核权。
5. actual form_fill赋值是唯一消费点，不通过把反思文本塞进messages绕过门禁。内层/外层分别产artifact；仅已明确批准的候选路径经选择器，默认/legacy行为保全并明确标未审。不得静默改变既有可用机制。
6. 缺完整提议/审核分别记录detected_no_executable_proposal或blocked_missing_authorization；disabled/not_triggered/no_issue/no_delta/rejected/error不得混用。没有真实结构生产来源的分支明确未完成，不能给它填消费成功。
7. 复用G1A账本和事件，不建另一套审计/授权/模型客户端。RuntimeFatal、身份/成本/持久化失败仍fatal，不能包成普通Reflection回退；一般提议解析/审核错误在合法J0存在时回退，并记录错误与0隐式追加调用。
8. postprocess/atomic/align现行逻辑保持。记录逐字段选中、消费、保留、覆写/丢失；现有规则存在不等于规划已授权其覆盖候选。任何未审权威或丢字段保留NOT_READY/consumption_contract_failed，禁止靠重新覆盖输出或关后处理凑通过。文件写出成功的finalized与机制验收通过分列。

## 允许修改与步骤

允许新增一个reflection_contract模块，最小修改graph state artifact传递、form_fill单一选择/赋值点，按需适配既有audit/入口最终receipt，及独立合成tests。不要改默认config、医学Prompt/阈值/预测器、数据/KB或旧frozen；若需改postprocess/align临床权威、真实输出生成流程或增加LLM生产提议/审核，停回规划。

1. 接续登记并完成小型变更清单；复用G1A接口，不重构全Agent。
2. 实现严格类型、身份/完整字段审核覆盖、纯选择器和明确状态。authorization不是模型自报的字符串或可编辑YAML；保持正式profile注册未获批准，测试注入不可放开load_model/服务启动。
3. 接入当前实际graph→form_fill路径；构造受信完整候选/审核测试输入，验证选中对象确实成为填表后续消费者输入，不只测试纯函数。
4. 将A02的S01–S18及参数分支映射为当前T1/T2/T3合法软件夹具。不得把抽象A/B当平台真实schema；夹具oracle只定义程序期望，不用患者/GT。至少涵盖同标签但理由/权重变化、多候选、错误base/draft/来源/审核覆盖、合法null、原判断非法、技术fatal。
5. 用真实填表解析/后处理/align和inference两个sockets完成有限stub集成；runner中与本修复有关的实际graph/form_fill不得整体替成写死prediction。只替模型返回/资料为无患者夹具。T1/T2/T3各证明适用字段投影；不支持的审核类型明确blocked，不直接套T1临床审核。
6. 正向证明候选在实际赋值后digest一致；拒绝/异常完整保留J0；末端覆写故障必须被识别为未满足合同。合成正向样例需有实际保留字段，不能全用末端被覆写的分支当成功。
7. 有意义专项回归及相关G1A回归通过后收束，不扩大重复测试。保存清理后证据、diff、版本、资源、已有结果保全及所有未完项。达到有限交付即停，不自动进入G1T/G2。

## 交付与完成标准

只写获批代码/测试、`output/p01_g1r_reflection_20261118/`及`reports/ROUND2_P01_G1R_REFLECTION_CONSUMPTION_20261118.md`。不得修改CURRENT/INDEX、其他任务output、public pending或上传。

交handoff.json、源码/配置及产物manifest、diff、契约字段/事件映射、S01–S18覆盖矩阵、实际图/入口stub日志、最终两sockets与阶段digest、失败/回退和保全、资源/生成调用账本。报告至少分三项：

- `AUTHORIZED_STRUCTURED_CANDIDATE_ENGINEERING_PASS/FAIL`：完整候选的真实赋值/消费是否闭合。
- `EXTERNAL_GRAPH_PRODUCER_AND_BOUND_REVIEW`：有无可用于真实路径的完整生产来源，当前不足应明确BLOCKED。
- `REAL_MODEL_AND_CLINICAL_STATUS=NOT_RUN`，整体机制仍NOT_READY。

不能把选择器/空artifact日志完成当成外部Reflection写回已修。若发现不改现有生成接口就无法满足目标，先交最小必要适配/新增请求成本供规划决策，不擅自改Prompt或模拟成有临床审核。

资源0真实生成/端点/新工具取证/GPU/训练；最多2进程/4线程/4GiB，复用环境，不装依赖。估算3–5小时为任务工作量，非硬截止；超过范围先交剩余清单，不扩架构。Plus余量未知，不派子Agent，具名小段读取、复用既有调查、记录可得使用统计但不换算账单。

停止：身份/权限/预算失败，必要来源或审核缺失，需新医学权威/真实调用/放松guard，越出修改范围或额度限制。按已知缺口交付，不以反复试Prompt找正例。后台安排须报北京时间及依据；完成后无后台任务则明确无需等待。
