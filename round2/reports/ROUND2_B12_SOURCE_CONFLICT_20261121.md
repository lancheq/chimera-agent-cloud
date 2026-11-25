# B12 来源整理与冲突分类

2026-11-21，北京时间。本机私有。B1/B2 离线候选已通过冻结材料核对与合成回放，待规划验收。B3 真实纵向、未知日期推断、拟合、自动改诊断、生产启用及正式 validation 保持 HOLD。 [任务卡](../discussion/tasks/20261121_B12_SOURCE_AND_CONFLICT.md) · [机器交接](../output/b12_source_conflict_20261121/handoff.json)

## B1 来源及版本

| 核对项 | 结果 |
| --- | --- |
| 冻结六条的分母 | 6 条标记，3 条版本记录，3 个病例键，3 张卡片，2 份互异临床原件，2 个保守风险组。版本记录沿用 `server_legacy` T1 的 0、1、72。 |
| 来源登记 | 卡片、MRI、lab、notes 和 PSA trend 按发布、任务、版本记录、快照指纹、对象及原字段定位登记。每条来源保留冻结矩阵行号、证据编号、原件路径及指纹回执。冻结复合项的卡片位置有一处缺少具体字段，保留卡片指针，字段为 UNKNOWN。 |
| 重复证据 | 同一绑定内的原字段归组并保留全部指针。两条版本记录的 clinical 路径指向相同字节，分母计一份原件，两张卡片仍各自绑定。临床独立性与观察身份均为 UNKNOWN。 |
| 合法准入 | 冻结指纹支持本次审计追溯。真实证据的决策快照可见性与运行准入继续为 UNKNOWN，相关分支停止于登记。 |

冻结六条的定性结论与版本分母沿用第一步验收。矩阵保留各项冻结判定及来源定位，供逐项回查。 [第一步验收](ROUND2_B_SOURCE_TIME_STATIC_PLANNING_REVIEW_20261120.md) · [私有证据矩阵](../output/b12_source_conflict_20261121/evidence_matrix_private.jsonl) · [私有来源目录](../output/b12_source_conflict_20261121/source_catalog_private.json)

## B2 分类回放

| 判定 | 验收结果 |
| --- | --- |
| 指标或字段误读 | 合成例把 MRI 的 PSAD 读作 PSA，能按已绑定字段识别误读。空活检字段的真实语义仍为 UNKNOWN。 |
| 不同时点或不同样本 | 两个经合成权威标注的采集时点可排序。同日且标本标识不同保留两次观察。同日同月、近值及报告转述均保留各自的身份缺口。 |
| 已知口径不可比 | 已绑定的 PSA 与 PSAD，以及已证方法不同的同指标，均可识别。冻结 PSAD 的两套已记载配对照录，卡片上游出处、无来源卡片值及缺失单位维持 UNKNOWN。 |
| 来源或观察身份 UNKNOWN | 回放覆盖卡片误配、回执作用域或摘要不符、同日近值、转述未绑定、迟到报告及修订版、未证单位。未经准入的合成载荷哨兵未出现在证据投影中。 |
| 同次观察未裁冲突 | 合成 B12-S1 的卡片与 lab 具有一致的已证观察标识、采集时间与比较口径。重复回执归组前可追溯 2 条指针，归组后可追溯 3 条，证据组数保持 2，原值一并保留。 |

真实冻结判定沿用 9 处 UNKNOWN、2 处仅有相对先后、1 处同字节重复和 0 处已确认同次观察的未裁冲突。两项原有 PSAD 同量可比性备注随矩阵保留。相对先后保留原有时间精度。回放共用原 `compare`、`bound_tool_sources` 和字段选择规则，在隔离层增加作用域与回执检查、同绑定归组以及缺证状态。 [原比较与回执绑定](../src/chimera_agent_baseline/agent/evidence_reconciliation.py) · [合成回放](../output/b12_source_conflict_20261121/synthetic_replay.jsonl)

## Phase 2 字段合同

依据接收报告和现有工具字段表完成静态核对。T1/T2/T3 的临床文件均登记独立 `cspca` 候选。T2/T3 同时登记独立 `AI_predicted_isup_grade_group`，其含义为最近一次可用活检的 AI 预测，缺预测为 `null`。现有 MRI 和病理工具仍各自登记报告字段，独立字段的运行准入为 UNKNOWN。csPCa 比较候选为 UNKNOWN，待证信息包括模型及版本、输入影像或快照、预测目标、量纲与校准口径。此项只登记字段身份与来源路径，零新增病例或队列。 [Phase 2 接收记录](ROUND2_PHASE2_SCHEMA_INTAKE_20261121.md) · [静态字段合同](../output/b12_source_conflict_20261121/phase2_source_contract.json)

## 资源与原件保全

回放使用一核和本机已有的 `chimera_access` Python 环境，复用 8 条冻结合成反例，新增 3 组边界用例。冻结依赖在回放前后核对 SHA-256。六处原件指纹沿用第一步，当前仅前后核对路径、大小及文件元数据，未打开原件或重算其内容哈希。零新增临床模型调用、临床工具请求、GPU、数据下载、GT 或新样本读取。阶段耗时、依赖及交付指纹见机器回执。 [机器交接](../output/b12_source_conflict_20261121/handoff.json)

从仓库根目录复核，`--check` 在内存重放并校验磁盘产物。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src taskset -c 0 \
  /home/hedongze/venvs/chimera_access/bin/python -B \
  output/b12_source_conflict_20261121/replay.py --check
```

[回放入口](../output/b12_source_conflict_20261121/replay.py) · [隔离原型](../output/b12_source_conflict_20261121/candidate.py) · [冻结回放](../output/b12_source_conflict_20261121/frozen_audit.py) · [用例](../output/b12_source_conflict_20261121/synthetic_cases.py)
