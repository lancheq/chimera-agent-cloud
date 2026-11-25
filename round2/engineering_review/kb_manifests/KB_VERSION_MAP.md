# 指南知识库版本映射（KB_VERSION_MAP）

本文直接回答一个反复出现的口径冲突：**1288 / 996 / 983 / 1372 到底各是什么**。

## 一、结论

这些数字来自**三个不同时间点、不同阶段的三套产物**，不是同一个量在不同报告里的笔误：

| 数字 | 实际含义 | 出自 | 阶段 |
|---:|---|---|---|
| **1288** | v1 切分产出的**记录数**（records） | `rechunk_20261124/stats.json` → `records` | 切分完成 |
| **996** | v1 **编码入库的向量数** | `rechunk_20261124/merge_summary.json` → `shape[0]`；`index_summary.json` → `indexed` | 向量编码完成 |
| **1372** | v2 切分产出的**记录总数** | `rechunk_20261124_v2/summary_v2.json` → `total` | v2 重切分 |
| **983** | v2r1 中**复用（不重新编码）的向量数**，不是记录单元数 | `rechunk_20261124_v2r1/summary_r1.json` → `vectors_reused` | v2r1 表修复 |
| **1037** | v2r1 处理后的**核心单元数**（1080 → 1037） | 同上 → `core_units_after` | v2r1 表修复 |

**1288 与 996 的差 292**，正好等于 `stats.json` 里的 `sections`（292）——即 `section_full` 类记录在编码阶段被合并，未逐条生成独立向量。这不是丢数据，是设计。

**983 出现在检索报告里被当作"单元数"引用，属于口径误用**：它是 v2r1 复用向量的计数，与"记录数"不是同一个量。

## 二、三个版本的构成

### v1 — `rechunk_20261124`

```
records 1288
├─ narrative        826
├─ section_full     292
├─ recommendation   157
└─ table             13

chunk_len: min 31 / median 703 / max 3201
recommendation_strength: Strong 115 / Weak 41 / Follow-up 2
skipped_toc_pages: [2..9]           ← 目录页剔除
reference_pages: 160, 251           ← 参考文献页
reference_page_count: 92
```

向量：996 × 768 维，21 个 shard，embedding 模型为 `resources/embedding_model`。

### v2 — `rechunk_20261124_v2`

在 v1 基础上扩展：

```
total 1372
├─ narrative        826
├─ section_full     292
├─ recommendation   157
└─ table             97     ← 表格从 13 增至 97

table_rows: 557
dup_groups_with_repeats: 2
retrieval_text_differs: 220  ← 220 条记录的检索文本与正文不同（如加了标题前缀）
```

同时引入 27 类篇章语境标注（`by_context`），从 `unknown: 38` 到 `active_surveillance_confirmed: 180`；`nonclinical_methodology: 21` 单独剔除。

### v2r1 — `rechunk_20261124_v2r1`

表格归属修复：

```
core_units:        1080 → 1037   (合并 24 组，额外删除 43)
table_records:       97 →   54
vectors_reused:            983
vectors_reencoded:          54
section_confidence: high 21 / medium 8 / unknown 25
```

`table_context` 的分布显示**大部分表格语境仍为 unknown（31/54）**，这是当前知识库已知的薄弱点，不是已完成修复。

## 三、产物与代码的对应

| 阶段 | 代码 | 产物 |
|---|---|---|
| v1 切分 | `round2/scripts/c_line/rechunk_eau.py` | `records.jsonl`, `stats.json` |
| v1 编码 | `encode_full.py`, `encode_shard.py`, `merge_shards.py` | `vec/shard_*.npy`, `vectors.npy`, `merge_summary.json` |
| v1 建库 | `build_index.py` | `db/chroma.sqlite3`, `index_summary.json` |
| 标题增强 | `build_title_boost.py` | `boost_meta.json`, `vectors_boost.npy` |
| v2 切分 | `rechunk_v2.py` | `records_v2.jsonl`, `summary_v2.json` |
| v2r1 表修复 | `fix_table_section.py`, `downgrade_table_meta.py`, `patch_v2_meta.py` | `records_r1.jsonl`, `summary_r1.json`, `table_audit.json` |
| 新库接入 | `v2_search_adapter.py` | 运行时适配，无独立产物 |

## 四、未同步的部分

`records*.jsonl`、`vectors*.npy`、`chroma.sqlite3` 含指南正文或完整向量，未上传。它们的规模与结构已由上述 summary 完整刻画：

- `records.jsonl` / `records_v2.jsonl` / `records_r1.jsonl`：行数见上表记录数
- `vectors.npy`：`996 × 768`（v1）、`1372 × 768`（v2，待核）、`vectors_r1.npy` 3.19 MB（v2r1）
- `vec/shard_*.npy`：21 个（v1）、23 个（v2）

需要逐条对应关系时，可用 `round2/scripts/c_line/probes/probe_a.py` 在读权限内复现。
