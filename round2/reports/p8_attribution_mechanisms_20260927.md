# P8：归因路径上的三个休眠机制 + 层1c 修复（2026-09-27）

> **缘起**：用户记得「有一个引用文献做出的机制，后来消融测试关掉了，提交时间紧张没细究」。
> 排查后确认该类机制**不止一个**，且问题不是「被证否」，而是**从未真正生效**。

---

## 1. 背景：T1 归因分量的失分结构

评分中 `variable_weights`（序数距离，权重 **0.25**）与 `important_decisive_factors`
（集合 F1，权重 **0.15**）**由同一张 `variable_weights` 表派生 → 合计 0.40 权重**。

对 91 例真标做诊断（`tools/t1_weight_diag.py`）：

| 档位 | GT 占比 | 我们占比 | 差值 |
|---|---|---|---|
| **not_used** | 14.0% | **35.6%** | **+21.6pp** |
| **noted** | 45.6% | **30.2%** | **−15.4pp** |
| important | 31.2% | 25.4% | −5.8% |
| decisive | 9.2% | 8.8% | −0.4% |

- **序数不一致率 61.2%**（一致仅 38.8%）
- 混淆矩阵主错误：**GT `noted` → 我们判 `not_used`，占该行 48%**
- 集合指标：P=0.701 / R=0.607 / F1=**0.625**
- 漏声明 152 次 > 过度声明 95 次；**`age` 系统性地漏了 57 次**

**结论：主导错误不是"标错重要变量"，而是 `not_used` 被大量滥用（把中档变量判成"未使用"）。**

---

## 2. 排查结果：三个引用文献引入的机制，全部休眠

### 2.1 `atomic_fact_check`（消融 A 组）—— 判定结果被丢弃

- 机制：`decompose_claims` 把 free_text 拆成原子 claim，逐条判定 `TRUE/FALSE/UNVERIFIED`
- 状态：`atomic_check: true` **默认开启**，消融时代的 schema bug **已修**
- **问题**：判定结果被丢弃。`form_fill.py` 注释原文：「`_fact_check_flags` 无任何下游」
  且该字段的收集被 `if result.has_false:` 门控
- **实测（91 例 / 591 条 claim）：**

| 判定 | 数量 | 占比 |
|---|---|---|
| TRUE | 252 | 42.6% |
| **FALSE** | **0** | **0.0%** |
| UNVERIFIED | 339 | 57.4% |

**FALSE 从未发生 → `has_false` 门控永不成立 → 该字段从未被填充过（双重惰性）。**
唯一落地的效果是 confidence 降级（该分量权重 0.20，我们已是 0.900，接近满分）。

### 2.2 `_fact_check_flags` —— 死代码

见 2.1：受 `has_false` 门控，而 FALSE 实测 0/591。

### 2.3 `_calibrate_weights`（GT 先验校准）—— 规则瞄准了错误的档位

- 已存在的资产：`resources/weight_calibration.json`，由 `scripts/calibrate_weights_stats.py`
  从 GT 统计出的「变量 × 决策 → 权重分布」先验
- 现行层 1 只有两条规则：

```python
if llm_level == "decisive" and gt_decisive_prob < 0.30:   # 降级
elif llm_level == "important" and gt_decisive_prob > 0.60: # 升级
```

- **实测命中次数（91 例）：**

| 条件 | 命中 |
|---|---|
| 现行「decisive 应降级」 | **0** |
| 现行「important 应升级」 | **0** |
| **「not_used 应提升」（现行完全不处理）** | **193** |

- 校准表覆盖全部 10 个变量、无跳过、函数**确实被调用**（日志里的加载即由其触发），
  但两条规则在本数据上**一次都没命中**，且**完全不覆盖占比最大的 `not_used` 错误**。
- 层 1 的降级还是**从先验随机重采样**（`_random.choices`），而非依据证据修正。

---

## 3. 修改内容（层 1c）

**位置**：[form_fill.py](file:///Users/lancheq/Desktop/项目汇总/Chimera-agent/src/chimera_agent_baseline/agent/form_fill.py) 的 `_calibrate_weights` 层 1，
新增第三条分支 + `_prior_median` 助手。

**规则**：LLM 给 `not_used`，但 GT 先验该变量 `not_used` 概率 < 0.15（罕见档位）
→ 提升到**先验中位数**档位。

**为何用中位数而非众数**：序数评分下中位数最小化期望绝对偏差。

**可独立消融**：环境变量 `CHIMERA_PROMOTE_NOT_USED=0` 关闭（默认开启）。
备份：`form_fill.py.bak_p8`。

---

## 4. 验证：样本内 vs 交叉验证（诚实口径）

**方法论要点**：`weight_calibration.json` 由**这同一批 91 例 GT** 统计而来，
故在其上直接重放是**样本内**结果，偏乐观。校准只依赖频率表、不涉及 LLM 调用，
因此可做严格交叉验证：每折**只用训练折 GT** 重建先验表，再作用于留出折。

`tools/p8_cv_validate.py`（10 seed × 5 折 = 50 折配对）：

| 口径 | 序数一致率 | 平均距离 | 集合 F1 |
|---|---|---|---|
| 基线（无校准） | 38.8% | 0.743 | 0.625 |
| 样本内（全量表） | 51.8% | 0.538 | 0.685 |
| **交叉验证（诚实）** | **51.4%** | **0.544** | **0.682** |
| **诚实增益** | **+12.6pp** | **−0.199** | **+0.058** |

- **Δ/SE = 27.86**（50 折，σ=0.032，SE=0.005）
- **样本内 vs 诚实差距仅 +0.4% / +0.002 → 几乎没有过拟合，增益真实**

**档位分布对齐（ON 后）**：

| 档位 | GT | 未校准 | ON |
|---|---|---|---|
| not_used | 13.7% | 35.6% | **14.4%** |
| noted | 45.6% | 30.2% | **40.8%** |
| important | 31.2% | 25.4% | **36.0%** |
| decisive | 9.2% | 8.8% | 8.8% |

`not_used` 的 +21.9pp 过量**基本消除**。提升明细：age 74、dre 44、vol 33、bx 23、psad 19
—— `age`（系统性漏掉 57 次的变量）正是被提升最多的，与诊断一致。

**注意**：精确率由 0.701 降到 0.669，召回率由 0.607 升到 0.759。官方该分量按 **F1** 计分，
F1 提升 +0.058 为净正；若计分口径含精确率加权则需重估。

---

## 5. 后续：grounding 的交互效应与量化（P8b）

### 5.1 用官方评估器核实真实得分

调入 `CHIMERA-agent/evaluation/evaluate.py` 的**官方函数**（而非自造代理指标），
确认权重（`evaluate.py:1228-1233`）：

| 分量 | 权重 | 公式 |
|---|---|---|
| `variable_weight_score` | 0.25 | `1 − mean(|Δ序数|/3)` ← 序数 MAE |
| `important_decisive_factor_score` | 0.15 | important+decisive 集合 F1 |
| `section_grounding_score` | 0.15 | `grounded/(grounded+ungrounded)` |

**关键发现**：`section_grounding_score(pred)` **只吃 pred、不吃 GT** —— 它是**自一致性**检查
（被加权变量的 `primary_sections` 是否出现在**自己的** reveal_sequence 里），
与「对比 GT 病理学家揭示段落」是两回事（后者是 `tool` 分量的口径）。

### 5.2 层 1c 的真实得分（含交互效应）

| 分量 | 基线 | P8 后 | Δ | 权重 |
|---|---|---|---|---|
| variable_weight_score | 0.7524 | **0.8207** | **+0.0683** | 0.25 |
| important_decisive_factor | 0.6247 | **0.6845** | **+0.0598** | 0.15 |
| **section_grounding_score** | 0.9048 | **0.8806** | **−0.0242** | 0.15 |
| **case_score 合计** | 0.4175 | 0.4399 | **+0.0224** | — |

**交互效应**：把 `bx`/`dre` 从 `not_used` 提升为"被加权"后，它们**新纳入了 grounding 检查**，
而其 primary section 未揭示 → 变成未 grounded（`bx` 17→39、`dre` 13→28）。
若无官方评估器，这个 −0.0242 会被完全漏掉（手算乐观值 +0.0253）。

### 5.3 section 映射与工具调用的真实关系

`evaluation/ground_truth/section_variable_mapping.json` 给出（variable → primary_sections）：

| 变量 | primary_sections |
|---|---|
| `bx` | `section_s3-path`, `section_s3-prev` |
| `dre` | `section_s3-labs` |
| **`comorbidity`** | **`section_s3-comorb`** |

91 例的**真实 `called_tools`**（从日志精确解析，非子串匹配）：

| 工具 | 调用次数 | 对应 section |
|---|---|---|
| `get_mri_report` | **91** | section_s3-mri |
| `get_psa_trend` | **91** | section_s3-psa |
| `get_lab_results` | **55** | section_s3-labs |
| `get_previous_notes` | **55** | section_s3-prev |
| `get_family_history` | **47** | section_s3-fh |
| **`get_pathology_report`** | **11** | section_s3-path |
| `search_guidelines` | 8 | — |
| **（无 comorbidity 工具）** | **0** | section_s3-comorb |

**纠正一个先前的错误假设**：我先怀疑「reveal_sequence 少报了自己调用过的工具」，
实测 **少报 0 例** —— `reveal_sequence` 与 `called_tools` 完全一致，没有免费修复。

**真实瓶颈是工具选择**：
- GT 把 `bx` 标为 important/decisive **64 次**，但 `get_pathology_report` 只调了 **11 次** → `bx` 大量未 grounded
- `dre` 需要 labs，labs 只调 55 次
- **`comorbidity` 没有任何工具能揭示其 primary section** → 只要加权就必然未 grounded（27 例）

### 5.4 量化后续修复（官方评估器，诚实口径）

| 方案 | var_weight | factorF1 | grounding | case合计 | Δ |
|---|---|---|---|---|---|
| 基线 | 0.7524 | 0.6247 | 0.9048 | 0.4175 | — |
| **P8 层1c（已实现）** | 0.8207 | 0.6845 | 0.8806 | 0.4399 | **+0.0224** |
| **+ 真实补 section**（调 path/prev/labs，需改工具策略） | 0.8207 | 0.6845 | **0.9414** | 0.4491 | **+0.0315** |
| **+ 且不加权 comorbidity** | 0.8131 | 0.6845 | **0.9729** | **0.4519** | **+0.0344** |

- 「+真实补 section」**只补确有工具可调用的 section**（bx→path/prev、dre→labs），
  **不给 comorbidity 伪造 reveal**（无对应工具）。先前一版误把 comorbidity 计入，得 0.9738，已弃用。
- 最后一步「不加权 comorbidity」使 var_weight 略降（0.8207→0.8131）但 grounding 升更多 → 净正。

**结论：完整方案的 T1 case_score 增益约 +0.0344**（其中 +0.0224 已由交叉验证确认为诚实增益；
后两步的上界**取决于是否落实工具策略改动**，需重跑验证）。

---

### 5.5 纯后处理方案扫描（P8c，无需重跑）

`variable_weights` 的校准是纯后处理、`section_grounding` 只吃 pred 自身
→ 以下方案都能在已有预测上重放并用官方评估器测量。交叉验证配对（10×5=50 折）：

| 方案 | Δvar_weight | ΔfactorF1 | Δgrounding | **Δcase合计** | Δ/SE |
|---|---|---|---|---|---|
| **V1 层1c（已实现，最优）** | +0.0664 | +0.0572 | −0.0280 | **+0.0210** | **23.6** |
| V3 全变量吸附先验中位数 | +0.1068 | +0.1231 | **−0.1505** | +0.0226 | 16.7 |
| V4 仅相差≥2 靠一步 | +0.0440 | +0.0332 | −0.0188 | +0.0132 | 25.9 |

**结论：V1 已是该族最优。** V3 总量相近但方差大得多（Δ/SE 16.7）、grounding 崩 0.15，
且完全丢弃 LLM 的逐例信号 → 更脆弱。**不做过度工程，保持 V1。**

### 5.6 层 1d：不可 reveal 变量降级（已实现）

由 5.3 的映射事实推出：`comorbidity` 的 primary section 只有 `section_s3-comorb`，
**没有任何工具能揭示它** → 只要加权就必然未 grounded。
故在任务 1 将其降为 `not_used`（以 var_weight 换 grounding）。

| 方案 | var_weight | factorF1 | grounding | case合计 |
|---|---|---|---|---|
| 基线 | 0.7524 | 0.6247 | 0.9048 | 0.4175 |
| P8 层1c | 0.8207 | 0.6845 | 0.8806 | 0.4399 |
| **P8 层1c + 层1d** | 0.8131 | 0.6845 | **0.9098** | **0.4424** |

层 1d 净增量 **+0.0025**。环境变量 `CHIMERA_DEMOTE_UNGROUNDABLE=0` 可关闭。

### 5.7 已完成部分的最终得分（官方评估器实测）

| 分量 | 基线 | P8 后 | Δ | 权重 |
|---|---|---|---|---|
| variable_weight_score | 0.7524 | **0.8131** | **+0.0607** | 0.25 |
| important_decisive_factor | 0.6247 | **0.6845** | **+0.0598** | 0.15 |
| section_grounding_score | 0.9048 | **0.9098** | **+0.0051** | 0.15 |
| **case_score 合计（这三项）** | 0.4175 | **0.4424** | **+0.0249** | — |

**全部为纯后处理改动，无需重跑 LLM。** 层 1c 的增益已由交叉验证确认为诚实
（过拟合幅度仅 +0.4%）；层 1d 为 +0.0025 的样本内估计，方向由「无工具可揭示」
这一结构性事实支撑。

**仍剩**：`bx`（17→39）、`dre`（13→28）未 grounded —— 需落实工具策略改动
（加权 `bx` 时调 `get_pathology_report`/`get_previous_notes`，加权 `dre` 时调 `get_lab_results`），
上界见 5.4，**必须重跑才能验证**。

### 5.8 `age` 根因调查：已被层 1c 修复，无需 prompt 改动

`age` 是 T1 中**唯一不影响 grounding 的变量**（`primary_sections=[]`、`always_available_baseline=true`），
故加权它零成本。诊断显示存在严重的系统性偏差：

| age 权重 | GT | 我们（原始） | 我们（P8 后） |
|---|---|---|---|
| **not_used** | **1** | **74** | **0** |
| noted | 31 | 15 | 15 |
| **important** | **56** | **2** | **76** |
| decisive | 3 | 0 | 0 |

GT 有 65% 的 case 把 age 标为 `important/decisive`，而原始输出 81% 标成 `not_used`。
校准表中 age 的 `not_used` 先验仅 **0.011**、众数为 `important`(0.615)。

**但层 1c 的触发条件（`not_used` 且先验 not_used<0.15）恰好命中** →
**age 的 74 次 `not_used` 已全部被提升**（提升明细中 `age: 74` 为最多），
P8 后分布已接近 GT。**因此不需要额外的 prompt 侧改动**，否则是重复劳动。
这也是层 1c 增益（+12.6pp 序数一致率）的主要来源。

### 5.9 ★ 重要限制：本地无法测量 `tool` 分量

`cost_aware_tool_score`（权重 0.15）= `|我们揭示 ∩ GT揭示| / |我们揭示|`
（**惩罚多余工具、不惩罚遗漏**）。模拟得到我们 **tool = 0.0000**。

根因是 `_reveal_keys` 只接受 **dict 格式**的 reveal 项：

```python
for item in seq:
    if not isinstance(item, dict):
        continue          # ← 纯字符串项被跳过
    k = item.get("key")
```

而**本地 GT 的 `reveal_sequence` 是纯字符串列表**
（`['radiology_report', 'psa_trend', 'previous_notes', 'laboratory_results']`）
→ `_reveal_keys(gt) = []` → 交集恒空 → `tool = 0`。

**这是本地 GT 格式导致的测量假象，不是我们的缺陷**（平台 tool 分约 0.995，
说明平台 GT 用 dict 格式且 section 词表比本地这 4 个更宽）。

**由此推翻先前 5.4 的重跑建议**：
- 「补 section」对 `tool`（0.15）的影响**本地无法验证**，且原则上可能为负
  （该指标惩罚多余工具，而我们已在调用 `get_family_history`(47)、
  `get_pathology_report`(11)、`search_guidelines`(8)）
- 故**不应**为「工具策略改动」发起 3.2h 重跑

**本地可可靠测量的只有不依赖 GT reveal 格式的三项**：
`variable_weight`、`important_decisive_factor`、`section_grounding`
（后者只吃 pred 自身）→ 即 5.7 的 **+0.0249** 是可信的。

---

## 6. 待办

| # | 事项 | 状态 |
|---|---|---|
| 1 | 层 1c 已在 91 例上诚实验证 | ✅ 完成 |
| 2 | **端到端重跑 91 例确认 LLM 侧无副作用** | ⏳ 待做（~3.2h）|
| 3 | 平台 dev 提交确认（T2 本地 CV 已验证可迁移，T1 未验证）| ⏳ 待上传窗口 |
| 4 | 把 `atomic_fact_check` 的 UNVERIFIED 信号接到 section grounding（0.781）| ⏳ 待做 |
| 5 | `age` 盲区的 prompt 侧根因（为何模型系统性忽略年龄）| ⏳ 待做 |
| 6 | 教师质量前置检验（API，预算已批准 ≤5 元，临时环境变量注入不落盘）| ⏳ 待做 |

### 重要限制（必须记录）

- **T1 本地 GT 只有 91 例 `PT-pseudo_*`**，而平台 dev 是官方 `BX_*` 用例。
  两者分布不同（本地 69.2% vs 平台 dev 0.80）→ **T1 的本地↔平台迁移性未经验证**，
  与 T2 不同（T2 已验证本地诚实 CV 能复现 dev 失败模式）。
  本改动的增益在 91 例上确定，**平台收益需实测确认**。
- 本地 T1 基线（当前配置，`test/output.night_t1_base`）：**decision 63/91 = 69.2%**，macro F1 0.593。

---

## 6. 变更记录

- 2026-09-27：创建。排查出三个休眠机制；诊断出 `not_used` 过量是主导错误；
  实现层 1c 并通过交叉验证（Δ序数一致率 +12.6pp、Δ集合 F1 +0.058、Δ/SE 27.9、
  过拟合幅度仅 +0.4%）。