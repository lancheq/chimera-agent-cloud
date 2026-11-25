# engineering_review — 源码级工程解析资料

这个目录是为"逐模块、逐函数理解 CHIMERA-agent 到底怎么跑"准备的，不是阶段性汇报。

## 从哪开始

按你关心的问题选入口：

| 想知道什么 | 看哪 |
|---|---|
| 系统实际按什么顺序执行 | `a_line/A_LINE_EXECUTION_ORDER.md` |
| Plan 是自然语言还是动作集合 | 同上，第三节 |
| Adaptive 和 Fixed 差在哪 | 同上，第一节 + `TRACE_INDEX.md` |
| 某一例具体走了哪些分支 | `a_line/traces/`（91 例 × 2 臂） |
| 指南库三代版本什么关系 | `kb_manifests/KB_VERSION_MAP.md` |
| 1288/996/983 到底各是什么 | 同上 |
| C 线每个脚本干什么 | `../scripts/c_line/README.md` |
| 哪些文件没上传、为什么 | `MISSING_FILES.md` |
| 实验对应哪个代码版本 | `VERSION_MAP.md` |
| armA/armC 和主仓什么关系、v2 怎么接的 | `ARM_INTEGRATION.md` |
| 每个实验的入口脚本在哪 | `EXPERIMENT_ARTIFACT_INDEX.md` |
| 模型/embedding/reranker 到底是什么 | `RESOURCE_MANIFEST.md` |
| 新旧检索算法差在哪、新库切流了吗 | `c_line_analysis/C_LINE_RETRIEVAL_DIVERGENCE.md` |
| Reflection 怎么验证的、会不会被覆盖 | `reflection/REFLECTION_EVIDENCE.md` |
| Fact Check 到底调不调 LLM | `reflection/FACT_CHECK_NOTES.md` |
| 旧库是怎么建出来的 | `../guideline_dataset/scripts/README.md` + 五个脚本 |

## 目录

```
engineering_review/
├── A_LINE...  → 见 a_line/
├── a_line/
│   ├── A_LINE_EXECUTION_ORDER.md   两臂事件序列实证
│   ├── traces/                     91 例 × 2 臂事件账本（脱敏）
│   ├── scripts/                    账本构建 + 实验 setup
│   └── summary/                    聚合统计（判对错、预算、资格）
├── b_line/
│   ├── scripts/                    语义修复 / 证据协调 / B12 边界与回归
│   ├── evidence/                   semantic_fix.diff 等汇总
│   └── archive/                    修复前代码 + 回归基线代码快照
├── c_line_analysis/                C01 知识库重建审计（含 record_id 级追溯）
├── reflection/                     Reflection 18 场景证据 + Fact Check 机制
├── kb_manifests/                   三代知识库 manifest + 版本映射
├── FILE_MANIFEST.md                源码/配置同步清单与哈希
├── TRACE_INDEX.md                  轨迹索引
├── VERSION_MAP.md                  实验↔代码↔资源版本对应
├── EXPERIMENT_ARTIFACT_INDEX.md    每个实验的入口、配置、产物
├── RESOURCE_MANIFEST.md            主模型/embedding/reranker/环境身份
├── ARM_INTEGRATION.md              armA/armC 与主仓关系、v2 接入机制
└── MISSING_FILES.md                未同步清单及原因

../archive/armA_current/            armA 快照（25 py，非历史版本）
../archive/armC_current/            armC 快照（135 py，非历史版本）
```

## 三条阅读原则

**一、先看事件账本，再看源码。** `candidate_events.jsonl` 直接给出某例的真实执行序列，比读 `graph.py` 猜节点顺序快得多。两者对不上时，以账本为准——账本是产物，`graph.py` 是当前代码，二者可能不同版本。

**二、代码里的常量比文档可信。** 报告会写"分析前先填表"，`graph.py` 的实际调用顺序才作数。本轮已发现报告与实测顺序不一致（见 A_LINE_EXECUTION_ORDER 第二节）。

**三、数字冲突先查口径，不要先怀疑笔误。** 1288 / 996 / 983 三个数曾被认为是三份报告互相矛盾，实际是三个不同阶段的量。同类问题还有 236 vs 238（GT 条目 vs 有效评估条目）；91 是**配对样本数**（worker0 46 + worker1 45），对应 182 份两臂账本，而单个 `candidate_execution_config.json` 里的 `cases: 46` 只是 worker0 的配置。

## 同步边界

本目录只放**可公开**的内容。所有含患者临床值、模型 prompt 正文、指南原文分片、向量文件、模型权重的部分都在 `MISSING_FILES.md` 里列明，并给出替代材料。

轨迹数据统一规则：

- `messages` / `response` 字段整体剥离
- `PSA` 数值 → `<PSA>`，`PI-RADS n` → `PI-RADS <n>`，年龄 → `<age>`
- `Gleason` / `ISUP` 分级 → `<n>`
- 保留：事件类型、工具名、`budget_before/after`、`policy_turn`、`action`、`stage`、token 计数
