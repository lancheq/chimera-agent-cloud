# P01-G1R 契约与 G1A 事件映射

| 契约字段或边界 | 生产者与事件 | 核对方式 |
| --- | --- | --- |
| DraftArtifact | graph 的外层反思，或 form_fill 内层独立 artifact | 原草稿正文摘要和消息摘要，来源标记 generated_opinion。外层自由文本单独保留摘要，不加入填表消息 |
| J0 | form_fill 的 parsed_judgment | 合成授权入口使用当次动态任务 schema，拒绝缺字段、非规范类型和必填决策空值，保留合法 null |
| 结构候选、审核和授权 | reflection_review | 同一私有事件保存合成提议与审核原始结构。提议绑定 run、task、case、profile、draft、J0、来源、过程、候选完整值和实际变化字段；审核绑定提议及其机制，覆盖候选全部结构字段 |
| 可引来源 | reflection_review 和 formfill_input | 从首条入口消息提取预展示记录区，排除指令和可申请工具列表；仅此区可作为此轮合成审核的引用来源。记录初始公开范围、摘要及准确引用。工具结果未加入本轮授权契约 |
| 选择与赋值 | reflection_selection → reflection_formfill_consumed | 至多选择一份完整候选，无授权、冲突或一般审核异常时保留完整 J0。事件记录标签是否改变、逐字段变化和候选/审核摘要。真实赋值后立即核对摘要，atomic 入口再核对一次 |
| 常规处理与落盘 | atomic_verification → 各后处理阶段 → assembled_output → graph_output → prediction_file → reasoning_align（T1/T2） | 阶段摘要链接到最终回执；同值保留，变更记录阶段、字段、之前/之后的值、存在位和有效规则 ID。空值用存在位与丢失区分 |
| 结果文件 | final_sockets | 两文件分别回读并核对值及 SHA-256。正常写出使用 finalized，另列结构候选字段结果，区分 finalized_retained、finalized_initial、finalized_initial_with_error、accepted_then_overridden、consumption_contract_failed |
| T1 字段投影 | biopsy_decision → decision；free_text、confidence、允许的 variable_weights → reasoning | reveal_sequence 来自程序填表，非模型候选字段。repeat_test 只在内部保留 |
| T2 字段投影 | treatment_recommendation.primary → decision；free_text、confidence、允许的 variable_weights → reasoning | repeat_test、治疗计划子字段和非平台白名单权重内部保留。平台允许权重键以实际入口常量为准 |
| T3 字段投影 | event、months_to_recurrence → decision；free_text → reasoning | repeat_test 只在内部保留 |
| 外层兼容与内层历史行为 | external_reflection、internal_reflection | 外层文本检测可记录 detected_no_executable_proposal；无绑定授权保留 blocked_missing_authorization；内层历史路径仍为 legacy_behavior_unreviewed |
| 失败 | failed_no_valid_initial、reflection_review_error、run_failed | 解析尝试结束仍无合法 J0 则失败。一般审核异常有合法 J0 时回退。身份、额度或审计持久化致命错误退出，缺失最终成功回执 |

显式覆写示例由合成测试直接注入规则，匹配阶段、路径和规范化的前后值。规则 ID 为合成标记。G1R 不授予任何现行后处理覆盖已审候选的生产权威。
