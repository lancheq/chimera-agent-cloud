# self_refine 消融实验报告

> 生成时间: 2026-08-26 23:38:01
> 实验设计: 30 case (T1:12 + T2:10 + T3:8) A/B 对比
> 配置A (baseline): self_refine=false, atomic_check=false
> 配置B (self_refine): self_refine=true, atomic_check=false

## 1. Decision Correctness 对比

| Task | 指标 | Baseline | Self-refine | Δ |
|------|------|----------|-------------|---|
| T1 | Accuracy | 66.7% (8/12) | 58.3% (7/12) | -8.3% |
| T1 | Macro F1 | 0.657 | 0.580 | -0.077 |
| T2 | Accuracy | 80.0% (8/10) | 66.7% (6/9) | -13.3% |
| T2 | Macro F1 | 0.639 | 0.367 | -0.272 |
| T3 | Accuracy | 50.0% (4/8) | 50.0% (4/8) | +0.0% |
| T3 | Macro F1 | 0.333 | 0.333 | +0.000 |

## 2. Confidence 分布对比

| Task | 配置 | clear | borderline | uncertain | MISSING |
|------|------|-------|------------|-----------|---------|
| T1 | Baseline | 5 (42%) | 4 (33%) | 3 (25%) | 0 |
| T1 | Self-refine | 6 (50%) | 1 (8%) | 5 (42%) | 0 |
| T2 | Baseline | 2 (20%) | 5 (50%) | 3 (30%) | 0 |
| T2 | Self-refine | 0 (0%) | 5 (56%) | 4 (44%) | 0 |
| T3 | Baseline | 0 (0%) | 0 (0%) | 0 (0%) | 8 |
| T3 | Self-refine | 0 (0%) | 0 (0%) | 0 (0%) | 8 |

Uncertain 比例变化:
  - T1: 25% → 42% (+17%)
  - T2: 30% → 44% (+14%)
  - T3: 0% → 0% (+0%)

## 3. 错误方向聚类

### Task 1

| 错误方向 | Baseline | Self-refine |
|----------|----------|-------------|
| false_negative | 2 | 3 |
| false_positive | 2 | 2 |
| **Total** | **4** | **5** |

#### T1 错误 case 明细

| Case | GT | Baseline Pred | SR Pred | Baseline Err? | SR Err? | 变化 |
|------|-----|---------------|---------|---------------|---------|------|
| PT-pseudo_0020cfca66c8 | no | yes | ✓ | ✗ | ✓ | 🟢 修复 |
| PT-pseudo_0cdfb9410718 | yes | ✓ | no | ✓ | ✗ | 🔴 新增错误 |
| PT-pseudo_152344add0f1 | yes | ✓ | no | ✓ | ✗ | 🔴 新增错误 |
| PT-pseudo_175e6ad47991 | no | yes | yes | ✗ | ✗ | ⬜ 未变 |
| PT-pseudo_20ba3f0f6ca0 | yes | no | ✓ | ✗ | ✓ | 🟢 修复 |
| PT-pseudo_4a2fa7bc3595 | yes | no | ✓ | ✗ | ✓ | 🟢 修复 |
| PT-pseudo_4bfd4ec864d8 | yes | ✓ | no | ✓ | ✗ | 🔴 新增错误 |
| PT-pseudo_c55496c11421 | no | ✓ | yes | ✓ | ✗ | 🔴 新增错误 |

### Task 2

| 错误方向 | Baseline | Self-refine |
|----------|----------|-------------|
| false_de_escalation | 1 | 1 |
| other | 1 | 1 |
| over_treatment | 0 | 1 |
| **Total** | **2** | **3** |

#### T2 错误 case 明细

| Case | GT | Baseline Pred | SR Pred | Baseline Err? | SR Err? | 变化 |
|------|-----|---------------|---------|---------------|---------|------|
| T2-019 | active_treatment | ✓ | active_surveillance | ✓ | ✗ | 🔴 新增错误 |
| T2-021 | continued_surveillance | ✓ | active_surveillance | ✓ | ✗ | 🔴 新增错误 |
| T2-032 | watchful_waiting | active_surveillance | active_treatment | ✗ | ✗ | 🟡 改向 |
| T2-111 | active_treatment | active_surveillance | ✓ | ✗ | ✓ | 🟢 修复 |

### Task 3

| 错误方向 | Baseline | Self-refine |
|----------|----------|-------------|
| false_recurrence | 4 | 4 |
| **Total** | **4** | **4** |

#### T3 错误 case 明细

| Case | GT | Baseline Pred | SR Pred | Baseline Err? | SR Err? | 变化 |
|------|-----|---------------|---------|---------------|---------|------|
| T3-005 | 0 | 1 | 1 | ✗ | ✗ | ⬜ 未变 |
| T3-016 | 0 | 1 | 1 | ✗ | ✗ | ⬜ 未变 |
| T3-028 | 0 | 1 | 1 | ✗ | ✗ | ⬜ 未变 |
| T3-069 | 0 | 1 | 1 | ✗ | ✗ | ⬜ 未变 |

## 4. Trace Audit 统计

### 4.1 Thinking 污染率

| 检查点 | 污染数 | 比例 |
|--------|--------|------|
| Critique 输出 | 30/30 | 100% |
| Baseline agent 输出 | 1/30 | 3% |
| Self-refine agent 输出 | 1/30 | 3% |
| Refined transcript | 1/30 | 3% |
| Baseline plan 输出 | 1/30 | 3% |
| Self-refine plan 输出 | 0/30 | 0% |

### 4.2 Refine 执行率

| 指标 | 数量 | 比例 |
|------|------|------|
| Refine 触发 (发现 issues) | 0/30 | 0% |
| Critique 无 issues | 30/30 | 100% |
| 无 critique 文件 | 0/30 | 0% |
| 总 issues 发现数 | 0 | - |

### 4.3 Refine 决策变更

| 指标 | 数量 | 比例 |
|------|------|------|
| 决策变更 | 11/29 | 38% |
| 决策不变 | 18/29 | 62% |
| Confidence 变更 | 14/29 | 48% |

#### 决策变更 case 明细

| Case | Task | Baseline | Self-refine | Baseline Conf | SR Conf | Refine触发 | Thinking污染 |
|------|------|----------|-------------|---------------|---------|-----------|-------------|
| PT-pseudo_4a2fa7bc3595 | T1 | no | yes | uncertain | clear | ✗ | ⚠️ |
| PT-pseudo_20ba3f0f6ca0 | T1 | no | yes | uncertain | clear | ✗ | ⚠️ |
| PT-pseudo_0cdfb9410718 | T1 | yes | no | borderline | uncertain | ✗ | ⚠️ |
| PT-pseudo_0020cfca66c8 | T1 | yes | no | borderline | uncertain | ✗ | ⚠️ |
| PT-pseudo_4bfd4ec864d8 | T1 | yes | no | clear | uncertain | ✗ | ⚠️ |
| PT-pseudo_c55496c11421 | T1 | no | yes | uncertain | clear | ✗ | ⚠️ |
| PT-pseudo_152344add0f1 | T1 | yes | no | clear | uncertain | ✗ | ⚠️ |
| T2-111 | T2 | active_surveillance | active_treatment | uncertain | borderline | ✗ | ⚠️ |
| T2-021 | T2 | continued_surveillance | active_surveillance | borderline | uncertain | ✗ | ⚠️ |
| T2-032 | T2 | active_surveillance | active_treatment | borderline | borderline | ✗ | ⚠️ |
| T2-019 | T2 | active_treatment | active_surveillance | borderline | uncertain | ✗ | ⚠️ |

### 失败 case
- Baseline 失败: 0
- Self-refine 失败: 1

## 5. 总结与结论

### 指标汇总

| 指标 | Baseline | Self-refine | 变化 |
|------|----------|-------------|------|
| 总体 Accuracy | 66.7% (20/30) | 58.6% (17/29) | -8.0% |
| 总错误数 | 10 | 12 | +2 |
| Uncertain 比例 | 20% | 31% | +11% |

### 结论

**self_refine 对系统性能的贡献: 负贡献**

依据: Accuracy 下降 -8.0%

补充分析:
- Refine 触发率: 0% — 低触发率可能说明 critique 过于保守
- Thinking 污染率: 100% — 污染较高，需优化 critique prompt
- 决策变更: 11 case 发生决策改变 — refine 实际影响了输出

---
> 注: 样本量 N=30, 统计显著性有限。结论应作为定性参考，不宜过度解读小幅差异。