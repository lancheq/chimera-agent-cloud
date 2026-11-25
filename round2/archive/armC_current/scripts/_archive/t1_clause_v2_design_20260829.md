# T1 条款 v2 设计文档 — 2026-08-29（0 推理，纯设计）

> 基于 L2-round-1 复盘 + 全量 91 case 数据解剖。只读产出，未改任何代码。
> 应用需等 Step9-v2 GPU run 退出后由本地 agent 统一 apply（src/ 为 editable install）。

---

## B1 · dd011 vs A1 八例判别特征表

### 共同点（不可判别项）

- 8/8 A1 与 dd011、47c3 的 `enc_type` 均为 **"Re-evaluation - prior PCa diagnosis"**（enc_type 无法判别）
- 全部 `bx=Positive`、多为 PI-RADS 5
- dd011 与 47c3 的数据中**均有已验证分级**（bx_isup=2, Gleason 3+4）——"是否已确诊/已分级"不是判别项

### A1 八例（GT=no，治疗/分期语境）特征

| case | PSA | 分级记录(数据) | 治疗/分期信号(数据) | 判别权重 |
|---|---|---|---|---|
| 0615b2bd8504 | **160** | 有 | **PET×3**、cT3a | 极值PSA+分期 |
| 11345b8a7ab3 | **113** | 有 | **PET×2** | 极值PSA+分期 |
| 11fe9c6ca833 | **190** | 有 | 无 | 极值PSA |
| 01b643727538 | 9.3 | **ISUP3** verified | "previously diagnosed prostate malignancy" | 高危已验证 |
| 4b950525e6a8 | 19 | **ISUP4** | **RT×2**、follow-up 语境 | 高危+治疗 |
| c9d31197c533 | 10.9 | **Gleason 4+3(ISUP3)** | 无 | 高危已验证 |
| cce5d47c05dd | 12 | **ISUP2** | 无（PSAV 3.98） | **灰区** |
| dcd61cf9b443 | 8.3 | **Gleason 5** | **PET×3**、surveillance×3 | 高危+分期 |

**A1 判别信号（按强度排序）**：① 极值 PSA ≥ ~100（3/8）；② 明确分期/治疗工作流信号——PSMA-PET 分期、RT/ADT（4/8）；③ 已验证高危分级 ISUP≥3（5/8）。三者中 A1 命中 ≥1 的为 7/8；**cce5（ISUP2、无治疗信号）为唯一数据层与 TP 不可分者**。

### dd011d696387（GT=yes，条款 (2) 误杀）确认

- PSA 10、PI-RADS 5、PSAD 0.372、age 75、**bx_isup=2（Gleason 3+4）**
- **无任何治疗/分期信号**（无 PET/RT/ADT/极值 PSA）；PSA 轨迹波动上升 2.9→10
- GT 理由：*"Intermediate risk disease in 75yr... No targeted biopsy and unclear timing... Unclear if PIRADS5 was biopsied (limited size lesion, unclear if new lesion while lack of previous MRI). Biopsy to exclude higher-risk disease, should include targeted cores."* — **诊断问题未决**（新病灶/未活检病灶/缺旧 MRI 对照）
- **结论：dd011 是"仅确诊但治疗决策未定"型**——条款 (2) v1 的 "who have entered treatment or staging workup" 限定词被模型丢弃，退化成"prior positive → no"。

### 47c373e230b6（GT=yes，L2 翻 yes 成功）为何逃过条款 (2)

- 临床画像与 dd011 几乎相同（PSA 10/P5/prior positive/age 75/ISUP2）
- L2 free_text：*"PI-RADS 5 lesion... rising PSA kinetics... prior positive biopsy suggesting potential disease progression or grade upgrade"* → 模型走了**条款 (3) 进展逻辑**（新病灶+上升 PSA → 再活检）
- **逃过纯属 LLM 边界采样**（dd011 被判"已确诊→治疗"，47c3 被判"进展→活检"）——同一临床画像，v1 条款无法稳定区分。

### 条款 (2) v2 应加的限定词（B1 交付）

区分**"已进入治疗/分期流程"** vs **"仅确诊但治疗决策未定"**：

- **no（已进入治疗/分期）需满足至少一项**：
  a. 极值 PSA（≥ ~100）提示晚期/转移；
  b. 明确分期/治疗工作流：PSMA-PET 分期、ADT/激素、放疗、术后随访；
  c. 已验证高危分级（ISUP ≥ 3）+ 记录显示治疗为既定方向。
- **biopsy（仅确诊未定/未进入治疗）**：AS 随访、分级不明、**可能新发或未被活检过的 PI-RADS ≥ 4 病灶**、缺旧 MRI 无法对照、或仅"prior positive"而无上述 no 信号 → **标准指征规则照常适用（P4-5/PSAD>0.15/上升 PSA → 活检）**。
- **明确反滥用条款**：prior positive 本身、或已记录的中危分级（ISUP 1-2）**不构成** decline biopsy 的理由。

---

## B2 · A2 十例共性 → 条款 (3) 收紧

### A2 共性（10/10 GT=no）

| 模式 | case | GT 理由示例 |
|---|---|---|
| "需确认病灶是否长大" | 079636、17f110、1a4e0、2b3e | "if stable no biopsy, if increased I will do biopsy" |
| "需初始 ISUP/MRI" | 3464、37d8、3b6e | "Need initial ISUP grade and MRI in men on active surveillance" |
| "需对比旧 MRI" | 3cea、4c6b | "If stabile P3 lesion, no biopsy" / "can be followed up" |
| "AS + 现 MRI 正常" | 3cc8 | "low-risk cancer on AS. Now normal MRI" |

**共性结论**：GT=no 的前提是**进展未被证实**——病灶大小/新旧、初始分级、旧 MRI 对照等信息缺失 → 条件性 no。模型把 "prior positive + P4-5 + 单次 PSA 上升 + DRE nodule" 误当进展证据。

### 条款 (3) 收紧措辞草案（英文）

> On active surveillance, confirmatory biopsy is indicated ONLY on verified
> evidence of progression: ISUP upgrade on repeat biopsy, a NEW or ENLARGING
> PI-RADS ≥ 4 lesion compared with a prior MRI, or PSA doubling time < 3 years.
> A single PSA elevation, a stable lesion, or a DRE nodule alone does NOT
> constitute progression. When no prior MRI is available for comparison, do
> not assume the lesion is new.

**平衡风险**：47c3 型（P5 + rising PSA + 无旧 MRI、GT=yes）会被收紧条款误伤 → 措辞需保留 "rising PSA **with a high-suspicion (PI-RADS ≥ 4) lesion**" 为进展触发，与"无对照时默认非新发"形成张力。设计取舍：优先保住 A2（10 例）的修复，47c3 型残留风险用 L2-round-2 测量。

---

## B3 · 条款 v2 完整草案（英文，可 paste）

`prompts.py` 的 T1 小节现有 4 条校准规则（PSA alone / PI-RADS 3 equivocal / prior negative / RP）**保持不变**；将 L2 已实施的 `_T1_BALANCE_CLAUSE` 替换为以下 v2（三条，逻辑重构）：

```text
* These guardrails do NOT override clear biopsy indications:

  (1) A NEW PI-RADS ≥ 4 lesion, PSAD > 0.15, or rising PSA with prior
      negative biopsy warrants biopsy — prior negative reduces but does
      not eliminate risk.

  (2) A prior positive biopsy establishes a diagnosis but does NOT by
      itself settle the biopsy question. A "no" applies ONLY when the
      record shows the patient has moved past diagnosis into treatment
      or staging — e.g., very high PSA (roughly > 100 ng/mL) indicating
      advanced disease, an explicit staging/treatment workup (PSMA-PET
      staging, hormone/ADT, radiotherapy), or a verified high-grade
      cancer (ISUP ≥ 3) with treatment already recommended. In all
      other prior-positive settings — active surveillance, uncertain
      grade, a possible NEW or previously unbiopsied PI-RADS ≥ 4
      lesion, or a lesion that cannot be compared with a prior MRI —
      the standard indication rules above still apply and biopsy
      remains indicated. Documented intermediate-risk disease (ISUP
      1-2) or a positive biopsy alone is NOT a reason to decline biopsy.

  (3) On active surveillance, confirmatory biopsy is indicated ONLY on
      verified evidence of progression: ISUP upgrade on repeat biopsy,
      a NEW or ENLARGING PI-RADS ≥ 4 lesion compared with a prior MRI,
      or PSA doubling time < 3 years. A single PSA elevation, a stable
      lesion, or a DRE nodule alone does NOT constitute progression.
      When no prior MRI is available for comparison, do not assume the
      lesion is new.
```

> 语义变化 vs v1：条款 (2) 从"已确诊→no"收紧为"已进入治疗/分期→no + 反滥用限定"；条款 (3) 从"进展即活检"收紧为"已验证进展才活检 + 负向列举"。

### 逐 case 预期效果表（0827 全量口径）

**10 FN（GT=yes，pred=no）→ 预期修复 8-9/10**

| case | 关键特征 | v2 预期 | 置信 |
|---|---|---|---|
| 0169468160c6 | PSA187+P5+prior pos | 极值 PSA→"advanced/staging"→可能仍 no（边界） | 中 |
| 075dd468b384 | P4+PSAD0.14+prior pos | 条款(2)收紧→标准规则→**yes** | 高 |
| 36c92fb424ac | P4+PSAD0.08+prior neg | 条款(1)→**yes** | 高 |
| 376de00c8131 | P4+PSAD0.09+prior neg | 已翻（L2 确认）→保持 yes | 高 |
| 47c373e230b6 | P5+PSAD0.27+prior pos | 已翻（L2 确认）→保持 yes | 高 |
| 48fb60dfd22f | P5+PSAD0.986+无活检 | 条款(1)→**yes** | 高 |
| 4d54f04e26ae | P4+PSAD0.352+prior neg | 条款(1)→**yes** | 高 |
| 4edb17f79e46 | P4+PSAD0.16+prior neg | 条款(1)→**yes** | 高 |
| dd011d696387 | P5+PSAD0.372+prior pos+无治疗信号 | 条款(2)收紧→**yes**（目标修复） | 高 |
| e001f4d06517 | P4+PSAD0.08+prior pos | 条款(2)收紧→标准规则→**yes** | 中高 |

**19 FP（GT=no，pred=yes）→ 预期修复 14-16/19**

A1×8：

| case | 判别信号 | v2 预期 | 置信 |
|---|---|---|---|
| 0615b2bd8504 | PSA160+PET | 极值+分期→no（保持） | 高 |
| 11345b8a7ab3 | PSA113+PET | 极值→no（保持） | 高 |
| 11fe9c6ca833 | PSA190 | 极值→no（保持，L2 已翻 no） | 高 |
| 01b643727538 | ISUP3 verified | 高危+治疗语境→no（保持） | 中高 |
| 4b950525e6a8 | ISUP4+RT | 高危+治疗→no（保持） | 高 |
| c9d31197c533 | ISUP3(4+3) | 高危→no（保持） | 中高 |
| cce5d47c05dd | ISUP2+无治疗信号 | **灰区：可能翻 yes → 新 FN（已知取舍）** | 低 |
| dcd61cf9b443 | PET×3+Gleason5 | 分期工作流→no（保持） | 高 |

A2×10（条款(3)收紧）：

| case | 关键特征 | v2 预期 | 置信 |
|---|---|---|---|
| 079636ac5644 | P4+需对比 | →**no** | 中高 |
| 17f110c9e142 | P5+需对比 | →**no**（47c3 型反例风险） | 中 |
| 1a4e0cf3b927 | P5+PSAD0.427 | →**no**（GT"most likely no"） | 中低 |
| 2b3ef1d3a505 | P5+PSAD0.06 | →**no** | 中高 |
| 34647958aba3 | P5+需初始分级 | →**no** | 中高 |
| 37d8ae9b27f1 | P4+需初始MRI | →**no** | 中高 |
| 3b6ea1920967 | P4+需初始分级 | →**no** | 中高 |
| 3cc88c81f35a | P2+AS+MRI正常 | →**no**（最强条款3样本） | 高 |
| 3ceacc9b660b | P3+需对比 | →**no** | 中高 |
| 4c6b2b2bbf94 | P4+低PSAD | →**no** | 中高 |

B×1：175e6ad47991（P5+PSAD0.28+age79+prior neg）→ 条款(1)支持 yes → **保持 FP（残留）**，置信高。

**TP 误伤面（18 个 prior-positive TP，GT=yes）→ 预期误伤 0-1/18**

- 条款(2) v2 的 no 三条件逐一核对：极值 PSA（18 例均无）；治疗/分期信号（16/18 数据中无；138092/d5ce 的 "pet" 为子串假阳性——匹配到 "probability"，需提示词用 "PSMA-PET staging" 精确措辞）；高危 ISUP≥3+治疗建议（仅 **b9f0b7018502**（ISUP4）低风险命中——其 GT 理由自相矛盾"Rather than new biopsy"却 GT=yes，属 GT 不一致样本）
- 条款(3)收紧：18 例多数有"新/增大 P4-5 + 上升 PSA"进展信号 → 保护
- **结论：v2 误伤面 ≈ 0（b9f0 为唯一低风险项，需 L2-round-2 验证）**

**净预期**（vs 0827）：FN 10→1-2；FP 19→4-5（A1 残留 1-2 + A2 残留 0-1 + B 1）；TP 误伤 0-1。yes 类 recall ↑，no 类 precision ↑ → macro F1 预期从 0.642 上升（量级待 L2/L3 实测）。

---

## B4 · L2-round-2 选例清单（待本地审批，不执行）

| # | case_id | 用途 | 期望 | 置信 |
|---|---|---|---|---|
| 1 | PT-pseudo_dd011d696387 | 条款(2) v2 正例（误杀修复） | **yes** | 高 |
| 2 | PT-pseudo_47c373e230b6 | 条款(2) v2 反例（L2 已翻，回归确认） | yes | 高 |
| 3 | PT-pseudo_b9f0b7018502 | **prior-positive TP 高危误伤面检测**（ISUP4，GT=yes） | yes | 低（唯一风险项） |
| 4 | PT-pseudo_0cdfb9410718 | prior-positive TP 常规误伤面（AS+分级不明） | yes | 高 |
| 5 | PT-pseudo_3cc88c81f35a | 条款(3) 最强样本（AS+MRI 正常） | **no** | 高 |
| 6 | PT-pseudo_17f110c9e142 | A2 型 FP + 47c3 型反例张力（P5+需对比） | no | 中 |
| 7 | PT-pseudo_0615b2bd8504 | A1 回归确认（L2 已翻 no） | no | 高 |
| 8 | PT-pseudo_cce5d47c05dd | （可选）A1 灰区：检测条款(2) v2 是否误伤 | no | 低（接受翻 yes 为已知取舍） |
| 9 | PT-pseudo_0169468160c6 | （可选）FN 边界：极值 PSA 型 | yes | 中 |

核心 7 例 + 可选 2 例。通过标准建议：① 全 7 例期望方向满足 6/7 以上；② b9f0（#3）不得翻 no（误伤面 ≤1 且可解释）；③ #6 若翻 yes 需本地判定条款(3)收紧是否过强。

---

## 附：设计风险登记

1. **cce5 型不可分灰区**：ISUP2+P5+无治疗信号（GT=no）与 dd011（GT=yes）临床近同——条款 (2) v2 收紧后 cce5 大概率翻 yes（新 FN）。属不可消除的 GT 主观差异，接受为已知取舍并在 L2-round-2 记录。
2. **47c3/17f110 型条款(3)张力**：P5+rising PSA+无旧 MRI——条款(3)收紧可能误伤 GT=yes 者（47c3 已确认翻 yes，17f110 待测）。
3. **b9f0 GT 不一致**：GT 理由"Rather than new biopsy"但决策=yes——若 v2 使其翻 no，属 GT 侧瑕疵，不算模型回归，需本地裁决。
4. **极值 PSA 阈值（~100）**：仅 3/8 A1 命中；其余靠"已验证高危+治疗语境"。若模型对"治疗语境"识别不稳，01b6/c9d3 可能翻 yes——L2-round-2 已覆盖 0615 但未覆盖 01b6/c9d3，如需可在 L3 前补测。

---

## 声明

本任务为纯设计：0 次模型推理、未启动 server、未跑 case、**未修改任何代码文件**（src/ 未动）；仅读取 `test/output.final_20260826/task1/`、`data/task1/` 与 `test/output.t1_l2_20260829/` 既有产物。未读取 Step9-v2 运行目录。应用时机：等 Step9-v2 GPU run 退出后由本地 agent 统一 apply。

---

## 本地 agent 审批结论（2026-08-29）

**B3 条款 v2 文本：批准**（语义重构方向正确：条款(2) "已确诊→no" 收紧为
"已进入治疗/分期→no + 反滥用限定"；条款(3) 收紧为已验证进展）

**B4 选例：批准 + 扩充为 11 例**（一次 run 成本 ~15min，信息密度优先）：

在原 9 例（核心7+可选2）基础上，追加风险登记#4 点名的两例：
- PT-pseudo_01b643727538（ISUP3 verified，预期保持 no）——"已验证高危+治疗语境"识别稳定性直接测试
- PT-pseudo_c9d31197c533（ISUP3 4+3，预期保持 no）——同上

L2-round-2 通过标准（修订）：
① 11 例期望方向满足 ≥9/11
② b9f0（#3）与 0cdfb（#4）不得翻 no（TP 误伤面 = 0 是硬要求）
③ 3cc8（#5）必须翻 no（条款(3) 最强样本）
④ dd011（#1）必须翻 yes（条款(2) v2 目标修复项）
⑤ cce5 翻 yes 记录为已知取舍（不算 FAIL）
⑥ 01b6/c9d3 保持 no（追加项，同①计入）

应用时机不变：等 Step9-v2 退出 + auto-eval 产出 + 本地读报告裁定后，
由本地 agent 统一 apply（单 commit，含条款 v2 替换 _T1_BALANCE_CLAUSE）。
