# CHIMERA 分线验收摘要（2026-11-22）

现行执行与回报格式见 [A/B/C/D 分线任务规则](LINE_TASK_RULES.md)。自动派发、续派及自主启动授权已撤销。就绪任务仅标记 `READY_TO_DISPATCH`，由用户明确启动。阶段完成度以各线验收范围为准。

| 研究线 | 本轮验收 | 后续门槛 |
| --- | --- | --- |
| A 自主决策 | 原策略短路候选因未找到满足条件的历史病例而停止。T1 活检目标已核到采集表单，盲审材料准备完成，独立盲审未运行。历史基准分数按原标签保留。 | 独立临床审阅资格、角色访问隔离和中立说明通过后方可审阅。原实机预算已撤回。 |
| B 来源与时间 | 原选槽和输入身份已验。`psav` 分类、`enc_date` 日期角色和同序列双向重复使原差异率无效。B-DATA-SEMANTIC-FIX 为独立 `READY_TO_DISPATCH`。 | 同槽修复、零生成复跑和独立验收后才关闭该项。M0 为基线、M1 待验、M2 HOLD。 |
| C 检索与证据 | 冻结请求基线和来源资格已验。C04-1 的隔离 CPU 映射与软件消费者对照为 `READY_TO_DISPATCH`。 | 仅增加一条合格映射，检查送达、条件未知、重复与误拒。无需模型或生产审批。C04-2 重排与 C04-3 NLI 另保留各自证据门。 |
| D 外部数据 | 三个公开候选中两个拒绝、一个 HOLD_UNKNOWN、零 ADMIT。PI-CAI、SEER 在本轮核对中缺对应的 T1/T2 决策目标。 | TCGA-PRAD 仅保留为潜在的 T3 受限对照。时间和删失合同、输入表征与独立性待证，D02 实施 HOLD。 |

PI-CAI 的公开开发集含 PROSTATEx 病例，不能将两者相加作为独立来源。SEER 的治疗记录限于已实施治疗的信息及其完整性范围。GDC 的数据使用条件按具体数据类别核实。[PI-CAI 发布方说明](https://github.com/DIAGNijmegen/picai_labels/blob/main/README.md) · [SEER 治疗字段说明](https://seer.cancer.gov/data-software/documentation/seerstat/nov2023/treatment-limitations-nov2023.html) · [NCI GDC 访问政策](https://gdc.cancer.gov/access-data/data-access-policies)

生产候选仍为受保护 M0。本次更新只准备两项任务，均未派发或执行。
