# B 线 — 时间演化、证据冲突与信息恢复

## 目录

```
b_line/
├── scripts/
│   ├── data_semantic_fix/   18 槽语义修复（psav 与水平分离、enc_date 单列、观测对去重）
│   ├── recon_build/         证据协调构建入口（index_evidence.py / run_serial.py）
│   ├── b12_boundary/        B12 边界回放
│   └── b12_regression/      B12 回归矩阵（preregister / formal / matrix / compare_formal）
├── evidence/
│   ├── semantic_fix.diff    修复前→修复后的真实 diff（含三项限定改动的说明）
│   ├── before_after_counts.json  修复前后计数对比
│   ├── denial_probes.json   6 条拒读探针（全部 denied，读取字节 0）
│   └── synthetic_checks.json  11 条合成正反例，全通过
└── archive/
    ├── before_recon_build/          证据协调构建的修复前代码
    └── formal_final_baseline_src/   B12 回归所用的正式基线代码（71 个 py）
```

## 三项语义修复到底改了什么

`semantic_fix.diff` 头部原话：

1. **`psav` 与 PSA 水平分离**：原值与字段出处单列；速度/水平语义未证时标 `UNKNOWN`，并排除出总 PSA 水平配对。
2. **`enc_date` 单列就诊元数据**：不作为采样时间、观察时间、历史可得性依据或临床截止。
3. **同序列观测对**：按去重后的两个不同观察引用组成无向对，每对一次，无自配，不以同值猜同源。

附带保持：限值符号（`<` `>` `≤` `≥`）不当作精确值参与精确比较。

这四条直接对应旧版把「速度」当「水平」、把「就诊日期」当「采样日期」、把「同值」当「同源」三类错误。

## 拒读探针的意义

`denial_probes.json` 记录了 6 次越界读取尝试，全部被拒，`content_bytes_read: 0`。探针路径指向 `data/.../train_release_chimera_phase2/task1/PT-pseudo_*/prostate-biopsy-decision-reasoning.json`——即真值文件。这是**可审计的隔离证据**，不是普通测试。

## 未同步

- `b_evidence_reconciliation_build_20261118/` 下的 1348 个逐例证据 json 与 222 个 md（880 MB）
- `b12_boundary/string_replay/case/private/`（目录自带 private 标识）
- `b12_regression_20261122/` 的 1987 个 json 与 `expected.json` / `expected_actual.jsonl`

原因与替代材料见 `../MISSING_FILES.md`。

## 待补

| 缺口 | 说明 |
|---|---|
| 时序 E1/E2 实验脚本 | 未在 `output/` 下找到独立目录，需确认是否已合并进 `data_semantic_fix` |
| 38 个候选病例筛选程序 | 未定位独立脚本 |
| 66 标记 / 29 风险组处理代码 | 疑似在 `08_semantic_fix.py` 内，`semantic_fix.diff` 只覆盖了它相对上一版的改动 |
