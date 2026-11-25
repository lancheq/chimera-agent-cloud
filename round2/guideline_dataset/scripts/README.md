# CHIMERA-agent 医疗手册数据集

> 生成时间：2026-08-20
> 目的：为 CHIMERA-agent 构建统一的临床指南数据集，供 RAG 检索 + SFT 训练 + 评估对齐使用

## 目录结构

```
guideline_dataset/
├── README.md                      ← 本文件
├── raw/                           ← 原始数据
│   ├── eau_2025_prostate.pdf      ← EAU 2025 完整指南 PDF（251 页）
│   ├── eau_official_chunks.jsonl  ← 官方 ChromaDB 导出（1501 chunks）
│   ├── eau_pdf_chapters.jsonl     ← PDF 章节精确切分（478 章节）
│   ├── pirads_v2_1_rules.md       ← PI-RADS v2.1 评分规则摘要
│   └── aua_suo_2023_early_detection.md ← AUA/SUO 2023 早期检测摘要
├── normalized/                    ← 归一化数据
│   ├── eau_normalized.jsonl       ← EAU ChromaDB 归一化（1501 条）
│   └── guidelines_unified.jsonl   ← 多来源统一 schema（491 条）
├── qa/                            ← 决策 QA 对
│   └── guideline_qa.jsonl         ← 20 条带 grounding 的 QA
└── scripts/                       ← 处理脚本
    ├── 01_normalize_eau.py        ← EAU ChromaDB 清洗归一化
    ├── 02_extract_eau_pdf.py      ← EAU PDF 章节提取
    ├── 03_merge_normalize.py      ← 多来源合并归一化
    └── 04_extract_qa.py           ← 决策 QA 提取
```

## 数据来源

| 来源 | 版本 | 规模 | 获取方式 |
|------|------|------|---------|
| EAU 2025 | Limited Update March | 478 章节 + 1501 chunks | GuidelineCentral 镜像 PDF |
| PI-RADS v2.1 | 2019 | 8 节规则 | 公开综述整理（Turkbey 2019 + SciELO） |
| AUA/SUO 2023 | Early Detection | 5 节规则 | 公开摘要（Medscape） |

## 统一 Schema

```json
{
  "id": "eau-pdf-0123",
  "source": "EAU",          // EAU | PIRADS | AUA
  "version": "2025-LimitedUpdate-March",
  "year": 2025,
  "section_id": "5.6.4",
  "section_title": "Summary of evidence and recommendations for performing prostate biopsy",
  "page": 43,
  "text": "...",
  "task_tags": ["T1", "T2"]  // T1=活检 / T2=治疗 / T3=BCR / GENERAL
}
```

## QA 对 Schema

```json
{
  "id": "qa-t1-000",
  "task": "T1",
  "question": "PI-RADS 5 + PSA 12 + PSAd 0.25，是否推荐活检？",
  "answer": "是，强烈推荐活检...",
  "decision": "YES",         // YES / NO / INSUFFICIENT / FOLLOW_UP / 等
  "confidence": "high",       // high / medium / low
  "grounding": [
    {"source": "EAU", "section_id": "5.4.2.2", "section_title": "...", "page": 36}
  ],
  "evidence_level": "1a"
}
```

## 数据统计（清理后）

### 归一化指南（376 条）
- EAU: 363 章节
- PI-RADS: 8 节
- AUA: 5 节

### task 标签分布
- T2（治疗）: 274
- T1（活检）: 179
- T3（BCR）: 159
- GENERAL: 38

### 清理记录
- 删除 59 条误识 section_id（统计表格数字被当成章节号，如 0.15/50.0/74.4）
- 删除 42 条目录残留短章节（text<100 且子章节已覆盖正文）
- 共删除 101 条，491 → 376 条
- QA grounding 全部修正为指向实际存在的章节，命中率 30/30=100%

### QA 对（20 条）
- T1 活检决策: 8 条（含 PSAd+PI-RADS 矩阵、missingness 场景）
- T2 治疗决策: 6 条（低/中/高危 + cN1 + 高龄 + WSI 缺失）
- T3 BCR 风险: 6 条（定义、预后、salvage RT、PSA 趋势不足）

## 关键覆盖章节（EAU）

| Section | 标题 | 与 CHIMERA 关系 |
|---------|------|----------------|
| 5.4.1 | Risk assessment before MRI and biopsy | T1 风险计算器 |
| 5.4.2.2 | Combining MRI and PSA Density | **T1 核心**：PI-RADS + PSAd 决策矩阵 |
| 5.6.4 | Biopsy recommendations | T1 活检方式 |
| 6.3.1 | Management of low-risk | T2 AS vs 治疗 |
| 6.3.2 | Management of intermediate-risk | T2 中危治疗 |
| 6.4.1 | BCR Background | T3 BCR 定义 |
| 6.4.1.1 | PSA velocity and doubling time | T3 倍增时间 |
| 6.4.5.1.1 | Salvage RT after RP | T3 salvage 治疗 |
| 7.4 | Follow-up | T3 随访方案 |

## 使用方式

1. **RAG 检索语料**：用 `normalized/guidelines_unified.jsonl`（491 条）替换/增强官方 ChromaDB
2. **SFT 训练数据**：用 `qa/guideline_qa.jsonl`（20 条）微调 LLM 学习决策格式
3. **评估对齐**：QA 对的 grounding 字段用于验证 section_grounding 评分

## 注意事项

1. NCCN v3.2024 需注册账号才能下载，未包含；EAU 2025 已覆盖 CHIMERA 三任务大部分规则
2. PI-RADS v2.1 因 ACR 官方 PDF 反爬拦截，改用公开综述整理的规则摘要
3. EAU PDF 提取时有少量"短章节"（目录残留），已在归一化时按 text 长度去重保留正文版
4. QA 对为手工构造的种子集，后续可用 LLM 批量扩充
```
