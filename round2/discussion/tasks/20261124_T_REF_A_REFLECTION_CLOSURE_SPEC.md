# T-REF-A Reflection 闭环规格（离线，零模型调用）

2026-11-24 · 待派发 · 依据 2026-11-18 G1R 验收报告 + 2026-11-24 源码核对

写回链已经闭环且有合成证据，不必再验。A 线剩下唯一要解决的是「外层自由文本 → 合法 `CorrectionProposal`」这一段——代码里不存在。任务重心因此从"回放断言"改为"补这一段 + 把写回钉成对照基线"。

## 1 已确认事实

| 编号 | 事实 | 证据 |
| --- | --- | --- |
| F1 | 报告口径 151 项 = G1R 专项 70 + G1A 40 + 相关回归 41（今日实测 70 / 40） | `reports/ROUND2_P01_G1R_REFLECTION_CONSUMPTION_20261118.md` 首表；`pytest --collect-only` |
| F2 | 覆盖范围是「审核绑定 → 唯一选择 → 实际赋值 → 落盘投影」；检测与提议全程由合成注入 | 同上，S01–S18 覆盖矩阵 |
| F3 | 契约层只吃对象不吃字符串，文件头自述 `No review or model calls` | `agent/reflection_contract.py:1` |
| F4 | 生产代码唯一构造 `ReviewBatch` 的地方是异常兜底 `ReviewBatch('error')` | `agent/form_fill.py:492` |
| F5 | 合法候选的唯一来源是测试注入，且强制 `profile.purpose == 'synthetic_test_only'` | `inference.py:526-530`；`reflection_contract.py:103-108` |
| F6 | 外层自由文本落成 `external_reflection` 事件，status = `detected_no_executable_proposal`，`consumed=False` | `agent/form_fill.py:455-460` |
| F7 | 「检测到问题」不是结构化判断，是 warning 字符串含 `critique:` 的前缀匹配 | `agent/graph.py:733` ← `agent/self_refine.py:315`、`agent/form_fill.py:1840` |
| F8 | critique 已要求 JSON，但 issues 是纯字符串列表，无字段路径、无目标值；refine 输出修正后的推理轨迹，不是 judgment | `agent/self_refine.py:261-300` |
| F9 | 契约要求 `candidate_json` 是完整 judgment，`validate_judgment` 做 canonical 全等（拒额外字段、拒类型强转） | `reflection_contract.py:142-155` |
| F10 | citations 的 quote 必须逐字出现在 upfront 记录区，否则 `binding_or_authority_rejected` | `reflection_contract.py:212-215` |

F7 + F8 合起来说明：现有实现里"Reflection 没发现问题"这个结论，只能从 warning 文本反推；模型即便发现了，也表达为自然语言 issues，永远到不了字段级。

## 2 只读基线（今日实测，先垫底）

对 `output/trace/` 全量统计：276 个 case 目录，165 例有 `critique_iter1.json`。

| 项 | 计数 |
| --- | --- |
| `parsed_issues` 非空（模型提出了问题） | 73 |
| `parsed_issues` 为空（模型称无问题） | 92 |
| critique 轨迹总数（含 iter2） | 272 |

**读法限定**：这只说明模型在 44%（73/165）的 case 里会主动列出问题，不是检测准确率——没有 GT 对照，也没有判断这些问题真假。但它足以推翻"检测能力本身不足"这个假设：瓶颈不在模型开不开口。同一批 165 例里成功变成结构化候选的是 0。

## 3 断言清单（零生成，全部基于已有 trace 与合成夹具）

### A1 检测信号可结构化程度
- 现状：只有 warning 文本（F7）。
- 做：对 165 例逐例比对三个信号是否一致——`parsed_issues` 是否非空、`severity` 取值、warning 是否含 `critique:`。产出三分类交叉表。
- 通过条件：不一致样本逐条列出并给出归因（解析失败 / severity 缺省 / 空 issues 但扣了 warning）。
- 不做：把计数当检测率。

### A2 提议形态的接纳面
- 做：对 `_parse_critique_json` 穷举输入形态——纯 JSON、markdown fence 包裹、多 JSON 块、单引号、尾随逗号、issues 为字符串数组 / 对象数组 / null、severity 缺失或非枚举。
- 通过条件：输出「哪些形态能进、哪些被静默丢弃、哪些抛错」三列表；静默丢弃必须点名具体分支。
- 目的：定下 B 线需要模型输出什么形态，而不是现在猜。

### A3 写回端到端回执（钉基线）
- 做：复用 S01 / S06 / T2 投影 / S12 / S13 五条夹具，重跑并核对 `FinalReceipt.terminal`。
- 通过条件：与 11-18 记录逐字一致。任何不一致先查未提交变更，不直接判回归（见 R2）。

### A4 citations 可行性
- 做：取 12 例 GT 病例的现有 refined 文本，检查能否摘出至少 1 条"逐字出现在 upfront 记录区"的 quote。
- 通过条件：给出可行比例 + 失败样本的典型错法（改写措辞 / 引用工具返回而非 upfront / 概括而非摘句）。
- 意义：F10 是最容易被低估的工程量，先量再定方案。

## 4 转换层决策（本任务要拍板的一件事）

缺口的具体位置：critique 产出 `issues: [str]`，refine 产出 prose，两者都进不了 `CorrectionProposal.candidate_json`（F8 / F9）。

| 方案 | 做法 | 代价 | 主要风险 |
| --- | --- | --- | --- |
| P1 补丁式 | critique 的 issues 改为 `[{field_path, before, after, reason}]`，程序 apply 到 J0 生成完整 candidate | 改一个 prompt + schema 校验；调用次数不变 | 模型给不出合法字段路径与取值域（T3 months 是数值，T1 biopsy_decision 是枚举） |
| P2 重出判断式 | refine 直接输出完整 judgment JSON，`changed_fields` 由 diff 算 | 改 refine prompt 语义 | refine 原本修推理轨迹，改为重出判断可能损失理由质量 |
| P3 侧解析器 | agent 侧从 prose 抽字段 | 不动 prompt | S04「提议格式错误」已证此类解析脆弱，且引入新错误源 |

倾向 **P2 主 + P1 兜底**：契约要的就是完整对象（F9），P2 直接产出、跳过中间层；P1 作为 P2 输出不合法时的降级。P3 不推荐。

citations 不在本任务拍板。A4 出数后单独定：放宽逐字约束，还是加一步 quote 对齐。

## 5 风险与前置阻塞

**前置阻塞（不做则基线不可复现）**：HEAD 停在 `7301642`（2026-11-01），工作区 270 项未提交变更，`discussion/` 与 round2 相关文件全部 untracked。11-18 的 151 项通过对应一个没有版本号的代码状态。起 A 之前先做一次快照提交或打 tag。

- R1 历史轨迹的 prompt 与当前不同，A1 / §2 的数字只能当现状描述，不得写成检测率。任何引用该数字的结论必须带这一条限定。
- R2 A3 若与 11-18 记录不一致，先查工作区未提交变更（尤其 `form_fill.py`、`graph.py`），再判回归。
- R3 F10 的逐字约束会让 P2 的通过率虚低，A4 必须把 citations 失败与候选格式失败分开统计。

## 6 B 线准入（不在本任务执行）

四条同时满足才起 12 例配对：
1. A2 定下提议形态，且该形态能被 `reflection_contract` 接受
2. A3 基线与 11-18 一致
3. A4 给出 citations 可行方案
4. 视图锁定完成，T1 视图与参赛版一致

指标五项：检测成功率、正确改判、错误改判、写回成功率、额外 token。预算硬门槛在 B 之前定（单例额外 token 增幅上限），超了直接停，不进医学效果裁定。

## 7 边界

不做：真实模型调用、91 例全量、后处理覆写权威改动（待 D05 冻结）、T1 权重表改动。

做完停在 A，产出规格结论交规划验收，不自行进入 B。
