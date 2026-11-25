# B12-RCA · 逐字段根因定位

本卡复现通过，交规划验收。对象是同一个无患者 B12-S1 夹具的两次历史 35B 短输出。两臂格式通过，语义完整通过 0/2。结构臂的冲突结论首次失配于原始 `choices[0].message.content`。实际请求明确写入同一次观察、冲突未裁，原答对应字段为 `UNKNOWN`，严格解析保留该值，语义校验据此拒绝。模型选择该枚举的原因、表述结构的因果贡献、真实病例中的表现均为 UNKNOWN。

## 参考值和送达模型的内容

B12-S1 的同观察凭证由合成夹具作者在 `confirmed_conflict()` 中制作。三个来源共用带采样证明的观察标识 `pinned-lab/specimen-and-observation/s1`。夹具赋予三个来源相同的任务、病例、快照、会话和观察日，再由源摘要或已结算的合成回执逐项绑定。工具回执验证来源、路径和返回内容，观察身份与可比属性由夹具中已固定的主张提供。这个凭证只在该合成夹具内成立。

| 输入来源 | 原值及定位 | 证据组 |
|---|---|---|
| S1 初始卡片 | 5，`psa`，绑定卡片摘要 | 初始卡片 |
| S2 实验室回执 | 6，`laboratory_results[0].val`，独立请求编号 | 实验室 |
| S3 实验室重复回执 | 6，同路径，与 S2 请求编号不同，响应摘要相同 | 与 S2 同一组 |

S2/S3 的范围、来源对象、字段和指标相同。分母为三个来源指针、两个已绑定证据组、一个独立观察。可比条件是合成实体 `synthetic-entity`、指标 `psa`、口径 `total-PSA`、方法 `synthetic-assay`、单位 `ng/mL`。观察日 2026-01-01、报告日 2026-01-02、截止前可得日 2026-01-03 分开保存。两个原值归于同一次可比观察，冲突未裁，胜出值 UNKNOWN，趋势 UNKNOWN。复跑夹具所得引用、分组和关系与封存行一致。见 [夹具](/home/hedongze/CHIMERA-agent/output/b12_source_conflict_20261121/synthetic_cases.py:166) 和 [来源权威](/home/hedongze/CHIMERA-agent/output/b12_short_output_20261121/source_authority_private.json)。

两个 HTTP 请求与各自封存字节一致，SDK 摘要、原生计数输入及 HTTP 摘要均能沿审计链复核。逐项还原结构臂请求后，两臂 JSON 完全一致。唯一改动是在共同观测与共同短输出指令之间，在 `messages[1].content` 增加下列注记。原始字节、前置换行和 SHA-256 见字段回执。

~~~text
B1/B2 source and comparison structure: {"evidence_group_count":2,"relations":[{"source_groups":[["S1"],["S2","S3"]],"status":"SAME_OBSERVATION_CONFLICT_UNRESOLVED"},{"same_evidence_source_ids":["S2","S3"],"status":"SAME_BOUND_EVIDENCE"}]}
~~~

## 逐字段结果

| 核验项 | 参考 | 原样臂原答 | 结构臂原答 |
|---|---|---|---|
| 来源、原值、重复回执呈现 | S1→5，S2/S3→6，共两条测量行 | 一致 | 一致 |
| 证据组数 | 2 | **1，最早失配** | 2 |
| 独立观察数 | 1 | 1 | 1 |
| 观察身份 | 经夹具凭证确认同次观察 | 无独立字段，组合枚举显式给出不同观察，与计数冲突 | 无独立字段，组合枚举 UNKNOWN，未显式保留身份 |
| 可比性 | 同实体、指标、口径、方法及单位 | 无独立字段，NOT_MEASURED | 无独立字段，NOT_MEASURED |
| 冲突状态 | `same_observation_conflict_unresolved` | `distinct_observations` | **`UNKNOWN`，最早失配** |
| 真值裁决 | 未裁，胜出值 UNKNOWN | 无独立字段，保留两值，未明示选真 | 无独立字段，保留两值，未明示选真 |
| 趋势 | UNKNOWN | `rising` | UNKNOWN |
| 固定理由及解释 | 限定理由可延期或明确待调和 | 选择通用证据有限句，允许。独立解释 NOT_MEASURED | 同左 |
| 语义评分 | verified | rejected，`evidence_group_count_unsubstantiated` | rejected，`conflict_status_unsubstantiated` |

严格解析复跑检查单一 assistant 响应、完整 JSON、重复键、schema、字段顺序与原值。两臂分别使用 1,553 / 1,621 输入 token，672 / 669 完成 token，均以 `stop` 结束并低于共同的 1,024 输出上限。SDK 回执对使用量附加可空元数据，清除这些额外空项后计数一致，原答文本逐字一致。解析对象逐字段等于原答对象。回读把组数等字段映射为摘要展示键，测量值转为展示字符串，语义没有变化。语义校验遇首错即拒绝，所以逐字段表仍列出基线随后的冲突和趋势问题。两臂的正式 Agent、planner、动态 MCP、后续诊断消费、填表、后处理写回、官方 Socket 和临床评分均为 NOT_MEASURED。

## 表示限制与候选

旧 schema 已允许 `same_observation_conflict_unresolved` 并可如实表达此合成夹具。观察身份、可比性、冲突及真值裁决没有各自独立字段。决策理由只接受三个固定句子。该结构可以容纳本例正确组合，但无法单独观测模型是否识别身份、判断可比、确认冲突与暂缓选真。结构注记送达、原答偏差和解析保留分别有凭据。组合枚举是否促成 UNKNOWN 仍待验证。

建议先审方案 A，把已绑定的确定性关系保存在程序字段，另验模型原答能否正确解释。映射检查可继续用于回归，现有回放没有定位到可修复的映射失真。方案 B 能提高字段可测性，其模型效果和格式代价尚待同配对检验。[候选比较](/home/hedongze/CHIMERA-agent/output/b12_rca_20261122/candidate_comparison.md)。

## 复现与留存

前后核对 67 份冻结文件，全部 SHA-256 和字节数一致。其中短输出实机交接索引 23 项、短输出离线产物 10 项、上游冻结索引 30 项，另有交接与任务卡。单亲和 CPU 执行，真实模型、GPU、临床工具与信息单位各 0。未接触真实病例或改动生产代码。报告及逐字段证据私有，本卡结束。

交接中的 `replay_command` 可直接复跑，无需写回。由项目根运行下面的命令，脚本会重新对比输入、逐字段结果及三份交付指纹。

~~~bash
python -B -c 'import json, os, sys; command = json.load(open(sys.argv[1]))["replay_command"]; os.execl("/bin/bash", "bash", "-c", command)' output/b12_rca_20261122/handoff.json
~~~

[逐字段回执](/home/hedongze/CHIMERA-agent/output/b12_rca_20261122/field_diff.json) · [交接和实测资源](/home/hedongze/CHIMERA-agent/output/b12_rca_20261122/handoff.json)
