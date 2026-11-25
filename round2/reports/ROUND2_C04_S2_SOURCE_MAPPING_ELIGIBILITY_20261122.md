# C04-S2 · 三处正例的来源映射资格与隔离对照提案

2026-11-22，北京时间 21:52–22:00 · 只读资格核对，未复制 overlay、未建候选、未跑检索、未调模型

**三处位置只有两段互异正文：一段可绑，两段不可绑。** Q02 第 5 位（unified-0179）含完整分级推荐，条件与例外齐全，可作条件参考；Q03 第 2 位与 Q04 第 4 位是同一段 unified-0204，即 EAU 2025 §5.7.1 的整节正文，**全节没有任何强弱分级**，条件只有「取决于危险因素（如 PSA 密度、PI-RADS 评分）」这类定性表述，按现有 schema 绑不成 `conditional_reference`。

另有一条结构性问题，比这三处更根本：**既有 overlay 的两条条件参考全部挂在 QA 行上，指南正文切片一条都没挂。** 这解释了上一轮 48 个位置里 38 个 `unknown_source_binding`。

## 核对对象与方法

只读，2 线程。三处位置取自 `output/planning_C01_frozen_retrieval_replay_20261118/` 的冻结返回，正文从冻结知识快照按 record_id 取回（SHA256 与本轮输入一致）。原文载体用 A01 已核的 `output/evidence_applicability_audit_20261117/sources/eau_2025_official.txt`（与 overlay `sources.eau_2025.files` 登记的 SHA 相同），对照版用 `output/planning_C01_retrieval_20261118/eau_2026_official.txt`。

匹配沿用 overlay 自己的规范化：合并空白，并把行尾 `[ \t]{2,}(Weak|Strong)` 去掉（`layout_normalization: eau_right_column_strength`）。这是 `load_overlay` 校验 `primary_text` 时的同一套口径，所以下面的通过与否可直接迁移到候选构建。

## 资格矩阵

| 位置 | 记录 | 正文特征 | 强度标记 | 连续切片 | 2025 判别 | 2026 判别 | 结论 |
|---|---|---|---:|---|---|---|---|
| Q02 r5 | unified-0179 | 中段起（"out MRI."），含 6 条分级推荐 | 6 | 是 | 通过 | 原文不在 | **可绑**（单条） |
| Q03 r2 | unified-0204 | 起于 §5.7.1 标题，整节正文 | 0 | 是 | 通过 | 原文不在 | **不可绑** |
| Q04 r4 | unified-0204 | 同上，同一段 | 0 | 是 | 通过 | 原文不在 | **不可绑** |

两段正文都是官方 2025 的连续切片，去掉强弱列后能在规范化原文里逐字定位（Q02 r5 起点 offset 148041，Q03/Q04 起点 offset 167554），没有拼接、改写或跨版本混入。三条候选句子在 2026 版里都找不到，版本判定没有歧义。

节级强度分布是关键证据：§5.5.7 共 1844 字符、10 条分级推荐、10 个强弱标记；§5.7.1 共 1009 字符、0 条分级推荐、0 个强弱标记。§5.7.1 在 EAU 2025 里是叙述性小节，不是推荐表。

## 逐条判读

**Q02 r5 可绑，但要接受一条限制。** 该切片里目标句完整：「When MRI is negative (i.e., PI-RADS ≤ 2), and clinical suspicion of PCa is low (PSA density < 0.20 ng/mL/cc, negative DRE findings, no family history), omit biopsy and offer PSA monitoring; otherwise consider systematic biopsy」，强度 Weak，条件全在句内，例外（otherwise 分支）保留。这句话与 overlay 现有 `qa:169` 条目（sha 344fd6b7…）的 `primary_text` 完全一致，所以原文核验已经做过一次，本轮只是把载体换成正文切片。

限制在于切片里有 6 条分级推荐，而 `package()` 一条命中只输出一个 `primary_text`（`load_overlay` 还对重复 `text_sha256` 显式报 `duplicate_overlay_source`）。绑 unified-0179 只能露出其中一条，另外五条仍不可见。这不是本轮能解的，属 schema 层面的「一条切片一个条目」。

**Q03/Q04 不可绑。** §5.7.1 全节 1009 字符里没有 `Weak` 或 `Strong`，也没有推荐表行；能当行动句的只有「Therefore, follow-up or direct repeat biopsy should be considered depending on risk factors (e.g., PSA density, PI-RADS score)」。它没有分级、没有阈值、没有例外，条件是开放式的。要把它绑成条件参考，必须自己补一个强度和一组数值切点，这两件事 A01 与 C02 都明确禁止（`unknown` 不得猜填，`strength` 为 unknown 时 `load_overlay` 直接抛 `unknown_recommendation_strength`）。按接续口径「来源绑定失败即记缺证并停止」，这两条记为缺证，不进入候选。

## 结构性问题：条件参考全挂在 QA 行

把 overlay 六条命中按载体拆开：

| 处置 | 载体 | 记录 | 有 primary_text | 条件数 |
|---|---|---|---:|---:|
| conditional_reference | QA 行 | qa:169 | 是 | 5 |
| conditional_reference | QA 行 | qa:170 | 是 | 5 |
| directory | 正文切片 | chunk-0005 | 否 | 0 |
| directory | 正文切片 | chunk-0006 | 否 | 0 |
| unverified_task_adaptation | 正文切片 | unified-1382 | 否 | 0 |
| unverified_task_adaptation | 正文切片 | unified-1387 | 否 | 0 |

条件参考在指南正文切片上的数量是 0。也就是说 C02 的映射是按 QA 行的文本 SHA 建的，而检索返回的主体是正文切片，两者文本不同、SHA 不同，于是任何被召回的正文切片都天然落进 `unknown_source_binding`。上一轮 38/48 的来源未绑定不是映射遗漏了几条，而是映射的载体类别和检索返回的载体类别错位。

这条发现改变下一轮的做法：不是「补三条条款」，而是「给正文切片加第一条条件参考」，属新的载体类别，需要规划明确认可。

## 隔离 overlay 对照提案（不执行，待另审）

拟议唯一可变项：在独立副本里新增 **1 条** 正文切片条件参考，不删不改既有六条。

| 字段 | 拟填 |
|---|---|
| `text_sha256` | Q02 r5 的冻结 SHA（unified-0179） |
| `handling` | `conditional_reference` |
| `document_id` | `eau_2025` |
| `locator` | `section 5.5.7, printed p41, chunk unified-0179` |
| `primary_text` | 上述 negative-MRI 句，与 `qa:169` 同一句 |
| `strength` | `Weak` |
| `conditions` | 与 `qa:169` 相同的五条：`biopsy_context in [initial, prior_negative]`、`pi_rads le 2`、`psa_density lt 0.20`、`dre eq negative`、`family_history_prostate_cancer eq false` |
| `exceptions` | 沿用 `qa:169` 的 otherwise 分支说明 |

预期读数（静态推演，未跑）：Q02 参考数 0→1，其余五条查询仍为 0；六条合计 0→1；48 个位置顺序、目录与任务摘要的降级、`automatic_action_authorized` 恒 false 均不变。Q02 的事实层是 `dre=negative`、`biopsy_context/pi_rads/psa_density/family_history` 全 unknown，因此该条参考的 `condition_status_at_retrieval` 会是 `unknown`，而不是 `matched`。这符合设计，也必须写进验收：**未知条件不得显示为已满足**。

验收门槛需要相应修订。上一轮 `next_candidate.md` 写的「返回 ≥3 条」在本轮核对后不成立，应改为：正文切片条件参考 ≥1（Q02）；Q03/Q04 记 `NO_GRADED_RECOMMENDATION_IN_SOURCE` 而不是误拒；其余四条门槛（正例送达、降级不回退、条件透明、顺序不变）照旧。

两个已知风险，需规划裁量：

- 同一句会同时挂在 `qa:169` 与 unified-0179 上。若某个返回窗同时召回两者，模型会看到同一推荐两次。`package()` 没有跨条目去重，本轮也不改。
- 正文切片绑一条后，该切片另外五条分级推荐仍不可见。若要全覆盖，需要「一条切片多条目」的 schema 变更，属另一件事。

## 对既有结论的更正

- C04-S1 报告写「3 条完整同场景推荐」，按本轮核对应改为「1 条完整分级推荐 + 2 段无分级的整节正文」。三处位置对应两段互异文本。C01 的完整口径要求「保留原有评级」，§5.7.1 没有评级可保留，Q03/Q04 的计数应降为行动分支或正文命中。C01 的计数口径是否调整由规划裁定，本轮只提供可复核的原文证据。
- 上一轮 `next_candidate.md` 写「最多补三处条款映射」，应改为「补一处正文切片映射 + 两处缺证登记」。

## 未做与未知

- 未复制 overlay、未生成候选 JSON、未重跑冻结双臂、未接模型。隔离对照需另审后再做。
- 未重新裁定 Q03/Q04 在临床上的证据价值。本轮只判「能否按现有 schema 绑定」，不判该段正文对会诊是否有用。
- §5.7.1 在 2026 版是否有分级推荐未查。冻结返回是 2025 文本，本轮只判 2025。
- 三处之外还有 45 个位置未做资格核对；若下一轮要扩大覆盖，需先按载体类别分账（正文、QA、目录、摘要、发布史）。

## 交付

`output/c04_s2_source_mapping_20261122/`：`eligibility_matrix.json`（三位置的逐项读数）、`source_binding_evidence.json`（overlay 载体分类、节级强度分布、三条候选项的 2025/2026 判别）、`eligibility.py`（可复跑）、`handoff.json`。本报告在 `reports/`。
