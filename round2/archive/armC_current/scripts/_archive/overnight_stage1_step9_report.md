# Stage 1 · Step9 T3 L3 全量 τ v2 报告

- 日期: 2026-08-29
- 层级: L3 全量 (73 case, task=3)
- 输出目录: `test/output.t3_full_tau_v2_20260829/`
- 上级通过证据: L3 轻量(Step8) TP=2/FP=1/TN=11/FN=5, EventAcc 68.4%, C-index 0.643, τ不变式 PASS

## 运行状态

- server: `llama_cpp.server` (35B Qwen3.6-35B-A3B, port 8765) ✓
- trace_monitor: 73/73 case, 无 [HARD] ✓
- 73 case 全部完成, 0 skipped, 0 failed ✓
- 耗时: ~76min (23:36 → 00:52)

## 混淆矩阵 (vs GT, 73 case)

| | GT=1 | GT=0 |
|---|---|---|
| **Pred=1** | TP=16 | FP=1 (T3-065) |
| **Pred=0** | FN=3 (T3-053, T3-057, T3-060) | TN=53 |

## 指标

| 指标 | 值 | 门槛 | 判定 |
|------|-----|------|------|
| FP率 | 1/54 = 1.85% | <30% | **PASS** |
| EventAcc | 69/73 = 94.5% | >60% | **PASS** |
| C-index (eval脚本) | 0.911 | >0.6 | **PASS** |
| C-index (手算, 含平局半计) | 0.9563 | >0.6 | **PASS** |

## L3 5项核查

| 判据 | 结果 | 判定 |
|------|------|------|
| ① 无 HARD | 0 告警 | PASS |
| ② τ 不变式 | event=1 ⇒ months<24 全部满足; 0 次升级; 0 None兜底 | PASS |
| ③ GT0 FP率 <50% | 1.85% | PASS |
| ④ FN ≤12 | 3 | PASS |
| ⑤ 降级触发 ≥1 | 2 (T3-031, T3-056: "decision override: event 1 -> 0") | PASS |

## Docker 3 门槛裁定

**PASS** (FP<30% / EventAcc>60% / C-index>0.6 全过)

## 失败 case 清单 (4)

- **FN (3)**: T3-053 (GT=1, pred=0, GT months=52), T3-057 (GT=1, pred=0, 36.7m), T3-060 (GT=1, pred=0, 60.4m)
- **FP (1)**: T3-065 (GT=0, pred=1, GT months=37.8)

## override 触发记录

- 3 个 warning: T3-023 (0→1), T3-031 (1→0), T3-056 (1→0)
- 符合 Stage 0A 结论: override 执行但 trace 不可观测（分歧时才产生 warning）

## 声明

- 未改任何代码 / 未改 τ (24.0) / 未改 config.yaml 默认值
- 输出独立目录, 与历史目录无覆盖
