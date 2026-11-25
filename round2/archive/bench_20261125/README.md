# 基线对照与官方评价器校准（2026-11-24 ~ 11-25）

本目录是一次性基线分析结项归档：官方架构 A 与当前候选 C 在 236 例上的配对对照、公共评价器的版本校准、以及三组归因分析。**已结项**，不追加评价器或 Confidence 专项，不重跑基线。

## 结论摘要

| 任务 | 官方 A | 候选 C | 差 |
|---|---|---|---|
| T1 | 0.653 | 0.679 | +0.026 |
| T2 | 0.390 | 0.465 | +0.075 |
| T3 | 0.640 | 0.648 | +0.008 |
| Overall (0.4/0.4/0.2) | 0.545 | 0.587 | +0.042 |

全部为内部样本观察：T1 的 91 例历史上全部跑过并被分析；T2/T3 相对干净但单次运行、温度 0.3。不构成独立泛化证据。

本轮最有价值的发现是 T1 与 T2 走了相反路径：T1 多取证（揭示 +126.9%、token +90%）决策持平；T2 少取证（揭示 −19.0%、token −9%）决策 +8.3pp。A/C 之间还有提示词、重排器、Adaptive 控制器、输出投影多组差异，**不能**把这些变化归因于 Adaptive 本身。

## 三条归因及其边界

- **T1 置信度**：诊断文本未能可靠区分专家置信度等级（GT 非 clear 组犹豫率 33.3%，clear 组 29.3%）。可排除「原文已表达犹豫、填表环节才压成 clear」；关键词检测不能推断模型内部完全没有识别不确定性。
- **T2 置信度**：14 例 borderline 中 12 例为 active_treatment，两臂判 AT 的比例均约 70%。反向关系由类别构成 × 过度治疗倾向解释，不归咎参考标注。
- **段支撑**：与取证覆盖高度相关（T1 加权变量数 6.44 vs 6.36 几乎相同，零揭示 53/91 vs 2/91），但该分只在 gate 通过病例上计算、两臂集合不同（T1 各 65/91；T2 A 36/72、C 42/72），不宣称严格因果。

## 公共评价器（冻结）

- 版本：官方 main `evaluate.py`，`sha256=8684b9c4104d227bd08325fbef3c67ccd826e2836155db10f571f6eb16316a99`，2055 行
- 上游：`192c39c5a73a`（2026-08-24，`weighing rationale higher`）
- 被替换：项目内 vendored 副本 `e2b768db…`（1615 行，2026-09-27）：T2 给 0.5 部分分、`reveal_sequence` 只认 dict `key`
- 校验：`evaluator/evaluator_guard.py`，指纹不符即拒绝评分；溯源在 `evaluator/PIN.json`
- 未闭合：平台镜像构建使用哪个 commit，无法本地证实

复现：

```bash
python evaluator/evaluator_guard.py   # 先过冻结校验
# 再按 scripts/build_gc_inputs_v2.py 生成预测与 GT 目录，然后：
INPUT_DIRECTORY=<arm>/inputs PREDICTIONS_FILE=<arm>/inputs/predictions.json \
GROUND_TRUTH_DIR=<gt> EVAL_OUTPUT_DIR=<out> USE_RATIONALE_JUDGE=0 python evaluate.py
```

## 目录内容

| 路径 | 内容 |
|---|---|
| `reports/` | 六份过程报告（冒烟 → 全量配对 → 官方口径复评 → 版本核对与归因 → 两次结项）+ 全量配对任务卡 |
| `metrics/` | 排行榜投影、逐任务汇总、Confidence 与段支撑的聚合分析、两臂冻结指纹、predictor 折外/回测口径、推理分项 |
| `evaluator/` | 评价器冻结凭证与校验脚本 |
| `scripts/` | 产物转换、配对评分、成本汇总、调度与收尾、归因分析的全部脚本 |

## 未随本次同步上传

按仓库公开策略（根 README 与 `round2/README.md`）：患者原文、标签、逐例日志、模型权重与凭证不进入仓库。因此以下文件留在服务器，仅保留聚合统计：

- `FINAL_CASES.csv`：236 例逐例真值、两臂预测、token 与信息单元
- `gc_v2/eval/*/per_case_results.csv`、`predictions.json`、各 job 输出文件
- `gc_v2/gt/`：由本地标注重排的 ground truth 目录
- `BOUNDED_ANALYSIS.json` 中的逐例明细（`examples`、`borderline_cases` 的 case_id 与 GT free_text）已在同步版中剔除，改为聚合计数

需要逐例核对时，在服务器 `~/CHIMERA-armC/output/bench/` 下读取原始文件。
