# B01 冲突病例证据包

59 个真实病例、171 条冲突标记、29 个未解决风险组，从项目服务器私有环境导出，供病例级核查使用。数据不含 GT、不含参考推理、不含预测结果。

抓取时间：2026-11-23 12:30 (UTC+8)。来源：`hedongze-server:/home/hedongze/CHIMERA-agent/`。

## 目录结构

```
cases_evidence/
├── audit/                      冲突审计层
│   ├── conflict_review_ledger.jsonl    171 条冲突标记（路径已转相对）
│   ├── unresolved_review_queue.json    29 个未解决风险组
│   ├── summary.json                    汇总计数
│   ├── REPORT.md                       审计报告原文
│   ├── source_hashes.json              源哈希清单
│   ├── handoff.json                    交接说明
│   └── target_read_guard.json          读取守卫配置
├── recovery/                   B01 恢复层
│   ├── pilot6_private.jsonl            6 条六行试点（含成本/可达性分账）
│   ├── recovery_private.jsonl          66 条恢复记录
│   ├── field_recovery_private.jsonl    66 条字段级恢复记录
│   ├── field_summary.json / handoff.json / delivery_manifest.json
│   └── cost_matrix.json / representation_matrix.json
├── cases/                      当前版本病例，59 例
│   └── <case_id>/
│       ├── structured-prompt.json
│       └── prostate-biopsy-decision-clinical-data.json
└── cases_phase2/               phase2 旧版病例，36 例（与 cases/ 重叠的版本对照）
    └── <case_id>/（同上两文件）
```

## 两个版本，别混用

同一病例在仓库里有两个版本，内容不同，sha256 不同。

| 版本 | 目录 | ledger 中 `source` | 例数 |
|---|---|---|---|
| 当前版 | `cases/` | `local_train_release_v1`、`server_legacy` | 59 |
| 旧版 | `cases_phase2/` | `phase2_train_release_20261104` | 36 |

36 个病例两版都有。72 处 sha256 分叉就来自这批重叠，不是文件损坏。旧版原始路径在 `data/round2_materials_20261111/mac_transfer/20261111_101222_bn72g8xr/payload/releases/phase2_train_release_20261104/task1/`，那个 release 一共 195 例，只取了与当前版冲突的 36 例。

比对时按 ledger 里的 `source` 字段选目录，否则会把版本差异误判成数据篡改。

## 剔除了什么

每个病例原始目录有 5 个文件，只同步了 2 个。

| 文件 | 处理 | 原因 |
|---|---|---|
| `structured-prompt.json` | 保留 | 结构化患者资料 |
| `prostate-biopsy-decision-clinical-data.json` | 保留 | 五个 section 的临床原文 |
| `prostate-biopsy-decision.json` | 剔除 | GT。内容就是 `"no"` 这类标签 |
| `prostate-biopsy-decision-reasoning.json` | 剔除 | 参考推理 + 变量权重 + 揭示顺序 |
| `prostate-modality-level-neural-representations.json` | 剔除 | MRI/切片特征向量，纯数值，对字段核查无用 |

`prostate-biopsy-decision-reasoning.json` 实际只有 27 例有，另外 32 例原始目录里就没这个文件。

三份 `*_private.jsonl` 里都有 `truth_selected` 字段，逐条验证过：`pilot6` 6 条、`recovery` 66 条、`field_recovery` 66 条，全部为空值。所以恢复层可以公开，不会漏答案。

## 脱敏做了什么

- `conflict_review_ledger.jsonl` 等文件中 `source_files.path` 的绝对路径 `/home/hedongze/CHIMERA-agent/...` 全部改为仓库相对路径，共 5 处（`source_hashes.json` 2 处、`recovery/handoff.json` 2 处、`delivery_manifest.json` 1 处）。
- 全目录扫描确认无 `home/hedongze` 残留。
- 病例文件本身未改写，`enc_ref` 等字段里的医生姓名保持原样。这些是原始 release 里的合成姓名，不是真实人员。

## 完整性自证

`audit/conflict_review_ledger.jsonl` 每条记录了源文件的 sha256。对 342 条记录逐条重算：

```
一致 328 / 不一致 0 / 缺失 14
```

缺失的 14 条是 `BX_01`、`BX_02` 两个病例的 7 条记录。这两例在 ledger 里 `usage` 标为 `engineering_fixture`，是软件测试夹具，不是真实病例，故意没同步。

`usage` 字段分布：`internal_development_exposed` 164 条，`engineering_fixture` 7 条。真实病例是把夹具排除后的 59 例。

## 冲突类型分布

`old_candidate.type` 字段：

| 类型 | 条数 | 含义 |
|---|---|---|
| `psa_multi` | 109 | PSA 多点数值不一致 |
| `psad_self` | 58 | PSAD 与 PSA/体积算出值不符 |
| `psad_vs_mri` | 2 | PSAD 与 MRI 报告口径冲突 |
| `bx_mention` | 2 | 既往活检史与卡片空字段冲突 |

## 已知限制

`clinical-data.json` 只有五个 section：`radiology_report`、`previous_notes`、`psa_trend`、`laboratory_results`、`family_history`。没写在里面的信息，公开数据里就查不到，需要靠 `recovery/` 里的可达性记录判断是"原始数据缺失"还是"程序没去读"。

`structured-prompt.json` 的字段是打平的一层（`psa`、`psad`、`vol`、`pirads`、`bx`、`cspca` 等），没有单位标注。`psad` 单位争议就是 `psad_self` 那 58 条的根源。
