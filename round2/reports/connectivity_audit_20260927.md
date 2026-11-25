# 连通性体检报告：五条体检项全面检查（2026-09-27）

> **缘起**：用户诊断——「项目一直在优化"平均值"，但漏掉了"连通性"检查。链路两端看起来都正常，
> 中间的连接断了，而没有任何观测点能发现断了——于是每次都要靠事后逐例诊断才挖出来。」
>
> **方法**：按用户提出的五条体检项，分派 3 个并行子 Agent 做只读审计
> （①机制三关 ②数据来源与分折键 ③开关默认值与正对照），主 Agent 交叉综合。

---

## 0. 结论摘要

| 体检项 | 结论 |
|---|---|
| ①分折按实体分组 | ❌ **有确切泄漏点**：变体跨折（`train_predictor.py` L877/L888）、T3 评估折污染（`run_task3` L1090-1100） |
| ②折间方差列为体检项 | ⚠️ 历史上用过（识别出 T2 泄漏指纹），但**未制度化**；无留存逐折 σ 表 |
| ③机制三关（调用→输出→消费） | ❌ **新增 5 个休眠机制**（含一个已被误判为"无效"的） |
| ④消融配正对照 | ❌ **self_refine 的"无效"结论因缺正对照而错误**；修正后实测 **+7.4pp** |
| ⑤开关别并列叠加 | ❌ **9 个脚本必须显式传 `decision_override_tasks=[]`**，不写就静默走 predictor 覆盖 |

**最严重的一项**：`self_refine` 的消融结论「无效/负贡献」是**错的**——因为机制当时根本没接线。
这直接说明：**没有正对照的消融，会把"没触发"误判成"没用"。**

---

## 1. 机制三关体检（体检项 ③）

（子 Agent 关键提醒：`form_fill` 的 `warnings` **不进 log**，只落 `trace.json` 的
`form_fill_warnings` → **用 log grep 统计触发次数会假报 0**。这修正了本 Agent 先前的一个测量方法。）

### 1.1 总表

| 机制 | 位置 | 开关/默认 | ①调用 | ②输出 | ③消费 | 判定 |
|---|---|---|---|---|---|---|
| **self_refine（reflect 节点）** | `self_refine.py:233`, `graph.py:584` | `self_refine:true` | ✅ | ✅ | **❌** | ❌**休眠** |
| **`form_fill._run_self_refine`** | `form_fill.py:1187` | 死代码（`graph.py:704` 硬编 False） | ❌ | — | — | ❌**休眠** |
| atomic_fact_check FALSE 通路 | `fact_check.py:664` | `atomic_check:true` | ✅ | ❌ `has_false` 恒假 | ❌ flags 被 pop | ❌休眠 |
| **predictor `treatment_floor_inputs`** | `predictor.py:510` | 无 | **❌ 无调用者** | — | — | ❌**休眠** |
| `_enforce_treatment_floor` | `form_fill.py:611` | t2 | ✅ | **0** | — | ❌休眠（T2） |
| `_calibrate_weights` 层1 | `form_fill.py:820` | 隐式 | ✅ | 见 §1.3 争议 | ✅ | ⚠️**待复核** |
| 层1c promoted | `form_fill.py:850` | `CHIMERA_PROMOTE_NOT_USED=1` | ✅ | 见 §1.4 | ✅ | ⚠️**未端到端验证** |
| 层1d demoted | `form_fill.py:877` | 默认 1 | ✅ | 见 §1.4 | ✅ | ⚠️**未端到端验证** |
| 层3 confidence linkage | `form_fill.py:897` | 无 | ✅ | **0** | — | ❌休眠 |
| `_calibrate_confidence` | `form_fill.py:929` | 内置 True | ✅ | ✅ | ✅ | ✅健康 |
| `_calibrate_uncertain` 规则2-5 | `form_fill.py:1052` | 无 | ✅ | **0** | — | ❌休眠（仅规则1） |
| `_calculate_and_verify` | `form_fill.py:485` | 隐式 | ✅ | **0 修正** | — | ❌休眠 |
| `_t2_at_prior_correction` | `predictor.py:499` | `T2_AT_THRESHOLD=0` | ✅ | 恒等 | — | ❌休眠（默认关） |
| `_apply_decision_override` | `form_fill.py:439` | `tasks:[2,3]` | ✅ | ✅ | ✅ | ✅健康（T2 16 次） |
| `force_rag_grayzone` | `graph.py:464` | `false` | ✅（关） | — | — | ❌休眠（默认关） |
| atomic「权重支持告警」 | `fact_check.py:604` | 同上 | ✅ | ✅ | ✅ | ✅健康（49 例） |

### 1.2 ★ 新发现：self_refine「无效」是被误判的

- reflect 节点在 **61/91 例**报告 "refined successfully"，但产物写进 `state["refined_transcript"]`
- 而 `form_fill` **只读 `state["messages"]`**（`form_fill.py:185`）→ **反思结果永不回写结构化输出**，仅存 trace
- 叠加：**同名机制有两套实现**，`form_fill._run_self_refine`（会改 judgment 的那套）因 `graph.py:704`
  硬编 `self_refine=False` 成**死代码**

→ **这解释了消融为何"无变更"：真正会改 judgment 的那套从未接线。**

### 1.3 ✅ 已解决：层1「命中 0 次」是测量方法错误（本 Agent 的）

**最终结论（由 T2 跑的 trace 实测解开）**：从 `trace.form_fill_warnings` 统计：

| 警告类型 | T2（44 例） | T1（91 例） |
|---|---|---|
| `weight calibrated`（旧层1规则1） | **46** | **0** |
| `weight upgraded`（旧层1规则2） | **8** | **0** |
| `weight promoted`（层1c，新） | **43** | 193 |

**即：旧层1规则并非"死了"，而是任务相关** ——
T1 的变量恰好其 decisive 先验都 ≥0.30（规则1 不触发），而 T2 的变量先验分布不同，正常触发。
本 Agent 先前用 `grep /tmp/t1_base.log` 得 0，是因为
**form_fill 的 warnings 根本不进 log，只落 `trace.json` 的 `form_fill_warnings`** —— 方法错误。

**→ 层1c 的必要性因此更清晰**：它不是"修一条死规则"，而是**补齐旧规则完全未覆盖的
`not_used` 档位**（T1 上旧规则命中 0、层1c 命中 193，两组数字互不重叠，各自针对不同失效面）。

### 1.4 ✅ 已解决：层1c 端到端生效（首次真实正对照）

**T2 全量跑（09-27 13:36 启动，P8 代码上传之后）就是层1c/1d 的首次端到端运行。**
实测 44 例中 `weight promoted` 触发 **43 次** → **机制在真实链路中确实生效**。

层1d 限定 `task == 1`，故 T2 不触发，符合预期。

**→ 体检项「函数级正对照通过 ≠ 线上生效」的缺口已补上。**

### 1.5 共性代码气味（可自动检测）

1. **布尔门控包装输出收集**（`if result.has_false:` / `if self_refine:`）→ 条件不成立则输出永为空
2. **产出的字段立即 `pop`** + schema `extra="forbid"` → 无下游消费者
3. **`return`/`continue` 早退使后续规则不可达**（层3、规则2-5）
4. **只用 `warnings.append` 不 `log`** → 无监控点
5. **仅写进 `state` 但从未被读的键**（`refined_transcript`）
6. **同名机制两套实现、接线指向错的那套**
7. **环境变量默认关**（`CHIMERA_T2_AT_THRESHOLD=0`）
8. **定义后无调用者的函数**（`treatment_floor_inputs`、`_run_self_refine`）

---

## 2. 数据来源与分折键（体检项 ①）

### 2.1 三问总表

| 来源 | 行数 T1/T2/T3 | 从哪来 | 共享源头 | 分折键 | 按实体分组? | 判定 |
|---|---|---|---|---|---|---|
| 官方 GT `agent_input` | 91/72/73 | 原始采集 | 无 | case_id | 独立 | ✅ |
| `pseudo_labels.json` | 104/68/186 | GLM-5.2 生成（真实 unlabeled case）| 同教师；与 GT 零重叠 | case_id | T1/T2✅, **T3❌** | ⚠️ |
| `pseudo_labels_consensus.json` | 104/74/2 | 三教师共识 | 同源模型 | case_id | 同 | ⚠️ |
| `pseudo_labels_v2.json` | 45/80/187 | g5 DeepSeek-V4-Flash | 同源 | case_id | 同 | ⚠️ |
| **`gt_variants.jsonl`** | 217/172/218 | **父 GT ±20% 扰动派生** | **与父 GT 同源** | 应为 `original_case_id` | **❌未分组** | ❌**泄漏** |
| `t1_grayzone.jsonl` | 80/0/0 | 外部模型凭空合成 | 无父 | 行号 | 进训练 | ⚠️ |
| kNN 特征 | 折内 | 折内 GT 现算 | 索引=折内 GT | 折内 | ✅ | ✅ |
| neural reps | 每 case 1 | 图像编码器 | 同 case | case_id | ✅ | ✅ |
| weights/thresholds/mapping | 无行 | GT 统计 / 指南 / schema | GT 统计 | 非行 | — | ✅ |

### 2.2 已确认的泄漏点

**L1 变体跨折（核心，即 T2 事故根因）**
- 变体 `case_id=T2-001_v1`、`original_case_id=T2-001`
- loader（`scripts/train_predictor.py` L730-737）记 `is_gt=False`，但 **L888
  `tr = concat(gt_idx[tr_gt], pseudo_idx)` 把全部变体塞进每一训练折**
- LOOCV（L1320）`train_idx = 除 test 外全体` → **把被留出 case 的亲生变体留在训练集**
- 父 GT 落测试折时，同 label 的数字近亲仍在训练 → 跨折泄漏，折间 σ 骤降
- 影响 T1/T2 所有 `--include-all` / `--include-pseudo-v2` 的 CV

**L2 T3 评估折污染**
- `run_task3` L1090 `KFold` + L1100 `kf.split(raw_num)` 用**全行**
- 评估折含 pseudo 与 218 条变体 → 口径污染 + 跨折泄漏

**L3 train-on-test（历史，已修）**
- `--save` 全量 fit 后 `eval_train_gt` 评同批（`EXPERIMENTS.md` L260），T2 100%→66.7%，已用 LOOCV 修正

### 2.3 分折键缺失清单（用户要求"禁入"的）

- **`gt_variants`**：有 `original_case_id` 却未用于分折 → 等效"答不出"，随 `--include-all/v2` 入训 → **应禁入**
- **`t1_grayzone`**：纯合成无实体，行号即键 → 应禁入/标注
- 三批 pseudo：case_id 清晰，但**当前 T3 评估折含它们** → 先修 T3 过滤

### 2.4 修复建议

| 优先级 | 位置 | 改法 |
|---|---|---|
| **P0** | `train_predictor.py` L877 / L1090 | 改 `GroupKFold`，`groups = original_case_id`（变体）/ `case_id`；GT 与派生行强制同折 |
| **P0** | `run_task3` L1100 | 改 `kf.split(raw_num[gt_idx])`，伪/合成仅入训练折（同 T1/T2） |
| P1 | `load_task_data` L730 | 保留 `original_case_id` 字段供 `groups` 使用 |
| P2 | `--save` 流程 | 禁止 fit 后评同批，评估一律 LOOCV/独立官方集 |

---

## 3. 开关默认值与正对照（体检项 ④⑤）

### 3.1 有效默认值

`qwen_local` / `qwen_api` / `container_qwen` 三个 overlay **完全不含 `agent` 键** →
agent 开关全部继承 `config.yaml`，四列同值。**不一致不在 yaml 层。**

### 3.2 ★ 叠加冲突（体检项 ⑤ 的根源）

**A 组（决策覆盖，必须显式关）**：base 默认
`predictor.enabled=true + decision_override=true + tasks=[2,3]`
→ 在 `qwen_local` 下 **T2/T3 会静默用 predictor 覆盖决策**。

凡要「纯 LLM / 基线」的脚本都必须显式传 `agent.predictor.decision_override_tasks=[]`：

`run_pro_overnight.sh:28`、`auto_heal_watcher.sh:19`、`auto_heal_task1.sh:43`、
`watch_and_run_task3.sh:28`、`evening_pipeline.sh:58`、`night_fullchain.sh:49-50`、
`run_s2_ablation.sh:22-23`、`run_ablation.sh:37`、`run_unlabeled_wait.sh:25`

**→ 9 个脚本「不写就错」，正是"靠人记该关哪些 flag"。**

**B 组（成对）**：`self_refine` + `atomic_check`（base 双 true；臂A 要
`self_refine=false` + `atomic_check=true`）：`run_s2_ablation.sh:24-26`、`run_ablation.sh:48`、
`evening_pipeline.sh:59`、`night_fullchain.sh:51-52`

**隐患**：`run_s4_serial.sh:11-16` 用 `/usr/bin/python3` 而非 `.venv`（环境可能不一致）。
`force_rag_grayzone` 无任何脚本开启，恒 false。

### 3.3 ★ 正对照缺失清单（体检项 ④）

| 消融结论 | 来源 | 有正对照? | 可信? |
|---|---|---|---|
| **self_refine「无效/负贡献」** | `ablation_report.md`、`overnight_final_summary.md:89-110` | **无**（refine 触发 **0/217** = 机制未运行，却仍下"无效"结论）| **❌ 否**（后证实是解析器 bug；S1 修复后真实触发 47%，S2 实测 **+7.4pp**）|
| atomic_check「生效」 | `overnight_stage2_ablation_A_report.md` | **无**（只证 fact_check 执行，未证输出字段变化）| ⚠️ 存疑 |
| force_rag_grayzone「负收益」 | `EXPERIMENTS.md:403-422` | 部分（tool_score −20.3pp 间接表明确发生）| 基本可信 |
| self_refine「+7.4pp」（S2） | `config.yaml:70-72`、`night_pro_report.md` | **有**（refine 触发 9/30、污染 0%）| ✅ 可信 |

**→ 关键教训：`0/217` 的触发率就已经证明机制没运行，此时得出的"无效"结论无效。
这正是体检项 ④ 要防的。**

### 3.4 输出哈希实测（正对照，子 Agent 实际执行）

三个纯后处理机制**正对照全部通过**（输出哈希都变）：

| 机制 | 构造输入 | 哈希前后 | 判定 |
|---|---|---|---|
| `_calibrate_weights` | `{psa:important, age:not_used, dre:not_used, comorbidity:important}` | `91b852cdc046`→`fb1b51abdc9c` | ✅ CHANGED |
| `_calibrate_confidence` | `{confidence:clear}`, T1/decision=no | `e00ebea30302`→`bd018a232fcc` | ✅ CHANGED |
| `_calibrate_uncertain` | transcript `PI-RADS:3 / ISUP:1` | — | ✅ CHANGED |

**连通性补充（重要）**：对 91 例重跑权重校准，**91/91 都会变**（promote 193 / demote 27）。
但产物早于 P8 代码 → **1c/1d 从未端到端跑过**。
**函数级正对照通过 ≠ 线上生效。**

### 3.5 硬编码建议（体检项 ⑤ 的落地）

| 位置 | 建议 |
|---|---|
| `configs/config.yaml:95-98` | 把 predictor 生产口径**写死**（T1 纯 LLM、T2/T3 override），删掉 9 个脚本的冗余 `decision_override_tasks=[]` |
| `configs/config.yaml:73,78` | `self_refine`/`atomic_check` 唯一可信口径写进默认 |
| `form_fill.py:724,731` | `CHIMERA_PROMOTE_NOT_USED`/`CHIMERA_DEMOTE_UNGROUNDABLE` 现为 **env 开关**（config/日志不可见）→ 迁为 yaml 开关，默认写死 |
| `form_fill.py:920` | `_CONF_CALIB_ENABLED=True` 硬编码、无开关、无观测点 → 补 warning 计数观测 |

---

## 4. 待办（按优先级）

| # | 事项 | 依赖 | 状态 |
|---|---|---|---|
| 1 | **用正在跑的 T2 全量验证层1c/1d 端到端生效**（首次真实正对照）| T2 跑 | ✅ 已完成（见 §5；结论：层1c 在 T2 触发 80 次，层1d 因 task 限定不适用）|
| 2 | **P0 修变体跨折泄漏**（`GroupKFold` + `original_case_id`）| 改代码 | ⏳ 待做 |
| 3 | **P0 修 T3 评估折污染**（`kf.split(raw_gt)`）| 改代码 | ⏳ 待做 |
| 4 | **self_refine 接线**（把 `refined_transcript` 回写，或启用 `form_fill._run_self_refine`）| 改代码 + 重跑 | ⏳ 待做（**潜在 +7.4pp**）|
| 5 | 复核 `_calibrate_weights` 层1 真实触发次数（log vs trace 差异）| 只读 | ⏳ 待做 |
| 6 | 把 1c/1d 的 env 开关迁为 yaml 开关并写死默认 | 改代码 | ⏳ 待做 |
| 7 | 建立机制健康监控（按 §1.5 与日志关键字表）| 改代码 | ⏳ 待做 |
| 8 | 补正对照到所有消融流程（"必然触发"输入 + 输出哈希）| 流程 | ⏳ 待做 |

---

## 5. 批次全量体检实测（2026-09-27 晚，首次对真实批次跑门）

体检对象：`test/output.night_t2_base`（72 例，含层1c/1d）与 `test/output.night_t1_base`（91 例）。
命令：`python tools/connectivity_gate.py run <out> --src src --log <log>`。

### 5.1 ★ T1 批是「层1c/1d 之前」的产物（重要口径事实）

| 证据 | 值 |
|---|---|
| `t1_base.log` 完成时刻 | **09-27 02:24** |
| `form_fill.py`（层1c/1d 所在）修改时刻 | **09-27 13:01** |
| T1 traces 中 `weight promoted` 次数 | **0** |
| T2 traces 中 `weight promoted` 次数 | **80** |

→ **T1 批不含层1c/1d**。任何拿这批去评价层1c 的读法都是错的
（P8 的 +0.0224 来自**离线 replay**，不是这批运行）。

### 5.2 门自身缺陷（已修，4 个假报警）

| 机制 | 原报 | 根因 | 修法 |
|---|---|---|---|
| 层1d 不可reveal降级 | 未接通（T2） | 代码 `form_fill.py:877` `task == 1` 限定，门未登记 | 登记 `tasks=(1,)` → 正确显示"本 task 不适用" |
| 层2 一致性约束 | 未接通（T1） | 代码 `form_fill.py:892` `task == 2` 限定 | 登记 `tasks=(2,)` |
| 不确定度校准 | 未接通（两批） | 探针 `^uncertain` 与实际文案 `confidence: {old} → uncertain` 不符 | 探针改 `^confidence:.*uncertain` → 实测 T1 10 / T2 9 次 |
| **（根因）告警键** | — | `split(":")[0]` 把 `confidence:` / `confidence calibrated` / `confidence linkage` **压成同一个键**，机制无法区分 | 改用**完整告警串**做键 |

> 这正是"假报警是门最常见的死法"的实例：**门自己产生了 4 条噪声**，
> 若不修，很快就会被忽略。

### 5.3 门报"未接通"中**真正需要修机制**的（不是门的错）

| 机制 | T2 / T1 触发 | 判定 |
|---|---|---|
| **层3 置信度联动** | 0 / 0 | **真失效**：条件 `confidence ∈ {uncertain, borderline}` 且 `decisive 占比 > 0.7` **数学上不可能成立**（实测占比上限 0.10 / 0.083）→ 详见 **§5.5①** |
| **数值校验修正** | 0 / 0 | **结构性失效**：PSAD 分支要求 free_text 含 prostate volume，而该字段不可得 → 分支恒不进入 → 详见 **§5.5②** |
| atomic FALSE通路 | 0 | 已知真问题（FALSE=0，`_fact_check_flags` 输出悬空） |
| self_refine 反思 | 158 / 187 | **产物悬空（纯浪费）**：7 处"读取者"经核对全非消费，且不回灌 `messages` → 详见 **§5.5③** |

### 5.4 门的新局限（待补）

- **门不知道"批次是否早于代码改动"**：T1 批的层1c/1d 报 0 是版本错配，不是机制失效。
  需在门里记录并比对**构建指纹**（git hash 或关键文件 mtime）→ 否则会重演本次的误导。

### 5.5 ★ 三个"真 0"机制的最终判定（已查清）

**① 层3 置信度联动 —— 真失效：门控永不成立**

条件（`form_fill.py:897-915`）：`confidence ∈ {uncertain, borderline}` **且** `decisive 占比 > 0.7`。

| 批 | decisive 占比 min / median / max | `> 0.7` 的例数 |
|---|---|---|
| T1（91 例） | 0.000 / 0.100 / **0.100** | **0** |
| T2（72 例） | 0.000 / 0.000 / **0.083** | **0** |

占比上限 0.100 / 0.083，距阈值 0.7 差 **7~8 倍** → 数学上不可能成立。
根因：**上游层1/层1b 的 GT 驱动校准把 `decisive` 几乎清零**
（T2 层1 触发 79 次降级 vs 层1b 仅 11 次升级）。层3 的前提已被上游消灭。
→ 判定：**该退役**（不是修阈值——它的设计前提已不适用）。

**② 数值校验修正 —— 结构性失效：前置条件在数据上不成立**

`_calculate_and_verify`（`form_fill.py:485-580`）只做三件事：PSAD、PSADT、T3 BCR。
其 PSAD 分支要求 free_text 里**同时出现 PSA 与 prostate volume**：

```python
if psa_match and vol_match:          # ← volume 不可得 → 整个分支不进入
    ...
    if re.search(r"PSAD.*(high|elevated|...)", free_text):
        if true_psad < 0.15:
            _downgrade_confidence_post(...)   # ← 连带的置信度降级也永不触发
```

而 **prostate volume 在本数据里不可得**（patient 字段没有；8 个工具也无覆盖），
所以 LLM 无从算出 PSAD，也就无从在 free_text 里写出 volume → 分支恒不进入。

> **这暴露一个真实缺口**：T2-111 的 free_text 写了 `low PSA density (0.09)`，
> `self_refine` 自己都点名它是 **fabricated**（"neither prostate volume nor PSA density
> are provided in the evidence transcript"）——而**数值校验本应拦住这类伪造，却因缺 volume 拦不住**。
> 即：**LLM 可以凭空编造派生量并把它当决策依据，现有校验结构性看不见。**

**③ self_refine —— 产物悬空（纯浪费，不是"无增益"）**

门报"7 处读取者，需人工确认"。**逐处核对结果：7 处全部不是消费**：

| 位置 | 性质 |
|---|---|
| `run.py:91` / `run.py.bak_gate:90` | docstring 提及 |
| `graph.py:72` | TypedDict 字段**声明** |
| `graph.py:604` / `graph.py:638` | **写**（return 到 state） |
| `graph.py:630-631` | **写**进 debug dump |
| `run.py:109` / `run.py.bak_gate:108` | **trace 序列化** |

且 `reflect_node`（`graph.py:600-641`）只 `return {"refined_transcript": refined, ...}`，
**完全不回灌 `messages`**；而 `form_fill` 的 `transcript = _final_assistant_text(messages)`
读的是**原始**消息 → 精炼结果从未进入下游。

→ 判定：**self_refine 每次运行都在烧 LLM 调用（T1 187 次 / T2 158 次）且产出全部丢弃**。
这既解释了运行时长（T2-111 多花 ~2 分钟跑 2 轮迭代），也是"消融开/关输出逐位相同"的**真正原因**：
不是机制无效，是**输出悬空**。修法见待办 4（回灌 messages 或改走 `form_fill._run_self_refine`）。

---

## 6. 变更记录

- 2026-09-27：创建。按用户五条体检项，3 个并行子 Agent 完成只读审计；
  确认变体跨折泄漏（含确切位置）、T3 评估折污染、5 个新休眠机制、
  self_refine「无效」结论因缺正对照而错误（修正后 +7.4pp）、9 个脚本"不写就错"。
  同时修正本 Agent 先前的一个测量方法错误（warnings 不进 log）。
- 2026-09-27（补）：新增 §5——首次对真实批次（T1 91 / T2 72）跑门；
  发现并修复门的 4 个假报警（层1d/层2 任务限定未登记、不确定度校准探针文案不符、
  告警键用 `split(":")[0]` 致机制无法区分）；确认 T1 批早于层1c/1d 改动；
  判定层3 与数值校验修正为**真 0 次**，待修。
- ⚠️ **更正**：本条上文的「self_refine 修正后 +7.4pp」**已被 exp-032 否证**
  （P9 Arm A：接通后 Δrank −0.0031，Δ/SE −0.33，胜/负 33/33 = 随机扰动）。
  且已查明那个 +7.4pp 源自 **N=30 的消融（fixed 3 / broke 1，净 2 例）**，
  从未大样本复核。`configs/config.yaml: agent.form_fill.self_refine` 已按此关闭。
- 2026-09-27（补 2）：新增 §5.5——三个"真 0"机制判定完毕：
  ① 层3 = 门控永不成立（该退役）；② 数值校验 = 结构性失效（缺 volume，且暴露
  "LLM 可编造派生量而校验看不见"的缺口）；③ self_refine = 产物悬空（7 处读取者全非消费，
  不回灌 messages）。

---

## 7. 门修复：把「已退役」与「意外断开」区分开（2026-09-28）

### 7.1 问题

exp-033 依结论关闭了 reflect 节点（`configs/config.yaml: agent.form_fill.self_refine=false`），
但 `MECHANISMS["self_refine 反思"]` 的探针依赖日志 `self_refine: X iter` → **每批都报 `✗ 未接通`**。
即一条永久假报警 —— 正是本项目教训里的「**假报警是 gate 最常见的死法**（噪声多了就被忽略）」。

### 7.2 修法（机械化，不硬编码当期结论）

新增 `RETIRED_WHEN` 登记表：`{机制名: (config 点号路径, 表示"已关闭"的取值)}`。
若某机制的 config 门控**被明确关闭** → 报 `○ 已退役` 且**不计 FAIL**；
门控改回开启时自动恢复正常的 ✓/✗ 判定（无需改门）。

实现：`tools/patch_gate_retired.py`（备份 `connectivity_gate.py.bak_retired`）
新增 `RETIRED_WHEN` / `_cfg_get` / `_retired_reason` / `_find_config` 与 `--config` 参数。

### 7.3 实测（p10_t1_ctl，91 例）

```
  ○ self_refine 反思    ✗ 未接通 | ✓ 被消费（7 处…）  [已退役] config `agent.form_fill.self_refine=False`
                                                     已明确关闭（**非失效**；若改回开启，须重新体检确认接通）
```

对比修复前：该行原为 `✗ … ← **未接通**（不等于『无增益』）` 并**拉低整体判定**。

### 7.4 尚未处理（需决策，未擅自放宽门）

| 机制 | 现状 | 说明 |
|---|---|---|
| **层3 置信度联动** | 仍报 `✗ 未接通` | 真 0：门控要求 decisive 占比 > 0.7，实测上限 0.08~0.10（差 7~8 倍）。§5.5 结论为"该退役" |
| **数值校验修正** | 仍报 `✗ 未接通` | 真 0：分支要求 free_text 同时含 PSA 与 **prostate volume**，而 volume 在本数据不可得。★ 其背后是一个**真实且未修的缺口**：LLM 可编造派生量（如 PSAD），现有校验结构性看不见 |

**为什么不直接登记为休眠**：这两条属"结构不可达"，语义上更接近已有的 `KNOWN_DORMANT` 而非 `RETIRED_WHEN`；
但其中**数值校验修正的缺口仍是活的**（不是"已接受"），登记休眠会让它从雷达上消失 ——
正是本项目「诊断写进报告 ≠ 修复」的历史教训。**故留给用户决策。**