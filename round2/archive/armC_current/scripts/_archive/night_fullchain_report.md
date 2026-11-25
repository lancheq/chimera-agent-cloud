# 夜间全链报告（T1 全量 91 + GEval）

> 生成: 2026-08-30 02:24:18
> run: test/output.t1_full_v2_20260829（91 GT case，双关闭，v2 条款 prompt）

## 1. Run 统计
- done: 91 / 91；failed: 3
- 反泄漏 'Predictor loaded' 命中: 0（应 0）
- 末 3 行: 
```
2026-08-30 02:19:07,370 __main__ INFO Task 1: case PT-pseudo_e001f4d06517 done in 82.2s
2026-08-30 02:19:07,374 __main__ INFO Task 1: wrote 91 predictions (0 skipped, 0 failed) under test/output.t1_full_v2_20260829/task1
2026-08-30 02:19:07,374 __main__ INFO Done. Wrote 91 predictions across 1 task(s).
```

## 2. Monitor
```
[DRIFT] uncertain 比例过高: 8/20 (GT ≈16%)
[DRIFT] uncertain 比例过高: 8/20 (GT ≈16%)
[DRIFT] uncertain 比例过高: 8/20 (GT ≈16%)
[DRIFT] uncertain 比例过高: 8/20 (GT ≈16%)
[DRIFT] uncertain 比例过高: 8/20 (GT ≈16%)
```

## 3. 官方 eval（Task1 段）
```
=== Task1 GT 评估（91 case）===

decision 正确率: 58/91 = 63.7%
  - yes 正确率: 40/56 (pred 57)
  - no 正确率: 18/35 (pred 34)
macro F1: 0.615
  - yes F1: 0.708
  - no F1: 0.522

confidence 匹配率: 45/91 = 49.5%
  - GT:  {'clear': 58, 'borderline': 18, 'uncertain': 15}
  - Pred: {'uncertain': 27, 'clear': 53, 'borderline': 11}
weights kappa: 0.285
reveal 非空率: 91/91 = 100.0%
free_text 非空率: 91/91 = 100.0%

=== Task2 GT 评估（72 case）===
```

## 4. vs 0827 全量对比
> 注：链内 Phase 4 因脚本 unpack bug 崩溃（`co,fo=cm(...)` 4 元组解 2 变量）。
> 以下为事后同口径重算（GT 91 / v2 91 / 0827 91 无缺失，与官方 eval 交叉验证一致）：
```
0827: TP46 FP19 TN16 FN10  F1=0.642 (yes=0.760 no=0.525)
 v2 : TP40 FP17 TN18 FN16  F1=0.615 (yes=0.708 no=0.522)
ΔF1=-0.028  投影预期 +0.004（未兑现，反向回归）

翻转 32 case: 修对 14 / 修错 18（净 -4）
FP 19→17（-2）；FN 10→16（+6）：压 FP 有限，FN（强指征漏判 yes→no）显著增加
```
> 口径差异提醒：v2 为干净口径（predictor.enabled=false），0827 为生产口径
> （predictor note 注入），-0.028 含口径混杂成分，纯 prompt 归因需注意。
> 完整 32 case 翻转明细在收件 agent 回传消息中。

## 5. T1 GEval（gemma4:e4b）
```
=== Task1 GEval 评估（91 case）===
平均 rationale alignment: 0.754
  - 高分(≥0.8): 67 case (73.6%)
  - 低分(<0.5): 9 case (9.9%)
  - 解析失败: 0 case
典型低分 case（前 5）:
典型高分 case（前 3）:
```
