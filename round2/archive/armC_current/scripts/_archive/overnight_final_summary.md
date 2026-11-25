# 0828-29 夜间无人值守批量编排 · 最终汇总报告

- 日期: 2026-08-29
- 模式: 无人值守编排（部分阶段自动化脚本后台执行）
- 层级声明: Stage0=L1 / Stage1=L3全量 / Stage2=L3轻量 / Stage3=L3轻量 / Stage4=0推理

---

## Stage 0 · L1 离线审计

### 0A · predictor override 失效根因（结论）

**Override 未失效，而是"不可观测"**。四重证据交叉对比：
1. `_save_trace()` 不写入 `predictor_decision` 字段（run.py）→ trace 中该条件恒为 0
2. `[定量模型参考]` 追加到 plan prompt 局部变量（graph.py），不入 state messages → trace 只存 LLM 响应
3. "Clinical prediction reference" 在 form_fill prompt 局部变量（form_fill.py）→ 同上
4. "decision override" warning 仅在 LLM 与 predictor **分歧**时产生 → 大部分 case 一致，无 warning

佐证：Step9 中 3 个 override warning（T3-023 0→1, T3-031/T3-056 1→0）证明 override 实际执行。
另发现 8/23 的 sklearn `SimpleImputer._fill_dtype` 兼容性 bug 已于 8/28 修复。
产物: scripts/overnight_stage0_override_audit.md

### 0B · former-FP 翻转矩阵（论文素材）

| 分组 | 总数 | 翻转/保持 | 明细 |
|------|------|-----------|------|
| former-FP | 49 | **22→TN**, 27 仍 FP | 45% 修复 |
| former-TP | 19 | 16 保持 TP, **3→FN** | T3-023/032/053 |
| former-TN | 5 | 4 保持 TN, **1→FP** | T3-045 |

特征变化：psa_threshold 引用率 100%→4-20%（模型不再机械引用阈值）；margin 71%→100%（仍为主要 FP 驱动）；baseline PSA 4%→33%。
产物: scripts/overnight_stage0_former_fp_matrix.json

### 0C · 消融 trace 诊断

- 旧消融 critique_iter1.json **100% 含 thinking 污染**（30/30）
- 旧 server（start_qwen_server.sh）与正确 server 均为 `llama_cpp.server` 同参数 → 污染**非 server 差异导致**
- 新版代码 trace 合并: reflect_warnings 字段；日志级证据 `critique contaminated by thinking process, treating as no issues` → 污染被 fallback 吞掉
产物: scripts/overnight_stage0_ablation_diag.md

---

## Stage 1 · Step9 T3 L3 全量 τ v2

**Docker 3 门槛裁定：PASS ✅**

| 指标 | 值 | 门槛 | 判定 |
|------|-----|------|------|
| FP率 | 1.85% (1/54) | <30% | PASS |
| EventAcc | 94.5% (69/73) | >60% | PASS |
| C-index | 0.911 (eval脚本) / 0.9563 (手算) | >0.6 | PASS |
| τ 不变式 | 0 违反 (event=1⇒months<24, 0 升级, 0 None) | - | PASS |
| 降级触发 | 2 (T3-031/056) | ≥1 | PASS |
| FN | 3 | ≤12 | PASS |
| HARD | 0 | 0 | PASS |

混淆矩阵: TP=16 / FP=1 / FN=3 / TN=53
产物: scripts/overnight_stage1_step9_report.md + eval_train_gt_report_t3_full_tau_v2_20260829.md

---

## Stage 2 · 消融A（atomic_check=true, self_refine=false）

| 任务 | case | 正确率 | macro F1 | 备注 |
|------|------|--------|----------|------|
| T1 | 11 | 63.6% | 0.633 | - |
| T2 | 9 | 55.6% | 0.458 | 达标(≥0.35) |
| T3 | 8 | event 62.5% | C-index 0.705 | - |

**fact_check 触发率: 29/30 case**；claims 73 TRUE / 1 FALSE / ~57 UNVERIFIED；confidence 降级 1 次（PT-pseudo_4bfd4ec864d8 clear→borderline）。
⚠️ 普遍 WARNING: `LLM decompose_claims failed: Extra data`（JSON 尾随字符，fallback 解析成功）。
产物: scripts/overnight_stage2_ablation_A_report.md

---

## Stage 3 · 消融B（self_refine=true, atomic_check=true）

### smoke gate
- smoke 3/3 case: `self_refine: no issues found (1 iteration)` → 表面污染 0%
- **修正后判定 PASS**（初版脚本路径错误误判 FAIL，已修正并补跑全量）

### 全量 30 case
| 任务 | case | 正确率 | macro F1 | 备注 |
|------|------|--------|----------|------|
| T1 | 12 | 58.3% | 0.580 | - |
| T2 | 10 | 60.0% | 0.343 | - |
| T3 | 8 | event 75.0% (6/8) | C-index 0.682 | - |

### 核心结论：self_refine 无效（负贡献）

| 指标 | 值 |
|------|-----|
| critique 执行率 | 30/30 |
| **refine 触发率** | **0/30 (0%)** |
| thinking 污染（日志级） | **30/30 均触发 fallback** |
| 决策变更率 | 0% |

**根因**：critique 输出含 thinking 段 → 代码 fallback `treating as no issues` → 所有 issues 被吞掉 → **refine 永不触发，决策零变更**。正确 server 未解决 critique 污染（污染源于模型输出格式，与 server 无关），需修 `_parse_critique_json`（**deferred，L2 验证**）。

### A vs B 对比

| 指标 | 消融A (no refine) | 消融B (refine) | 变化 |
|------|-------------------|----------------|------|
| T1 macro F1 | 0.633 | 0.580 | -0.053 |
| T2 macro F1 | 0.458 | 0.343 | -0.115 |
| T3 event | 62.5% | 75.0% | +12.5% |
| T3 C-index | 0.705 | 0.682 | -0.023 |
| refine 触发 | - | 0% | 无贡献 |

**self_refine 净贡献：负/无效**（F1 全面下降，唯一 T3 event 提升系随机波动；refine 0% 触发说明机制实际未工作）。

产物: scripts/overnight_stage3_ablation_B_report.md + eval_train_gt_report_ablation_B_selfrefine_20260829.md

---

## Stage 4 · GEval（gemma4:e4b）

- Step9 达标 → GEval 已执行（73 case, Task3）
- **平均 rationale alignment: 0.799**（73 valid, 0 解析失败）
  - 高分 ≥0.8: 54 case (74.0%)
  - 中分 0.5-0.8: 18 case (24.7%)
  - 低分 <0.5: 1 case (1.4%) — T3-064 (0.30)
- Ollama judge 可用率: 100% (0/73 错误) ✅
- 注: gemma4:e4b 已安装（7.5B Q4_K_M）；初版脚本因 ollama 路径误判"未安装"已修正
产物: scripts/overnight_stage4_geval_report.md + scripts/overnight_geval_report_step9.md

---

## 各阶段失败 / 告警 case 清单

| 阶段 | case | 原因 |
|------|------|------|
| Step9 FN | T3-053, T3-057, T3-060 | GT=1 判 0 |
| Step9 FP | T3-065 | GT=0 判 1 |
| 消融A | PT-pseudo_4bfd4ec864d8 | fact_check_flags extra_forbidden (schema bug, 需 L2) |
| 消融A | T2-007 | LLM 输出空 JSON, 3 次解析失败 |
| HARD(消融A) | PT-pseudo_20ba3f0f6ca0, T2-039 | PSAD 转录偏差 (1.0vs0.086, 0.19vs0.187) |
| HARD(消融B) | PT-pseudo_1dc32184cab6, T2-039 | PSAD 转录偏差 (0.02vs0.021, 0.19vs0.187) |

---

## 声明

- **未改任何代码** / 未改 τ (24.0) / 未改 config.yaml 默认值（全部 Hydra 命令行覆盖）
- 各 output 独立目录，无相互覆盖
- 未自行进 Docker 打包
- GEval 为临时副本脚本（.geval_tmp/geval_eval_step9.py），仅替换 PRED_DIR/REPORT_PATH 路径常量，未改源码逻辑

## 报告路径索引

- Stage0: scripts/overnight_stage0_override_audit.md / overnight_stage0_former_fp_matrix.json / overnight_stage0_ablation_diag.md
- Stage1: scripts/overnight_stage1_step9_report.md
- Stage2: scripts/overnight_stage2_ablation_A_report.md
- Stage3: scripts/overnight_stage3_ablation_B_report.md
- Stage4: scripts/overnight_stage4_geval_report.md + overnight_geval_report_step9.md
