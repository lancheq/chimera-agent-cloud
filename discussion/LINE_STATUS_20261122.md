# CHIMERA 分线验收摘要（2026-11-22）

现行执行与回报格式见 [A/B/C/D 分线任务规则](LINE_TASK_RULES.md)。本次为研究状态汇总，阶段完成度以各线验收范围为准。

| 研究线 | 本轮验收 | 后续门槛 |
| --- | --- | --- |
| A 自主决策 | 原策略短路候选因未找到满足条件的历史病例而停止。T1 活检目标已核到采集表单，盲审材料准备完成，独立盲审未运行。历史基准分数按原标签保留。 | 独立临床审阅资格、角色访问隔离和中立说明通过后方可审阅。原实机预算已撤回。 |
| B 来源与时间 | 原范围内的语义复核已验收，另有失败尝试的审计缺口。受保护 M0 为基线，M1 待验，M2 HOLD。 | 同次冲突、后验可达和纵向资格保持 UNKNOWN，合成覆盖与实际发现分开。 |
| C 检索与证据 | 冻结请求基线和来源资格检查已验收。后续隔离候选限定一条合格分级推荐的正文映射。 | 需另审映射、重复与误拒。隔离对照、重排和 NLI 尚未启动。 |
| D 外部数据 | 三个公开候选中两个拒绝、一个 HOLD_UNKNOWN、零 ADMIT。PI-CAI、SEER 在本轮核对中缺对应的 T1/T2 决策目标。 | TCGA-PRAD 仅保留为潜在的 T3 受限对照。时间和删失合同、输入表征与独立性待证，D02 实施 HOLD。 |

PI-CAI 的公开开发集含 PROSTATEx 病例，不能将两者相加作为独立来源。SEER 的治疗记录限于已实施治疗的信息及其完整性范围。GDC 的数据使用条件按具体数据类别核实。[PI-CAI 发布方说明](https://github.com/DIAGNijmegen/picai_labels/blob/main/README.md) · [SEER 治疗字段说明](https://seer.cancer.gov/data-software/documentation/seerstat/nov2023/treatment-limitations-nov2023.html) · [NCI GDC 访问政策](https://gdc.cancer.gov/access-data/data-access-policies)

生产候选仍为受保护 M0。本次更新不启动新试验、模型运行或平台操作。
