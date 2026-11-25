# S1 critique 解析修复 · 217 case 离线重放报告

> 生成: 2026-08-30T16:35:17（零推理）
> 数据源: 0829 消融 B 的 217 个 output/trace/*/critique_iter1.json
> 解析器: 修复后 `_parse_critique_json`（S1，commit 见 git log）

## 核心结论

**若 S1 修复在 0829 消融 B 生效，refine 触发率会是 102/217（47.0%），原触发率 0/217（0.0%）。**

- 原解析器把全部 217 个 thinking 前缀 critique 误判为污染 → `parsed_issues` 全为空 → refine 0% 触发。
- S1 修复后：212 个 thinking 前缀 case 从尾部恢复了有效 JSON；
  5 个本就是干净 JSON 直接开头（原路径已正常）；0 个确无 JSON（保持降级）。

## 统计

| 指标 | 值 |
|---|---|
| refine 将触发 case 数 | **102/217（47.0%）** |
| 原触发率（修复前） | 0/217（0.0%） |
| 抓到的 issue 总条数 | 215 |

### issues 类型分布（10 类，按命中数）

| 类型 | 命中数 |
|---|---|
| internal_inconsistency | 74 |
| fabricated_facts | 60 |
| missing_guideline | 34 |
| unsupported_weights | 30 |
| confidence_miscalibration | 25 |
| psa_volume_inconsistency | 20 |
| treatment_biopsy_confusion | 16 |
| under_treatment | 5 |
| over_treatment | 3 |
| imaging_pathology_mismatch | 3 |

### severity 分布（全部 217 case，非触发 case 归入 none）

| severity | case 数 |
|---|---|
| none | 115 |
| medium | 58 |
| high | 26 |
| moderate | 12 |
| mild | 4 |
| low | 2 |

> 注意：模型 severity 未严格按 schema（none/mild/moderate/severe），
> 大量使用 high/medium/low 尺度——格式漂移，refine 消费 severity 时需映射。

## 结论补全（消融 B 真实结论）

0829 消融 B 得出「refine 0% 触发率 → self_refine 无增益」的结论，但根因是
解析器 bug（2A 前置过滤错杀 thinking 前缀后的有效 JSON），而非 refine 本身无效。
S1 修复后重放显示 refine 本可触发 102/217（47.0%），top issue 类型为「internal_inconsistency」等。
self_refine 的真实增益需在修复后重跑消融 B 才能定案（下一步，GPU 任务）。

> 逐 case 明细见 scripts/s1_replay_results.json
