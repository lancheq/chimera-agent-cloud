# 官方 evaluate.py 评估报告 (Pro 口径 self_refine=true + atomic_check=true)

> 生成时间: 2026-08-31
> 评估方式: 官方 evaluate.py 确定性评分（0 LLM judge, ollama 不可用）
> GT 目录: test/official_gt_20260831/
> Pred 目录: test/input_gc_pro_t{1,2,3}/

## 6 组件完整表

| 组件 | T1 (91 case) | T2 (71 case) | T3 (73 case) |
|---|---|---|---|
| Decision accuracy | 0.604 (55/91) | 0.648 (42 correct + 8 partial) | n/a |
| Decision F1 | yes: 0.684, macro: n/a | macro: 0.444 | n/a |
| Confidence kappa | -0.015 | -0.030 | n/a |
| Variable-weight kappa | 0.499 | 0.317 | n/a |
| Section grounding | 0.910 | 0.961 | n/a |
| Event accuracy | n/a | n/a | 0.822 |
| Time score | n/a | n/a | 0.562 |
| Final case score | 0.375 | 0.367 | 0.692 |
| Case score (gate passed) | 0.621 | 0.528 | n/a |

## vs false 口径变化

| 指标 | T1 false | T1 Pro | 变化 | T2 false | T2 Pro | 变化 | T3 false | T3 Pro | 变化 |
|---|---|---|---|---|---|---|---|---|---|
| Decision acc | 0.615 | 0.604 | -1.1pp | 0.480 | 0.592 | +11.2pp | 0.717 (C-idx) | 0.786 (C-idx) | +6.9pp |
