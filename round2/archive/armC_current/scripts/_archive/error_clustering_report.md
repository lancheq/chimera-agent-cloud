# 错例聚类分析报告 (gt_v3_20260823)

> 生成时间: 2026-08-24
> 数据来源: scripts/decision_errors.json
> 评估目录: test/output.gt_v3_20260823/

---

## 一、总览

| 任务 | 总 case | 错例数 | 错误率 |
|------|---------|--------|--------|
| T1   | 91      | 23     | 25.3%  |
| T2   | 72      | 24     | 33.3%  |
| T3   | 73      | 13     | 17.8%  |

---

## 二、T1 错例聚类（23 例）

### A 类：GT=no 但 pred=yes（过度推荐活检）— 14 例

| # | case_id | GT reasoning 摘要 | 判定 |
|---|---------|-------------------|------|
| 1 | PT-pseudo_0615b2bd8504 | PSA 160, "Start treating the patient" | 哲学冲突：已确诊应治疗非活检 |
| 2 | PT-pseudo_11345b8a7ab3 | PSA 113, "Start treatment, do PSMA-PET" | 哲学冲突：已确诊应治疗 |
| 3 | PT-pseudo_175e6ad47991 | P5, 79yr, "Need to discuss with patient" | 哲学冲突：高龄+shared decision |
| 4 | PT-pseudo_17f110c9e142 | "Need to know if P5 lesion has grown" | 哲学冲突：需更多信息 |
| 5 | PT-pseudo_1a4e0cf3b927 | "Most likely no biopsy, but rapid growth might change" | 哲学冲突：条件性no |
| 6 | PT-pseudo_2b3ef1d3a505 | "Need to know if lesion has grown or stable" | 哲学冲突：需更多信息 |
| 7 | PT-pseudo_34647958aba3 | "Man on AS, need initial ISUP and MRI" | 哲学冲突：已在AS中 |
| 8 | PT-pseudo_3646e0a2ae13 | "Normal MRI. No biopsy" | **可能模型缺陷**：MRI正常但模型因PSA趋势推荐活检 |
| 9 | PT-pseudo_4b950525e6a8 | ISUP4, "Treatment without repeat biopsy" | 哲学冲突：已高级别应治疗 |
| 10 | PT-pseudo_c55496c11421 | 80yr, low risk PC, "Only biopsy if signs of high risk" | 哲学冲突：高龄+低危 |
| 11 | PT-pseudo_c9d31197c533 | "Advocate for curative treatment" | 哲学冲突：应治疗非活检 |
| 12 | PT-pseudo_cce5d47c05dd | "Low threshold for curative treatment" | 哲学冲突：应治疗非活检 |
| 13 | PT-pseudo_d7d26761c714 | Low risk PC, PSA<10, "New MRI in two years" | 哲学冲突：低危AS |
| 14 | PT-pseudo_dcd61cf9b443 | "Should discuss curative treatment" | 哲学冲突：应治疗非活检 |

**A 类小结**：14 例中 13 例为 GT 哲学冲突（已确诊+高PSA应治疗而非活检、需更多信息、高龄低危），1 例可能为模型缺陷（MRI 正常仍推荐活检）。哲学冲突占比 **93%**。

### B 类：GT=yes 但 pred=no（漏诊）— 9 例

| # | case_id | GT reasoning 摘要 | 模型错误原因 | 判定 |
|---|---------|-------------------|-------------|------|
| 1 | PT-pseudo_0169468160c6 | PSA 187, "Very High PSA, most likely metastatic" | 模型认为已确诊不需活检 | 反向哲学冲突 |
| 2 | PT-pseudo_075dd468b384 | "Missing ISUP info, need biopsy" | 模型因低PSAD(0.14)否决 | **模型缺陷**：PSAD否决PI-RADS |
| 3 | PT-pseudo_099651db404e | "PIRADS 5 in a young man" | 模型因低PSAD(0.14)否决 | **模型缺陷**：PSAD否决P5 |
| 4 | PT-pseudo_2eef4115a253 | "P4 and LUTS symptoms, low PSA" | 模型因低PSA否决 | **模型缺陷**：忽略症状 |
| 5 | PT-pseudo_33c8cd4571fd | "P5 in 71yr, no major comorbidities" | 模型分析后错误结论 | **模型缺陷**：推理断裂 |
| 6 | PT-pseudo_c39e94a13920 | "AS for intermediate risk, confirmatory biopsy" | 模型因低PSAD(0.08)否决 | **模型缺陷**：PSAD否决AS确认活检 |
| 7 | PT-pseudo_d3d6b421d0aa | "AS biopsy indicated after two years" | 模型分析后错误结论 | **模型缺陷**：推理断裂 |
| 8 | PT-pseudo_d5ceb6b4204f | "Confirmatory biopsy after 2yrs" | 模型决定no | **模型缺陷**：忽略AS协议 |
| 9 | PT-pseudo_dd011d696387 | "Biopsy to exclude higher-risk disease" | 模型分析后错误结论 | **模型缺陷**：推理断裂 |

**B 类小结**：9 例中 1 例为反向哲学冲突，8 例为真模型缺陷。主要模式：
- **PSAD 过度否决**（3 例）：模型因低 PSAD 否决 P5/P4 病灶的活检指征
- **AS 确认活检遗漏**（2 例）：模型不理解 AS 协议需要定期确认活检
- **推理断裂**（3 例）：free_text 中有正确分析但最终结论错误

### T1 结论

| 类型 | 数量 | 占比 | 可修性 |
|------|------|------|--------|
| A 类哲学冲突 | 13/14 | 93% | 不可修（GT 哲学决定） |
| A 类模型缺陷 | 1/14 | 7% | 可修 |
| B 类哲学冲突 | 1/9 | 11% | 不可修 |
| B 类模型缺陷 | 8/9 | 89% | **可修** |

**GT 哲学冲突总占比**：14/23 = **61%**（低于此前预估的 90%）
**真模型缺陷**：9/23 = **39%**（主要集中在 B 类漏诊）

**可修方向**：
1. 降低 PSAD 在决策中的否决权重（当 P5/P4 存在时 PSAD 不应一票否决）
2. 增加 AS 确认活检规则（AS 患者 2 年后需确认活检）
3. 修复推理断裂（free_text 分析正确但结论错误）

---

## 三、T2 错例聚类（24 例）

### 混淆矩阵

| GT \ Pred | AS  | AT  | CS  | WW  | Total |
|-----------|-----|-----|-----|-----|-------|
| **AS**    | 17  | 5   | 3   | 0   | 25    |
| **AT**    | 7   | 24  | 0   | 0   | 31    |
| **CS**    | 6   | 0   | 7   | 1   | 14    |
| **WW**    | 1   | 1   | 0   | 0   | 2     |
| **Total** | 31  | 30  | 10  | 1   | 72    |

### 主要误判方向

| 误判方向 | 数量 | 性质 | 典型模式 |
|----------|------|------|----------|
| AT → AS  | 7    | **欠治疗（危险）** | GT 说应治疗，模型说AS。多为高PSA/GG2-GG3但模型因低PSAD选择AS |
| CS → AS  | 6    | 过度治疗 | GT 说继续观察，模型说AS。模型将"无癌观察"误判为"有癌AS" |
| AS → AT  | 5    | 过度治疗 | GT 说AS，模型说AT。模型因PSA速率/PSAD过高推荐治疗 |
| AS → CS  | 3    | 欠治疗 | GT 说AS（有癌），模型说CS（无癌观察） |
| CS → WW  | 1    | 欠治疗 | — |
| WW → AS  | 1    | 过度治疗 | — |
| WW → AT  | 1    | 过度治疗 | — |

### 逐类分析

**AT → AS（7 例，最危险）**：
- T2-013: PSA 113, GG5, GT说"PSMA-PET+治疗"，模型因PSAD高但选择AS
- T2-015: PSA 190, GG3, GT说"metastatic, staging"，模型选择AS
- T2-043: GT说"severe LUTS + csPCa，讨论手术"，模型选择AS
- T2-101: 81yr, GT说"staging+shared decision"，模型选择AS
- T2-106: GG2, GT说"life expectancy long, discuss treatment"，模型选择AS
- T2-107: GG3, GT说"prostatectomy"，模型选择AS
- T2-111: GT说"shared decision for prostatectomy vs RT"，模型选择AS

模式：模型过度依赖 PSAD 和 GG 级别，忽略了 GT 中"高PSA需staging+治疗"的临床判断。

**CS → AS（6 例）**：
- 多为"阴性活检+PI-RADS"病例，GT说继续观察（无癌），模型误判为AS（有癌）
- 模型未区分"有癌AS"和"无癌CS"的界限

**AS → AT（5 例）**：
- 模型因 PSA 速率/PSAD 过高推荐治疗，但 GT 认为 GG1 低危可继续 AS

### T2 结论

| 误判类型 | 数量 | 占比 | 可修性 |
|----------|------|------|--------|
| 欠治疗（AT→AS/CS, AS→CS, CS→WW） | 11 | 46% | **可修**：需增强治疗推荐逻辑 |
| 过度治疗（AS→AT, CS→AS, WW→AT/AS） | 13 | 54% | 部分可修 |
| WW 完全失败（0/2 正确） | 2 | — | 可修：增加 WW 规则 |

**主要可修方向**：
1. 高 PSA（>20）+ GG2+ 应推荐 AT 而非 AS（修复 AT→AS 的 7 例）
2. 阴性活检 + 低 PSA 应为 CS 而非 AS（修复 CS→AS 的 6 例）
3. 增加 WW 规则：高龄+低PSA+阴性活检+多并发症

---

## 四、T3 错例聚类（13 例）

### Event 错误方向

| 方向 | 数量 | 占比 |
|------|------|------|
| event=0 预测为 1（假阳性，过度预测复发） | 13 | **100%** |
| event=1 预测为 0（假阴性，漏判复发） | 0 | 0% |

**所有 13 例错误均为假阳性**——模型过度预测复发。

### Months 偏差统计

| case_id  | GT months | Pred months | 偏差(pred-gt) |
|----------|----------|-------------|-------------|
| T3-002   | 65.7     | 24.0        | -41.7       |
| T3-003   | 31.4     | 12.0        | -19.4       |
| T3-004   | 63.1     | 36.0        | -27.1       |
| T3-005   | 57.1     | 24.0        | -33.1       |
| T3-007   | 18.1     | 18.0        | -0.1        |
| T3-008   | 35.5     | 12.0        | -23.5       |
| T3-010   | 22.6     | 12.0        | -10.6       |
| T3-012   | 36.3     | 24.0        | -12.3       |
| T3-013   | 37.0     | 24.0        | -13.0       |
| T3-014   | 62.5     | 24.0        | -38.5       |
| T3-015   | 47.1     | 24.0        | -23.1       |
| T3-016   | 22.8     | 12.0        | -10.8       |
| T3-065   | 37.8     | 15.3        | -22.5       |

- 偏差范围：-0.1 ~ -41.7 个月
- 平均偏差：**-21.3 个月**（模型预测偏短）
- 中位偏差：-19.4 个月
- 12/13 例偏差 > 10 个月

### T3 结论

| 问题 | 现状 | 根因 |
|------|------|------|
| Event 假阳性 | 13/13 全为 FP | 模型倾向于预测复发（event=1） |
| Months 偏短 | 平均偏短 21.3 月 | 预测值集中在 12-36 月，GT 长尾到 60+ 月 |

**可修方向**：
1. Event 校准：提高 event=0 的预测阈值（当前倾向 event=1）
2. Months 校准：Cox 模型或分位数回归延长预测区间（Task 5 验证中）
3. 注意：C-index=0.835 仍然达标，说明 months 排序基本正确，只是绝对值偏短

---

## 五、综合结论

### 哲学冲突 vs 模型缺陷

| 任务 | 总错例 | 哲学冲突 | 模型缺陷 | 冲突占比 |
|------|--------|----------|----------|----------|
| T1   | 23     | 14       | 9        | 61%      |
| T2   | 24     | ~5       | ~19      | ~21%     |
| T3   | 13     | 0        | 13       | 0%       |
| **合计** | **60** | **~19** | **~41** | **~32%** |

### 可修的真缺陷优先级

1. **T1 B 类漏诊**（8 例）：PSAD 过度否决 P5/P4 + AS 确认活检遗漏 + 推理断裂
2. **T2 AT→AS 欠治疗**（7 例）：高 PSA + GG2+ 应推荐治疗
3. **T2 CS→AS 误判**（6 例）：阴性活检应 CS 而非 AS
4. **T3 event 假阳性**（13 例）：提高 event=0 阈值
5. **T3 months 偏短**（全局）：Cox 模型校准

### 不可修的 GT 哲学冲突

- T1 A 类：已确诊+高PSA 应治疗而非活检（13 例）
- T2 部分：GT 的个体化 shared decision 判断（少量）
