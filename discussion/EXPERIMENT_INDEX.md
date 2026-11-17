# 实验与过程索引

| 工作 | 测量对象与结果 | 报告 |
|---|---|---|
| 数据池登记 | 三来源1275条记录不是独立患者数；来源／版本分账 | [登记](../round2/reports/ROUND2_DATA_POOL_REGISTRATION_20261113.md) |
| 冲突来源审查 | 171条候选，105条可解释、66条未裁；不改原值 | [审查](../round2/reports/ROUND2_CONFLICT_SOURCE_REVIEW_20261113.md) |
| 信息与成本边界 | 75项测试、423条官方输入绑定；不测医学提分 | [验收](../round2/reports/ROUND2_S2_FULL_SCOPE_ACCEPTANCE_20261113.md) |
| 固定／自主候选 | 共同计划、观察、权限，脚本＋真实MCP验收 | [候选](../round2/reports/ROUND2_SHARED_PLAN_CANDIDATE_ACCEPTANCE_20261113.md) |
| 首轮真实配对v1 | 91例，两组均60正确；自主控制失败较多 | [结果与机制](../round2/reports/ROUND2_S4_PAIRED_RESULTS_20261114.md) |
| 控制修复v2 | 接口和有限恢复工程验收，不是提分实验 | [修复](../round2/reports/ROUND2_CONTROL_REPAIR_ACCEPTANCE_20261114.md) |
| v2首次正式尝试 | 源码实际导入v1，私有分析拒绝；760次调用不作v2医学结果 | [失效取证与数据范围](../round2/reports/ROUND2_S4_V2_PROVENANCE_FAILURE_20261115.md) |
| v2-r1有效完整对照 | 182组别全成功；两组64/91，自主信息少10.33%，组内token约2.22倍；无准确率优势证据 | [有效结果与机制](../round2/reports/ROUND2_S4_V2_RESULTS_20261115.md) |
| 原始文献定位修正 | 已有相似实验及负面证据比较；S4降为工程基线，非新生成 | [文献比较](../round2/reports/ROUND2_LITERATURE_POSITIONING_20261115.md) |
| 6例／47次离线机制复盘 | 3例同临床事实但请求不同；23次早停少25单位，24次无患者信息节省；未作因果干预 | [机制复盘](../round2/reports/ROUND2_MECHANISM_REVIEW_20261115.md) |
| 两份任务规则来源／适用性审查 | 69条全覆盖；36窄义相符、17条件不全、13偏差、3未定位／说明；0新调用，资源未改 | [来源台账与单一候选](../round2/reports/ROUND2_EVIDENCE_APPLICABILITY_AUDIT_20261117.md) |
| C02来源／适用条件证据包 | 工程204测试＋真实MCP/CPU检索/stub消费通过；默认关闭，仅2原文载体；医学NO_GO、0新临床生成 | [验收与边界](../round2/reports/ROUND2_EVIDENCE_PACKAGE_ACCEPTANCE_20261117.md) |

更早试验见 reports 下的过程报告。不同患者集合／输入版本／统计环节不能连成一条提分曲线，重复调用不增加独立患者数。

工具试验（非医学实验）：[Codex → DSH 委派](DSH_DELEGATION_20261114.md)，无敏感数据冒烟通过。


## 2026-11-17 规划与任务登记（不是新实验）

| ID | 状态与产出 | 指令／证据 |
|---|---|---|
| INIT-20261117 | 状态恢复完成，0新临床生成，冻结源码与历史产物复核通过 | [规划报告](../round2/docs/ROUND2_PLANNING_INITIALIZATION_20261117.md) |
| MECH-20261115 | 已完成的6例／47次只读复盘，本轮恢复并补入导航 | [机制报告](../round2/reports/ROUND2_MECHANISM_REVIEW_20261115.md) |
| LIT-20261115 | 既有比较性核查，含负面结论与适配限制，不当新机制实验 | [定位报告](../round2/reports/ROUND2_LITERATURE_POSITIONING_20261115.md) |
| A01 | PLANNING_ACCEPTED_WITH_ADDENDUM：69条交付已验收，P60理由更正在C02新补充中落实 | [验收卡](tasks/20261117_A01_SOURCE_APPLICABILITY.md) |
| A02 | 具备条件，待用户确认；Reflection检测／纠正／审核／消费分层 | [任务指令](tasks/20261117_A02_REFLECTION_PATH.md) |
| B01 | 具备条件，待用户确认；66条未裁候选的恢复与合法成本 | [任务指令](tasks/20261117_B01_EVIDENCE_RECOVERY.md) |
| C01 | 可静态准备，规范冻结依赖A01；检索／诊断利用分开评价 | [任务指令](tasks/20261117_C01_RETRIEVAL_CONTRACT.md) |
| D01 | 具备条件，待用户确认；最多3个外部来源的资格与目标映射 | [任务指令](tasks/20261117_D01_EXTERNAL_DATA.md) |

上述READY不表示已派发或已生成结果。正式临床批次、训练、批量下载和平台提交均未启动。


| 新Task | 状态／边界 | 指令 |
|---|---|---|
| A01规划验收 | 接受进入候选准备，P60理由更正为必做项；非专家医学裁定 | 本地output/planning_c02_control_20261117/a01_planning_review.json |
| C02 | PLANNING_ACCEPTED_ENGINEERING_CLINICAL_NO_GO：工程结题，真实覆盖门槛未满足 | [完整目标](tasks/20261117_C02_EVIDENCE_PACKAGE.md) |

C02交付：DELIVERED_AWAIT_PLANNING_REVIEW。工程通过与医学晋级分开；真实合法格式/条件/来源覆盖未完成，临床NO_GO。[未执行验证草案](../round2/docs/ROUND2_EVIDENCE_PACKAGE_VALIDATION_DRAFT_20261117.md)。


| 规划终验 | 已核查证据／状态 | 报告 |
|---|---|---|
| C02终验 | 独立111项通过，与执行204项重叠；271新冻结／3321既有／278旧源码保全；工程通过、医学NO_GO；184公开文件独立回读 | [规划终验](../round2/reports/ROUND2_C02_PLANNING_ACCEPTANCE_20261117.md) |
