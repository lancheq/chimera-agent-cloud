# G2-TRACE-REVIEW · 三例逐层归因与 T3 时域裁定

2026-11-19，北京时间。内部记录，私有。只读复核冻结运行、源码与官方资料；零模型请求、零 GPU、未打开 GT、未改动任何推理、Prompt、阈值、配置或冻结工件。

## 1. 结论

T3 的 `months_to_recurrence=18` 在诊断阶段就已产生，填表只做复述。全链无硬编码、无默认值、无尺度转换、无回退、无重写。规划评审"正式数值首次出现于填表"的表述与逐行证据不符，需更正。

F06 的 `60` 是另一回事：诊断明写"不可得"，数值首次出现在填表，由本地模板的 `6-60 / NEVER 0` 约束逼出，取范围上界。

T1 的误停发生在正式运行完成之后的监督读回，推理原路径完好，属读回器取错字段。

T2 的检索回执确实进入诊断上下文，模型也正确识别了命中人群不匹配，但最终治疗表述不在命中内。

T3 时间数值的晋级裁定为 **HOLD**，仅限临床标定层面；字段合法性、传递完整性与可复核性为 GO。

## 2. 方法与边界

本轮只做读取与重算。使用的工具：SSH 读取服务器文件、Python 标准库解析 JSON/JSONL、`sha256sum` 重算指纹、官方 Grand Challenge 页面抓取。未启动模型服务，未调用 MCP，未读写 GT 或参考理由，未修改服务器上任何文件。

工作副本落在本地 `/Users/lancheq/WorkBuddy/2026-11-19-20-22-45/_ro/`，两份源目录分别同步，不交叉。仓库 HEAD 为 `7301642d0eedda2f143ba44b93d42f948249d813`，与两份 handoff 记载一致。

复核时间：2026-11-19 20:23–20:32（北京时间）。任务卡 sha256 `b19319ff…`，规划评审 sha256 `cb3c40fb…`。

## 3. T3 时域：逐层证据

### 3.1 六段链路

同一冻结请求（`diagnosis` 为 attempt 5，`form_fill` 为 attempt 6），逐段定位如下。事件序号取自 `remaining/live/T3/audit/545e7da528314f4ca8717b288702de0f/events.jsonl`。

| 序号 | 阶段 | 记录类型 | 时域状态 |
| --- | --- | --- | --- |
| 8–11 | planner | wire/model_response | 无月份概念，`18` 计数 0 |
| 13–26 | policy ×3 | wire/model_response | 无月份概念，`18` 计数 0 |
| 32 | diagnosis | model_response | **首次出现**：正文含 `12–24 months` 区间与 `**Months to Recurrence:** 18` |
| 33 | formfill_input | consumption | `transcript` 与诊断输出逐字相同，sha256 均为 `78bc5362…`，长度同为 2544 |
| 34 | formfill_request_input | consumption | `user` 含 `Months to Recurrence` ×1、`18` ×1，transcript 以 verbatim 嵌入 |
| 35–39 | form_fill | wire/model_response | 原始输出 `{"event": 1, "months_to_recurrence": 18, …}` |
| 40 | parsed_judgment | consumption | `months_to_recurrence` = 18.0，`warnings` 为空，`fallback` 为 null |
| 42 | internal_reflection | consumption | `status=disabled`，`changed_fields` 为空 |
| 43 | atomic_verification | consumption | `status=disabled`，`changed_fields` 为空 |
| 44 | verified_judgment | consumption | `status=disabled`，`changed_fields` 为空 |
| 46 | postprocessing | consumption | `status=disabled`，`reason=Guarded evidence-only boundary` |
| 48 | assembled_output | consumption | 仅补 `case_id/case_type/patient/reveal_sequence/schema_version/task`，`months` 未变 |
| 51 | prediction_file | consumption | `changed_fields` 为空 |
| 53 | final_sockets | consumption | 投影 `decision=/event + /months_to_recurrence`，`reasoning=/free_text` |

### 3.2 数值来源判定

**直接可证。**

- `18` 是诊断模型给出的点估计，上下文为"within the first 12–24 months post-surgery"，理由写作 "Estimated based on high-risk pathological features"。
- 该值在填表阶段被完整接收并原样输出，`form_fill` 未见重算痕迹：输出 `18` 与输入 `18` 一致，`free_text` 也复述 "within 18 months"。
- 四道后处理闸门（内部反思、原子校验、验证判据、后处理）全部 disabled，改动集为空。校准函数 `_apply_t3_tau`（τ=24.0）存在但未执行，`verified_judgment` 记录 `configured=false`。
- 事件链 digest 连续：`parsed_judgment` 的 `after_digest` 为 `dd205b4c…`，经 `internal_reflection`、`atomic_verification`、`verified_judgment`、`postprocessing` 四段后仍为 `dd205b4c…`，到 `assembled_output` 才变为 `f2fa1c8a…`（补入身份字段）。

**可排除。**

- 硬编码：`form_fill.py` 全文只有两处 `months` 常量，均为提示文本（"typically 6-60 months" 与 skeleton 的 `<number 6-60>`），无 `18` 或 `60` 赋值语句。`schema.py` 的 `Task3Judgment.months_to_recurrence` 只有 `ge=0.0`。
- 默认值：`form_fill.py:1015` 只在"仅凭 baseline PSA 套阈值"的降级分支写 `None`，本 conditions 未触发（`event=1`，`warnings` 空）。
- 提示示例：诊断 system（8623 字符）中唯一的 `18` 是 T1 的示例 "PSA 18.0"，与 T3 时域无关。
- 尺度转换：18 与 "18 months" 同尺度，F06 的 60 与 "5 years" 同尺度，均无月/年换算错误。
- 检索污染：T3 的 `rag_status=not_triggered`，`search_guidelines` 未进入允许工具集。

**缺因果证据，登记 UNKNOWN。**

- 18 这个具体数字没有本病例证据锚定。三份工具报告（手术病理、活检病理、MRI）不含任何随访时长或复发时间，术前字段只有 `age`、`psa`、`active_treatment_prior_to_surgery`。草稿的 "typical kinetics for this risk stratum" 是模型内部知识，本轮无法核对来源。
- 观察随访与观察复发均为 UNKNOWN：`t3_upfront_admitted` 的 `retained_fields` 只有三项，工具集不含随访字段。

### 3.3 字段合法性与标定状态

| 维度 | 判定 | 依据 |
| --- | --- | --- |
| 字段合法 | 通过 | `event=1` 在 `ge=0, le=1` 内；`months=18.0` 为 float 且 `ge=0.0`；官方 `validate_record` 只要求两者可归一 |
| 基于允许证据 | 部分 | 病理特征（pT3b、精囊侵犯、切缘阳性、pN+、PI-RADS 5、ISUP GG3）全部来自白名单工具；但 18 本身无证据锚点 |
| 可解释 | 通过 | 草稿给出区间与点估计及理由 |
| 已校准 | 不通过 | 无任何校准阶段执行；无随访真值可比对（GT 未开） |

### 3.4 Socket 合同独立评估

T3 双 Socket 与官方要求一致：

- `prostate-time-to-recurrence-or-last-follow-up.json` 只含 `{"event": 1, "months_to_recurrence": 18.0}`，sha256 `41fde3ce…`，与 `final_sockets.files.decision.sha256` 相符。
- `prostate-time-to-recurrence-or-last-follow-up-reasoning.json` 为纯字符串，对应官方"free-text explanation"，sha256 `73eab060…`。
- 本地 `prediction.json` 多出的 `patient`、`reveal_sequence`、`repeat_test`、`schema_version`、`task` 是运行时中间产物，不进入 Socket，不构成合同偏差。

官方页面要求 T3 输出两个文件，字段名为 `months_to_recurrence` 与 `event`，与本地实现一致。

## 4. F06 对照：60 的来源

F06 与真实 T3 是不同输入（`clinical-data.json` sha256 分别为 `81f602c5…` 与 `fe4bfad5…`），F06 无 `case_pilot_registry` 记录，属工程夹具。

**F06 逐段状态。**

| 序号 | 阶段 | `60` 计数 | 说明 |
| --- | --- | --- | --- |
| 11–35 | planner + policy ×5 | 0 | 全程无月份 |
| 42 | diagnosis | 0 | 明写 `Months to recurrence: Not available`，并说明 "last follow-up duration not documented" |
| 50 | form_fill | 1 | `"months_to_recurrence": 60`，reasoning 写 "Predict no recurrence within 5 years" |

**关键点在填表请求本身。** F06 的 `formfill_request_input.user`（3748 字符）同时包含三样东西：`6-60` 出现 2 次、`NEVER 0` 出现 1 次、`Not available` 出现 1 次。同一个 prompt 一边说数值不可得，一边强制要求填 6 到 60 之间的数且不能为 0。模型没有随访数据，只能就范。

**逐项回答。**

- 是否只是该次预测措辞：不是。60 与 "within 5 years" 是同一判断的两种写法，5×12=60，尺度自洽，不是笔误。
- 是否固定策略：是。约束来自模板而非病例，任何"随访未记录 + event=0"的输入在同一模板下都会被逼出一个范围值。本例取上界。
- 是否有被观察的随访证据：没有。诊断阶段已明确随访时长未记录，`follow_up_source_delivered=false`。

**代码层面的归属。** 官方 `_norm_months` 只做 `float()` 转换，无上下界；官方 `validate_record` 只检查可归一。`6-60` 与 `NEVER 0` 是本地实现加严，引入于 `33a961a`（"baseline: A 类修复前的生产状态"）。这是一条本地规则，不是官方合同。

**对官方打分的影响。** `recurrence_time_score` 对删失病例的规则是"预测时间不早于真实随访时长即满分"，所以填 60 在多数删失病例上拿满分。这个分数只作诊断，不进榜。进榜的 C-index 只用预测月份的相对排序，60 会把该例推向最低风险端。

## 5. 对既有审查的更正

`remaining/T3_time_semantics_review.json` 记录 `formal_numeric_field_first.stage = "form_fill"`，规划评审据此写下"正式数值首次出现于填表"。本轮逐行复核认为该结论不成立。

该文件的 `diag_horizon_groups` 只列出三组：684 个月（标为 `unit=year`）、24 个月、6 与 24 个月。这三组分别对应"57-year-old"被误算成 57 年、"12–24 months"的上界、"every 3–6 months for the first 2 years"。**诊断草稿里显式写着的 `Months to Recurrence: 18` 没有被抽出。**

抽取器漏检的原因是可判定的：它的模式匹配只认"数字 + months/years"的相邻形式，而草稿里的 18 是 `**Months to Recurrence:** 18`，数字在标签之后且不带单位。同一模式还把患者年龄 57 当成了时间跨度，产生 684 个月的假阳性。

结论：`formal_numeric_field_first` 应为 `stage=diagnosis, seq=32, value=18`。F06 的 60 才符合"首次出现于填表"。

这条偏差不影响三例的运行结果，只影响归因表述，但会导致后续把时域问题错记到填表层，故在报告中单列。

## 6. T1 误停：读回器取错字段

### 6.1 现象

`continuation/live/T1/case_readback.json`：

```
"integrity_failure": true,
"tool_names_returned": [null, null, null],
"request_count": 6, "http_sent": 6,
"sockets_verified": true, "tool_ledger_ok": true,
"stop": "policy_stop",
"earliest_observed_failure": "after_run_completed"
```

六个请求、六次 HTTP、双 Socket 校验通过、账本通过、策略正常停止，唯独工具名是三个 `null`。

### 6.2 根因

`continuation/read_case.py:53`：

```python
payload_tools = [e.get('tool') for e in tool_records if e['kind']=='tool_return']
```

`tool_records` 读的是 `candidate_events.jsonl`（第 43–45 行），不是 `events.jsonl`。该文件的 `tool_return` 记录字段为 `budget_after / elapsed_seconds / kind / message / previous / request_digest / request_id / seq / sha256 / timestamp`，**没有顶层 `tool` 键**；工具名在 `message[0].data.name` 里。

于是 `payload_tools = [None, None, None]`，第 74–75 行的 `not set(payload_tools) <= set(allowed)` 求值为真，`integrity_failure` 被置位。

这是读回器的字段路径错误，与推理路径无关。

### 6.3 推理原路径的完整性

同一批证据证明正式运行本身没有问题：

- 账本 `spent_units=3`、`admitted_attempts=3`、`pending_requests=0`，三个工具各计 1 unit，`allowed` 全为 true。
- 白名单为 `get_lab_results / get_mri_report / get_previous_notes / get_psa_trend / search_guidelines`，四个 control 阶段的 tool 列表与白名单完全一致（`private_trace_checks.json`，`exactly_allowed=true`）。
- 诊断请求（seq 27）含 `Actual verified observations:` 段，三个工具的观测按序绑定，与账本 `response_digest` 相符。
- 六个 `wire_attempt` 的 `payload_digest` 与 `http_request_verified`、`model_response.usage.prompt_tokens` 三方一致。

### 6.4 处理与回归

原停点保留，未重跑（`earlier_T1.rerun=false`），只做离线回读。补丁 `read_case_supervisor_fix.patch` 把取数改为 `matched_tool_names()`，并新增 `tool_message_readback_error` 字段，读回失败时显式置位而非静默通过。

回归 `test_tool_receipt_readback.py` 共 6 项：1 项接受真实序列化形状，并断言 `journal[1].get('tool') is None`（把旧缺陷固化进测试）；5 项参数化拒绝（错名、错 call_id、错 digest、未授权工具、重复键）。日志 `tool_receipt_regression.log` 记录 6 passed。

补丁 `patch_deployed_for_live=false`，只用于离线回放。后续若要再跑 T1，需先把这个补丁部署到正式读回路径，否则同样误停会重演。

## 7. T2 检索：进入上下文，但不适用

### 7.1 逐段

| 环节 | 证据 |
| --- | --- |
| 计划或原因 | policy seq 12 计划 `search_guidelines`，purpose 为 "Identify standard-of-care multimodal therapy for locally advanced prostate cancer" |
| 实际查询 | `cT3a prostate cancer treatment recommendations EAU NCCN` |
| 命中来源与版本 | 8 条，全部 `source=EAU`，同一快照 `13fbd478…`，正文标注 "PROSTATE CANCER - LIMITED UPDATE MARCH 2025" |
| 适用人群 | 见下表 |
| 回执消费 | 诊断请求 `user`（14770 字符）中 `6.3.4.1` 出现 4 次、`6.3.2.4.2` 出现 4 次、`2.3 Future goals` 出现 2 次 |
| 诊断陈述 | 写 "References to radical prostatectomy for cT3b–T4 disease and multimodal therapy considerations for locally advanced/unfavorable intermediate-risk disease"，随后给出 cT3a 的 EBRT+ADT |
| 填表最终字段 | `treatment_recommendation.primary=active_treatment`，`modalities=[external_beam_radiotherapy, androgen_deprivation_therapy]` |

### 7.2 命中内容与适用性

| # | 章节 | 内容性质 | 与本例（cT3a、PSA 160、ISUP GG2）的关系 |
| --- | --- | --- | --- |
| 0 | 2.3 Future goals | 未来研究计划 | 不适用 |
| 1–2 | 4.4 Prognostic relevance of stratification | 预后分层讨论 | 不适用 |
| 3 | p94 "72 mo" | 生存曲线 | 不适用 |
| 4 | p79 "24 mo." | 生存曲线 | 不适用 |
| 5 | 6.3.4.1 Radical prostatectomy | cT3b–T4 队列的 10 年 CSS/OS | 人群不匹配（本例 cT3a） |
| 6 | 6.3.2.4.2 Brachytherapy | NCCN unfavourable intermediate-risk 的 trimodality | 人群不匹配（本例高危） |
| 7 | 6.3.3.2.1 Lymph node irradiation | cN0 的预防性照射证据 | 与治疗选择主体无关 |

八条命中里没有一条直接覆盖 cT3a 高危局限性前列腺癌的根治性治疗选择。

### 7.3 三段判定

- **实际送入上下文：是。** 命中原文以完整 chunk 形式进入诊断 user 消息，非摘要。
- **准确引用与适用：引用准确，适用不成立。** 模型在草稿里正确复述了命中的适用人群（cT3b–T4、unfavourable intermediate），没有把 6.3.4.1 的结论直接套到 cT3a。随后给出的 "EAU guidelines recommend definitive local therapy… EBRT combined with long-term ADT" 不在任何命中里，是模型内部知识。
- **导致决策变化：UNKNOWN。** 可证的是计划确实因检索而重构：policy seq 18 的 `plan_replaced=true`，理由明写 "the guideline search did not yield specific treatment recommendations for cT3a disease… A targeted guideline search for cT3a treatment is required"，随后改调 `get_mri_report`。不可证的是若没有这次检索，最终治疗选择会不会不同。seq 24 以预算/策略轮耗尽停止，未执行补检索。最终 `active_treatment` 与 EBRT+ADT 的表述与检索内容没有可追踪的对应关系。

诊断与填表在 `active_treatment` 上一致，`transcript` 逐字传递（2592 字符，与诊断输出相同），不存在跨层改写。

## 8. 三例逐层矩阵

阶段取值为该层最早可观察的错误或"无"。`F06` 单列，不计入三例。

| 层 | T1 | T2 | T3 | F06（对照） |
| --- | --- | --- | --- | --- |
| 输入 | 无 | 无 | 无（随访字段不在允许集） | 无（随访不在夹具内） |
| 计划 | 无 | 无 | 无 | 无 |
| 取证 | 无，3 工具全部授权并记账 | 无，4 工具含 1 次 RAG | 无，3 工具全部授权 | 无 |
| 停止 | 无，`policy_stop` | 无，`policy_stop`（策略轮用尽，补检索未执行） | 无，`policy_stop` | 无，`policy_stop` |
| 诊断 | 无 | 指南表述越出命中范围 | **18 首次出现，无随访锚点** | 明写不可得 |
| 审核状态 | `legacy_behavior_unreviewed` | 同左 | 同左 | 同左 |
| 填表 | 无 | 与诊断一致 | 复述 18 | **60 首次出现，模板逼出** |
| 后处理 | 四闸门 disabled，改动 0 | 同左 | 同左 | 同左 |
| Socket | 双文件指纹相符 | 同左 | 同左 | 同左 |
| 读回 | **误停：工具名取到 null** | 无 | 无 | 无 |
| 错误最早可观察点 | 监督读回 `read_case.py:53` | 诊断段的越界表述 | 诊断段 seq 32 | 填表段 seq 50 |
| 跨层变化 | 无（推理路径未被影响） | 计划重构，决策方向未变 | 无数值改写 | 无数值改写，但由无到有 |

四例的 Socket 指纹本轮全部独立重算通过：

| 例 | decision sha256 | reasoning sha256 |
| --- | --- | --- |
| T1 | `6c76a933…` | `158aa884…` |
| T2 | `65cbc205…` | `d54d0383…` |
| T3 | `41fde3ce…` | `73eab060…` |
| F06 | `18ead55e…` | `603af237…` |

## 9. 五源定范围

| 来源 | T1 | T2 | T3 | F06 |
| --- | --- | --- | --- | --- |
| 隔离 | 排除：子进程无法访问隔离外文件，MCP 白名单生效 | 排除 | 排除 | 排除 |
| 数据 | 排除：三份输入 sha256 与批准视图一致 | 排除：命中确实不含 cT3a 推荐 | **缺随访字段**，非错误但构成缺证 | 同左 |
| 模型推理 | 排除 | 部分：越出命中的指南表述来自内部知识 | **18 为自估点估计** | event=0 判定由模型给出 |
| 表单/程序 | 排除 | 排除 | 排除 | **6-60 / NEVER 0 模板约束** |
| 审计 | **读回器字段路径错误** | 排除 | **时域抽取器假阳（57→684）与假阴（漏 18）** | 同左 |

三例中没有任何一例可归到隔离或数据污染。T1 的问题全部在审计层，T3 的数值问题在模型推理层，F06 的问题横跨表单层与模型层，T2 的不适用在数据层（检索语料覆盖不足）与推理层（越界表述）。

## 10. T3 数值晋级裁定

**结论：HOLD。** 仅针对数值的临床标定，不针对机制与合同。

拆开看三项：

- **合同与传递：GO。** 字段合法，诊断到填表逐字传递，四道后处理闸门无改写，Socket 投影符合官方两文件格式，指纹可独立重算。这一层没有待修项。
- **机制可复现：GO。** 18 的产生路径完全可追踪，排除了硬编码、默认值、提示示例、尺度转换与检索污染。
- **临床标定：HOLD。** 18 没有本病例证据支撑，也没有任何校准介入。在官方 C-index 口径下，这个数值唯一的作用是把该例排到队列中的某个风险位次；由于本轮不开 GT、且只有单例，位次正确性无法评估。

**最小缺证，三项，按优先级：**

1. 该病例的观察随访时长与事件状态。这是唯一能判断 18 是否落在合理区间的信息，也是 F06 的 60 无法证伪的原因。当前工具集与 upfront 字段都不提供，需在数据合同层面解决，不能靠模型推断。
2. 官方对时间零点的明文规定。官方页面只写 "time to recurrence or last follow-up"，evaluator 也只写 "time-to-recurrence"；"from treatment" 这个表述只出现在本地 `form_fill.py:2070`。需要官方文档或 baseline 明确，否则"18 个月"从哪个起点算起没有权威依据。
3. 一个不产生假阳/假阴的时域抽取器。现有实现把年龄当时间跨度、又漏掉显式字段，已经导致过一次错误归因。

在拿到第 1 项之前，不建议把 18 用于任何阈值调整、校准拟合或跨例比较。

## 11. 工件索引

路径相对项目根 `~/CHIMERA-agent/`。

**交接与规划**

- `reports/ROUND2_G2_CASE_PILOT_BATCH_PLANNING_REVIEW_20261119.md`
- `output/g2_case_pilot_20261119/handoff.json`
- `output/g2_case_pilot_20261119/continuation/handoff.json`（229 项指纹）
- `output/g2_case_pilot_20261119/remaining/handoff.json`（124 项指纹）
- `discussion/tasks/20261119_G2_TRACE_REVIEW.md`

**本轮接手的既有取证（19:55 生成，本轮未重做）**

- `output/g2_trace_review_20261119/artifact_index.json`：229 + 124 + 15 项交接指纹全部 matched，73 个冻结源码 per seal，`rehash_matches=true`
- `output/g2_trace_review_20261119/stage_evidence.json`：四例的 runtime/controller 链哈希、工具回执绑定、诊断响应定位（T3 为 seq 32 / line 33）

这两份文件由上一段会话产出，本轮只做引用与抽查；其 T3 诊断定位与本轮逐行复核一致。

**T3**

- `output/g2_case_pilot_20261119/remaining/live/T3/audit/545e7da5…/events.jsonl`（55 条）
- 同目录 `temporary_evidence/output/task3/gc-case/prediction.json`
- 同目录 `temporary_evidence/output/trace/gc-case/form_fill_attempt1.json`
- 同目录 `temporary_evidence/output/trace/gc-case/judgment_pre_calibration.json`
- 同目录 `temporary_evidence/output/task3/gc-case/processing_audit.json`
- `output/g2_case_pilot_20261119/remaining/live/T3/sockets/`（两个文件）
- `output/g2_case_pilot_20261119/remaining/T3_time_semantics_review.json`（结论已更正，见第 5 节）

**F06**

- `output/a_f06_budget_recheck_20261119/live/host_cases/F06/private/2932b71e…/events.jsonl`（66 条）
- 同目录 `temporary_evidence/output/task3/gc-case/prediction.json` 与 `trace/`
- `output/a_f06_budget_recheck_20261119/live/host_cases/F06/sockets/`
- `output/a_f06_budget_recheck_20261119/offline_identity.json`（夹具指纹）

**T1**

- `output/g2_case_pilot_20261119/continuation/live/T1/case_readback.json`（原始误停）
- `output/g2_case_pilot_20261119/continuation/live/T1/audit/c8e71cbf…/events.jsonl`（53 条）
- 同目录 `temporary_evidence/output/task1/gc-case/candidate_events.jsonl`（24 条）
- `output/g2_case_pilot_20261119/continuation/read_case.py`（缺陷位置第 53 行）
- `output/g2_case_pilot_20261119/continuation/read_case_supervisor_fix.patch`
- `output/g2_case_pilot_20261119/continuation/read_tool_receipts.py`
- `output/g2_case_pilot_20261119/continuation/test_tool_receipt_readback.py`
- `output/g2_case_pilot_20261119/remaining/T1_sealed_reader_replay.json`

**T2**

- `output/g2_case_pilot_20261119/remaining/live/T2/audit/17185faa…/events.jsonl`（58 条）
- 同目录 `temporary_evidence/output/task2/gc-case/candidate_events.jsonl`（30 条，含 4 次工具请求）
- `output/g2_case_pilot_20261119/remaining/live/T2/sockets/`
- `output/g2_case_pilot_20261119/remaining/layer_summary.json`

**官方与源码**

- `output/round2_20261110/official_evaluator/evaluate.py`，sha256 `8684b9c4104d227bd08325fbef3c67ccd826e2836155db10f571f6eb16316a99`，与 `configs/round2_evaluator.json` 的 pin 一致
- `src/chimera_agent_baseline/agent/form_fill.py`（第 2070、2116–2117 行为模板约束）
- `src/chimera_agent_baseline/output/schema.py`（`Task3Judgment` 仅 `ge=0.0`）
- 官方页面：`https://chimera-agent.grand-challenge.org/data-sources-and-imaging/`、`https://chimera-agent.grand-challenge.org/baseline-and-evaluation-repositories`

## 12. 本轮执行记录

- 模型请求：0。GPU 作业：0。MCP 调用：0。
- GT 或参考理由读取：0。样本重选：0。
- 冻结工件修改：0。服务器写入：仅本报告与本目录的 handoff.json。
- 隔离状态：只读同步至本地工作副本，服务器原件未改动。
- 目标泄漏迹象：未发现。三例的输入、工具返回、诊断与填表均未出现 GT 字段。

首轮取证到此为止，输出保全后停止。
