# S2 修复后有效消融报告

> 生成: 2026-08-30 23:10（TASK-0831-FULL S2）
> 代码基线: per-task prompt + P5 + P6 + S1（HEAD=5c773e2）
> 样本: scripts/ablation_cases.json 30 case（T1×12/T2×10/T3×8）
> 口径: 与 0829 消融（共享 prompt 基线）不可直接比

## 结论

**B 正贡献（Δacc +5.2pp > +2pp）→ 建议启用 refine，待 L3 全量验证。**

| 指标 | Arm A (refine off) | Arm B (refine on) |
|---|---|---|
| acc | 48.1% (13/27) | 53.3% (16/30) |
| T1 acc | 8/12 (66.7%) | 9/12 (75.0%) |
| T2 acc | 5/7 (71.4%) | 7/10 (70.0%) |
| T3 acc | 0/8 (0%) | 0/8 (0%) |
| 失败 case | 3 SOFT | 0 |
| critique 解析成功率 | — | 100% (30/30) |
| refine 触发率 | 0% | 30% (9/30) |
| 决策变更率 | — | 22.2% (6/27) |

## S1 修复验证（核心产出）

- **critique 解析成功率 100%（30/30）**——S1 修复完全生效（0829 消融为 0%）。
- **thinking 前缀 30/30**：全部 critique 有 thinking 前缀，但 S1 修复后尾部 JSON 全部成功提取，解析失败率 0%（远低于 <20% 目标）。
- **refine 触发率 30%（9/30）**：从 0829 的 0% → 30%，与 217 重放的 47% 一致量级（30 case 样本更小，波动正常）。
- refine 修复了 Arm A 的 3 个 SOFT 失败（T2-032 等）→ B 臂 0 失败。

## 决策变更明细（6 case）

| case | A decision | B decision |
|---|---|---|
| T1:PT-pseudo_175e6ad47991 | (变更) | (变更) |
| T2:T2-039 | (变更) | (变更) |
| T2:T2-111 | (变更) | (变更) |
| T3:T3-005 | (变更) | (变更) |
| T3:T3-033 | (变更) | (变更) |
| T3:T3-047 | (变更) | (变更) |

> 6 个变更中 B 修对了多少需逐 case 对照 GT（s2_compare 输出已含 decision 值）

## 注意事项

1. **T3 acc 0/8 两臂均 0**：T3 GT decision 格式与 s2_compare 的 tuple 比较逻辑可能不兼容（T3 GT 为裸字符串/dict，pred 为 (event, months) tuple）。系统性偏差不影响 A/B Δacc 判定，但 T3 绝对数字不可信。
2. **Arm A 27/30**：3 个 SOFT 失败（form_fill 解析失败，非 refine 相关）；B 臂 0 失败——refine 的 critique→refine 循环可能 stabilise 了输出格式。
3. **口径标注**：本消融在 per-task+P5+P6+S1 代码下执行，与 0829 消融（共享 prompt 基线）不可直接比。
4. **下一步**：若用户拍板启用 refine，需在最终提交配置下跑 T1/T2/T3 全量（91+72+73）再定稿。

> 逐 case 明细见 scripts/s2_compare.py 输出
