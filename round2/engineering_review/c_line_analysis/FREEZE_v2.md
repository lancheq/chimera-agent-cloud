# v2 冻结配置

冻结时间：2026-11-24 22:50

## 知识库

- 路径：`/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2/`
- 可检索单元 1080：narrative 826 / recommendation 157 / table 97
- 父文档 `section_full` 292 条，不入库，仅作回填
- 向量：`vectors.npy`（1080×768，embeddinggemma-300m，chimera_access 环境 st 5.7.0 / tf 4.57.6）
- 抽样向量对齐通过：7 个采样点（首、尾、分片边界两侧、narrative/recommendation/table 三种类型）余弦均为 1.000000；**未做 1080 条逐条验证**

## 检索参数

| 参数 | 值 |
|---|---|
| dense 召回 | 20 |
| BM25 召回 | 20 |
| RRF | k=60，前 30 进重排 |
| 重排 | xlm-roberta cross-encoder，max_length 512 |
| 最终正文 | 5 |
| P1 回填 | ≤2 小节，按首次命中重排顺序，去重 |
| 泛词处理 | F6（去 EAU/AUA/ESUR/ISUP/guidelines，三处同时） |
| 临床场景过滤 | 未启用 |

## 元数据语义（2026-11-24 修正）

- 表格：`table_id` 保存 caption 编号（如 `Table 4.3`）；`section_id` 置空、`section_unknown=true`、`clinical_context=["unknown"]`。caption 编号不得冒充真实章节，避免程序误用导致错误回填
- 正文：`section_id` 来自 PDF 版面标题，可信

## 已知限制（不再调参）

1. 97 个表格的真实章节未知（caption 编号与章节号不对应），`clinical_context` 全部 unknown
2. Q03/Q04/Q06 场景错配 9-10 条 / 上下文 11 条
3. Q01 正确分支第 12 名，靠 P1 进上下文
4. 24 个跨页重复 caption 已打 `dup_caption`

## 不可改动项

- Agent prompt 不改
- 35B 模型、温度、后处理不改
- 不新增重排模型、不引入小模型
