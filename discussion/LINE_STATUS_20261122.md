# CHIMERA 分线验收摘要（2026-11-23）

现行执行与回报格式见 [A/B/C/D 分线任务规则](LINE_TASK_RULES.md)。自动派发、续派及自主启动授权已撤销。就绪任务仅标记 `READY_TO_DISPATCH`，由用户明确启动。阶段完成度以各线验收范围为准。

| 研究线 | 本轮验收 | 后续门槛 |
| --- | --- | --- |
| A 自主决策 | 中立说明和角色隔离清单已回读。表单决策页还动态渲染有关字段，局部摘要不足以确定历史可见视图，字段验收 PARTIAL。 | 先核字段与裁定视图；病例放行仍需独立资格、角色隔离及可见性凭证。 |
| B 来源与时间 | B-DATA-SEMANTIC-FIX 独立 PASS。原 18 槽复跑完成，54 对访问记录闭合。修正后的差异对为 T1 78/85、T2 74/87。 | 局部修复完成。真实同次冲突、后验可达及纵向资格仍未知；M1 待验、M2 HOLD。 |
| C 检索与证据 | 投影设计确认了临床输入与目标的文件角色分离。工具摘要的等价见证仍需核对，首屏只可声明按合同重建。 | 可先确认制包的最小字段与见证口径。实际制包、原查询复跑及生产仍 HOLD，重排与 NLI 另有门槛。 |
| D 外部数据 | 三个公开候选中两个拒绝、一个 HOLD_UNKNOWN、零 ADMIT。PI-CAI、SEER 在本轮核对中缺对应的 T1/T2 决策目标。 | TCGA-PRAD 仅保留为潜在的 T3 受限对照。时间和删失合同、输入表征与独立性待证，D02 实施 HOLD。 |

PI-CAI 的公开开发集含 PROSTATEx 病例，不能将两者相加作为独立来源。SEER 的治疗记录限于已实施治疗的信息及其完整性范围。GDC 的数据使用条件按具体数据类别核实。[PI-CAI 发布方说明](https://github.com/DIAGNijmegen/picai_labels/blob/main/README.md) · [SEER 治疗字段说明](https://seer.cancer.gov/data-software/documentation/seerstat/nov2023/treatment-limitations-nov2023.html) · [NCI GDC 访问政策](https://gdc.cancer.gov/access-data/data-access-policies)

生产候选仍为受保护 M0。本次独立验收只读取既有工件，未启动后续任务。
