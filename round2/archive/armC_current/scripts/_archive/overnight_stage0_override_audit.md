# Stage 0A: Predictor Override 失效根因审计

> 纯离线分析，0 推理。审计日期: 2026-08-29
> 审计目标: `/root/shared-nvme/chimera-agent-baseline`

---

## 1. T3 Run 日志关键词搜索

### 搜索的 5 个关键词

| # | 关键词 | t3_full_tau_v2_run.log | t3_004_rerun.log | full_run_predictor_override_20260823.log | full_run_gt_v2_20260823.log | full_run_integrated.log |
|---|--------|----------------------|------------------|----------------------------------------|---------------------------|------------------------|
| 1 | `Predictor loaded task` | 3 | 3 | 3 | 3 | 0 |
| 2 | `Predictor model missing` | 0 | 0 | 0 | 0 | 0 |
| 3 | `Predictor not loaded` | 0 | 0 | 0 | 0 | 0 |
| 4 | `predictor decision override ENABLED` | 1 | 1 | 0 | 0 | 0 |
| 5 | `predictor failed for` | 0 | 0 | 0 | 73+ | 1 |

### 关键上下文

**关键词 1: "Predictor loaded task" (t3_full_tau_v2_run.log, 3 次)**
```
L6: 2026-08-28 23:36:56,041 ... Predictor loaded task1 from model/predictor/predictor_task1.joblib
L7: 2026-08-28 23:36:56,042 ... Predictor loaded task2 from model/predictor/predictor_task2.joblib
L8: 2026-08-28 23:36:56,123 ... Predictor loaded task3 from model/predictor/predictor_task3.joblib
```
结论: 三个任务模型全部成功加载。

**关键词 4: "predictor decision override ENABLED" (t3_full_tau_v2_run.log, 1 次)**
```
L10: 2026-08-28 23:36:57,472 ... Task 3: predictor decision override ENABLED
```
结论: override 在 Task 3 已启用。启动命令行参数确认: `agent.predictor.decision_override=true agent.predictor.decision_override_tasks=[3]`。

**关键词 5: "predictor failed for" (full_run_gt_v2_20260823.log, 73+ 次)**
```
L11200: Task 3: predictor failed for T3-001: 'SimpleImputer' object has no attribute '_fill_dtype'
L11262: Task 3: predictor failed for T3-002: 'SimpleImputer' object has no attribute '_fill_dtype'
... (每个 T3 case 均失败)
```
结论: 8月23日运行中，predictor 对**每个** T3 case 都失败，原因是 sklearn 版本不兼容（模型用 sklearn 1.6.1 序列化，运行时为 sklearn 1.9.0）。8月28日已修复（模型重新训练或版本调整）。

**关键词 5: "predictor failed for" (full_run_integrated.log, 1 次)**
```
L200: Task 3: predictor failed for T3-mock-001: [Errno 2] No such file or directory: 'data/task3/agent_input/T3-mock-001/structured-prompt.json'
```
结论: mock case 数据文件缺失，非模型问题。

---

## 2. 模型文件存在性

### model/predictor/ 目录

| 文件 | 大小 | 存在 | 备注 |
|------|------|------|------|
| predictor_task1.joblib | 3,293 B | YES | 分类器模型 |
| predictor_task2.joblib | 4,679 B | YES | 分类器模型 |
| predictor_task3.joblib | 961,730 B | YES | 生存模型（含 HistGB） |
| predictor_task1.joblib.bak.sklearn161 | 3,421 B | YES | sklearn 1.6.1 备份 |
| predictor_task2.joblib.bak.sklearn161 | 4,855 B | YES | sklearn 1.6.1 备份 |
| predictor_task3.joblib.bak.sklearn161 | 766,386 B | YES | sklearn 1.6.1 备份 |
| predictor_task3.joblib.bak.oldfeat | 152,779 B | YES | 旧特征定义备份 |

结论: 三个 predictor 模型文件均存在且有备份。模型文件日期均为 2026-08-28 08:29。

---

## 3. Trace.json 四清单交叉对比

### 3.1 旧输出目录: test/output.t3_full_20260828/task3/ (73 cases)

> 时间戳: 2026-08-28T11:55 ~ 13:28 (白天运行，t3_full_tau_v2_run.log 之前的更早运行)
> t3_004_rerun.log 在 15:22 重写了 T3-004 的 trace.json

| 条件 | 命中数 | 命中 Case |
|------|--------|----------|
| (a) predictor_decision 字段非空 | 0/73 | (无) |
| (b) plan 阶段文本含 `[定量模型参考]` | 0/73 | (无) |
| (c) free_text 含 "reference model" | 1/73 | T3-004 |
| (d) form_fill_warnings 含 "decision override" | 0/73 | (无) |

**交叉对比:**
- a & b & c & d 同时满足: 0 cases
- 四条件均不满足: 72 cases
- 仅满足 c: 1 case (T3-004, "reference model" 出现在 LLM 自发生成的 free_text 中, 非系统注入)

### 3.2 新输出目录: test/output.t3_full_tau_v2_20260829/task3/ (57 cases, 运行中)

> 时间戳: 2026-08-28T23:37 ~ (仍运行中)
> 启动参数: `agent.predictor.enabled=true agent.predictor.decision_override=true agent.predictor.decision_override_tasks=[3]`

| 条件 | 命中数 | 命中 Case |
|------|--------|----------|
| (a) predictor_decision 字段非空 | 0/57 | (无) |
| (b) plan 阶段文本含 `[定量模型参考]` | 1/57 | T3-027 |
| (c) free_text 含 "reference model" | 9/57 | T3-011, T3-014, T3-017, T3-018, T3-024, T3-028, T3-043, T3-051, T3-059 |
| (d) form_fill_warnings 含 "decision override" | 3/57 | T3-023, T3-031, T3-056 |

**交叉对比:**
- a & b & c & d 同时满足: 0 cases
- 仅满足 d: T3-023 (有 "decision override: event 0 -> 1" 警告)
- 仅满足 b: T3-027 (LLM 在 plan 响应中回显了 `[定量模型参考]`)
- 仅满足 c: 9 cases (LLM 在 free_text 中提到 "reference model")

### 3.3 Predictor=1 Cases 与 structured_response event 交叉验证

日志中 `predictor=1`（predictor 预测 event=1/复发）的 14 个 case:

| Case | predictor | event (输出) | months | override 警告 | 分析 |
|------|-----------|-------------|--------|-------------|------|
| T3-001 | 1 | 1 | 1.78 | 无 | LLM 与 predictor 一致 |
| T3-017 | 1 | 1 | 11.38 | 无 | LLM 与 predictor 一致 |
| T3-018 | 1 | 1 | 1.80 | 无 | LLM 与 predictor 一致 |
| **T3-023** | **1** | **1** | **19.18** | **"event 0 -> 1"** | **override 生效! LLM 原判 0, predictor 判 1, 已覆盖** |
| T3-025 | 1 | 1 | 4.38 | 无 | LLM 与 predictor 一致 |
| T3-027 | 1 | 1 | 19.90 | 无 | LLM 与 predictor 一致 |
| T3-032 | 1 | 1 | 1.92 | 无 | LLM 与 predictor 一致 |
| T3-033 | 1 | 1 | 1.80 | 无 | LLM 与 predictor 一致 |
| T3-036 | 1 | 1 | 14.90 | 无 | LLM 与 predictor 一致 |
| T3-039 | 1 | 1 | 14.88 | 无 | LLM 与 predictor 一致 |
| T3-047 | 1 | 1 | 1.30 | 无 | LLM 与 predictor 一致 |
| T3-050 | 1 | 1 | 10.86 | 无 | LLM 与 predictor 一致 |
| T3-052 | 1 | 1 | 6.19 | 无 | LLM 与 predictor 一致 |
| **T3-053** | **1** | **0** | **25.21** | **无** | **override 可能被 _calibrate_t3_event 或 _apply_t3_tau 逆转** |

结论: override **确实在执行**（T3-023 有明确的 "event 0 -> 1" 警告），但大部分 case 中 LLM 与 predictor 判定一致，因此不产生 override 警告。

---

## 4. Git 变更历史

### configs/config.yaml 的 git 历史

```
$ git log --all --oneline -- configs/config.yaml
e82a198 CHIMERA-agent cloud deployment baseline 2026-08-11
```

仅一次提交，无 override 相关的独立变更。

### config.yaml 中 override 相关内容

```yaml
# Line 5: 注释 - "Override per-experiment with +experiment=name or bare key=value on the CLI."
# Line 90: predictor.decision_override: false   (默认关闭)
# Line 91: predictor.decision_override_tasks: [2, 3]   (配置了任务列表但 override 本身关闭)
```

### 全局 override 相关 git log

```
$ git log --all --oneline --grep="override"
(无结果)

$ git log --all --oneline --grep="predictor"
(无结果)
```

结论: 无 override 相关的独立 commit。override 通过 CLI 参数在运行时启用，非 config.yaml 持久化配置。

---

## 5. 根因结论

### 核心发现: Override 未"失效"，而是"不可观测"

在最新运行（t3_full_tau_v2_20260829, override=true）中，override **确实在执行**:
- T3-023: `form_fill_warnings` 含 `"decision override: event 0 -> 1"` — override 将 LLM 的 event=0 覆盖为 predictor 的 event=1
- T3-031, T3-056: 同样有 decision override 警告
- 14 个 predictor=1 的 case 中，12 个 event=1（LLM 与 predictor 一致，无需 override）

但在旧输出目录（t3_full_20260828）中，4 个条件几乎全部为 0。原因是:

### 根因 1 (主要): `_save_trace()` 不写入 `predictor_decision` 字段

- **文件**: `src/chimera_agent_baseline/run.py`, L84-115
- **问题**: trace.json 写入的字段为 `case_id, task, timestamp, elapsed_seconds, status, messages, disease_plan, refined_transcript, reflect_warnings, form_fill_warnings, structured_response`
- **缺失**: `predictor_decision` 在 graph state 中存在（run.py L318: `"predictor_decision": predictor_decision`），但 `_save_trace()` 从未将其提取到 trace 输出
- **影响**: 条件 (a) 永远为 0，无法从 trace.json 追溯 predictor 的原始决策

### 根因 2: `[定量模型参考]` 在 plan prompt 中，不在 trace messages 中

- **文件**: `src/chimera_agent_baseline/agent/graph.py`, L254-261
- **问题**: `[定量模型参考]` 文本被追加到局部变量 `user_msg`，通过 `model.invoke()` 发送给 LLM。该 user message **不**被添加到 state 的 `messages` 列表
- **影响**: trace.json 的 `messages` 只存 LLM 的响应（plan_text），不含输入 prompt 中的 `[定量模型参考]` 标记
- **例外**: T3-027 的 LLM 在响应中回显了 `[定量模型参考]`，因此被检测到

### 根因 3: "Clinical prediction reference" 在 form_fill prompt 中，不在 trace messages 中

- **文件**: `src/chimera_agent_baseline/agent/form_fill.py`, L204-205
- **问题**: `_predictor_note()` 输出被追加到局部变量 `base_user`，发送给 LLM 但不存入 trace
- **影响**: 条件 (c) 在旧目录仅 1/73 命中（T3-004 的 LLM 自发生成），在新目录 9/57 命中（LLM 在 free_text 中引用）
- **注意**: 这些 "reference model" 出现是 LLM 自主提及，非系统注入标记

### 根因 4: "decision override" 警告仅在 LLM 与 predictor 分歧时产生

- **文件**: `src/chimera_agent_baseline/agent/form_fill.py`, L342-343, L419-441
- **问题**: `_apply_decision_override()` 仅在 `judgment.get("event") != ev` 时添加 `"decision override: ..."` 警告。若 LLM 判定与 predictor 一致，无警告产生
- **影响**: 条件 (d) 命中率低（新目录 3/57），不是因为 override 不工作，而是因为大部分 case LLM 与 predictor 一致
- **额外发现**: T3-053 的 predictor=1 但 event=0，override 可能被后续的 `_calibrate_t3_event()` (L604-657) 或 `_apply_t3_tau()` (L660+) 逆转

### 历史问题: sklearn 版本不兼容 (已修复)

- **时间**: 2026-08-23 (full_run_gt_v2_20260823.log)
- **错误**: `'SimpleImputer' object has no attribute '_fill_dtype'`
- **原因**: 模型用 sklearn 1.6.1 序列化，运行时为 sklearn 1.9.0
- **状态**: 已修复 — 8月28日模型重新训练（model/predictor/ 文件日期为 2026-08-28 08:29），新运行中无此错误

### 旧目录 vs 新目录差异说明

| 属性 | test/output.t3_full_20260828/ | test/output.t3_full_tau_v2_20260829/ |
|------|------------------------------|--------------------------------------|
| 运行时间 | Aug 28 11:55-13:28 | Aug 28 23:37 ~ (运行中) |
| override 配置 | 可能未启用 (日志未找到) | 明确启用 (CLI: decision_override=true) |
| trace.json 数量 | 73 | 57 (进行中) |
| 条件 (a) 命中 | 0 | 0 |
| 条件 (b) 命中 | 0 | 1 (T3-027) |
| 条件 (c) 命中 | 1 (T3-004) | 9 |
| 条件 (d) 命中 | 0 | 3 (T3-023, T3-031, T3-056) |

---

## 6. 修复建议 (仅供参考，不在本次审计范围内)

1. **在 `_save_trace()` 中增加 `predictor_decision` 字段** — 从 `result` 中提取并写入 trace.json
2. **在 plan_node 中将含 `[定量模型参考]` 的 user_msg 存入 state messages** — 或在 trace 中单独记录 plan prompt
3. **在 form_fill 中将 predictor_note 存入 trace** — 或在 trace 中记录 form_fill 的完整 prompt
4. **考虑 override 被 _calibrate_t3_event / _apply_t3_tau 逆转的情况** — 检查 T3-053 类 case 是否符合预期
