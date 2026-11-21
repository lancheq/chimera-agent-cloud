# CHIMERA-Agent 第二轮训练发布与 schema 变更

2026-11-21，北京时间。新版训练发布已独立接收。复核 423 个病例目录及 1,666 个文件，逐文件 SHA-256 与传输清单全部一致。回执记录未覆盖、未合并旧数据。本次验收范围为传输完整性，未运行训练、推理或评测。

## Phase 2 输入变更

按随批发布说明，仅 `structured-prompt.json` 与各任务的 clinical-data 文件相对 Phase 1 更新。

| 任务 | 首屏不再直接提供的键 | clinical-data 变更 |
|---|---|---|
| T1 | `psa`、`psad`、`vol`、`pirads`、`psap`、`task`、`cspca`、`notes` | `cspca` 改为独立字段，原 `radiology_report` 不再包含该预测。 |
| T2 | `psa`、`psad`、`vol`、`pirads`、`psap`、`task`、`cspca`、`notes`、`bx_isup`、`bx_gl_prim`、`bx_gl_sec` | `cspca` 与 `AI_predicted_isup_grade_group` 改为独立字段，分别从 `radiology_report` 和 `pathology_report` 移出。 |
| T3 | `task` | `cspca` 与 `AI_predicted_isup_grade_group` 改为独立字段，分别从 `radiology_report` 和 `pathology_report` 移出。 |

`AI_predicted_isup_grade_group` 对应最近一次可用活检的 AI 预测，缺预测时为 `null`。独立字段可见性、Socket 任务绑定、工具准入与预算结算仍需针对新发布验收。旧版资料和旧批次结果保留原先的版本身份。[Phase 2 官方入口](https://chimera-agent.grand-challenge.org/chimera-agent-phase-2/)
