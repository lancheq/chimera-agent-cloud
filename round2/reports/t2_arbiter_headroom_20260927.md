# T2 两路裁决层可行性验证（2026-09-27）

> **问题**：用户提出新增"决策 agent / 裁决层"——收集 LLM 判断 + predictor 判断，由它自己做决策，
> 理由是当前"唯结果论"导致决策-论述错位，且 `_predictor_note` 是把答案塞进 prompt 的 hack。
> **本文用零成本方式回答：这一层值不值得建。**

---

## 0. 结论速览

| 问题 | 结论 |
|---|---|
| LLM 与 predictor 的一致率 | **61.1%（44/72）**，分歧 **28 例** —— ⚠️ **不是**此前引用的 98% |
| 两路 oracle 天花板（每例都取对的那个） | 0.9028，比 predictor 单独 **+0.0417** |
| **只给两个标签的裁决器增益** | **+0.0000（可证：恒等于 predictor）** |
| 裁决层有价值的条件 | 输入必须是**证据**、且必须能识别"LLM 对"的那 3 例 |
| 附带纠正 | `_predictor_note` 确实把答案注入了 LLM prompt，但**锚定无效**（仍分歧 39%） |

---

## 1. 方法（零成本，不需要跑 LLM）

用刚跑完的 T2 全量（`test/output.night_t2_base/task2`，72 例）复原两路决策：

| 量 | 来源 |
|---|---|
| GT | `data/task2/agent_input/<case>/prostate-treatment-decision.json`（裸字符串） |
| predictor 决策 | `prediction.json` 的 `treatment_recommendation.primary`（override 后即 predictor 决策） |
| LLM 原始决策 | `trace.json` → `form_fill_warnings` 里 `decision override: primary X -> Y` 的 **X**；未命中时即等于最终决策 |

脚本：`tools/t2_arbiter_headroom.py`。

> **诚实的口径说明**：predictor 这 72 例是**样本内**预测（bundle 训练含这 72 例），
> 其实测 0.8611 略高于诚实 CV 的 0.853；oracle 上限也因此略偏乐观。

---

## 2. 实测结果

```
predictor 单独准确率 : 0.8611
LLM(原始) 单独准确率 : 0.5972
两路一致率           : 0.6111   (44/72)
分歧子集大小         : 28 例 (38.9%)
  分歧例中 predictor 对 : 22/28   (78.6%)
  分歧例中 LLM 对       : 3/28    (10.7%)
oracle(每例取对的那个) : 0.9028
max(单侧)              : 0.8611
★ 两路裁决层天花板     : +0.0417
最优【标签级】裁决器   : 0.8611  (增益 +0.0000)
```

### 2.1 分歧格分布（LLM, predictor）→ GT

| LLM | predictor | n | GT 分布 | 多数类 |
|---|---|---|---|---|
| active_treatment | active_surveillance | 12 | AS 11 / AT 1 | **AS（=predictor）** |
| active_treatment | continued_surveillance | 6 | CS 5 / AS 1 | **CS（=predictor）** |
| continued_surveillance | active_surveillance | 5 | AS 3 / AT 2 | **AS（=predictor）** |
| active_surveillance | active_treatment | 3 | AT 2 / AS 1 | **AT（=predictor）** |
| watchful_waiting | active_treatment | 2 | WW 1 / AT 1 | 平 |

**每一个分歧格上，GT 的多数类都恰好是 predictor 的预测。**
→ 因此任何只接收两个标签的裁决器，最优策略就是"永远取 predictor"，**增益恒为 0**。

### 2.2 分布诊断（重要的旁证）

| 来源 | AS | AT | CS | WW |
|---|---|---|---|---|
| **GT** | 25 | 31 | 14 | 2 |
| **predictor** | 25 | **33** | 14 | 0 |
| **LLM** | 11 | **46** | 13 | 2 |

- predictor 的类别分布**几乎等于 GT 分布**（差 2 例）。
- LLM 的 AT **严重过量**（46 vs GT 31）——它系统性地把病例推向"积极治疗"。
  这是 LLM 侧 0.597 的**主要错误形态**，也解释了为什么分歧例里它只对 3/28。

---

## 3. 结论

1. **一致率的前提被推翻**：此前引用 `提升手段全览` L133 的"60 例里仅 1 例被改判 → 98% 一致"
   （09-12 口径）不成立于当前 T2。实测 **61.1%**。所以"两路高度相关 → 裁决层没原料"这个
   **论证前提是错的**（多样性其实是够的：28 例分歧）。
2. **但结论方向不变，理由换了**：多样性有，可是**质量不对等**——分歧例里 predictor 对 78.6%、
   LLM 对 10.7%。所以"两个标签二选一"的信息量为 **0**（已在 §2.1 证明）。
3. **天花板 +4.17pp 是真的，但它是 oracle**：要靠它，裁决层必须能**识别出那 3 例 LLM 对的**
   （T2-011 / T2-031 / T2-110），同时不把 22 例 predictor 对的翻掉。仅凭两个标签做不到。
4. **裁决层要赚钱，必须喂证据而不是结论**：类别概率、规则引擎命中、指南阈值、量化工具值——
   也就是"predictor 和 LLM 都没直接用的那路信息"。这与文献一致（ADAgent 靠 5 个异构骨干取胜）。
5. **附带发现（影响 P2 的解法）**：`_predictor_note`（[form_fill.py L204-205]）**已经把 predictor
   的决策注入 LLM prompt**，且无条件生效。实测它**没能买到一致性**（仍分歧 39%），但
   **抬高了 LLM 的分数**（0.597 是"带提示"成绩，无提示只会更低）。
   → 这意味着：想公平测"两路独立判断"，必须做一次**不带 `_predictor_note` 的 A/B 重放**。

---

## 4. 建议的下一步（按 ROI）

| # | 动作 | 成本 | 收益 |
|---|---|---|---|
| 1 | **不做**"只给两个标签"的裁决层 | 0 | 已证 +0.0000 |
| 2 | `_predictor_note` A/B 重放（关掉锚定），测 LLM 真实独立水平与真实分歧率 | 一次 T2 全量（~3h） | 决定"决策-论述顺序反转"方案是否成立 |
| 3 | 若要做裁决层，输入改为**证据表**（predictor 概率 + 规则命中 + 指南阈值） | 设计+开发 | 唯一有可能吃到那 +4.17pp 的路径 |
| 4 | **顺序反转**：先由裁决层出决策 → 再让 LLM 写论述与权重 | 中 | 直接解决决策-论述错位（P2），且不需要"塞答案" |

---

## 5. 复现

```bash
cd ~/CHIMERA-agent
~/miniconda3/envs/chimera/bin/python tools/t2_arbiter_headroom.py
```

---

## 6. 变更记录

- 2026-09-27：创建。用 T2 全量（72 例）实测两路一致率 61.1%、oracle 上限 +4.17pp，
  并证明"标签级裁决器增益 = 0"。同时纠正此前引用的"一致率 98%"。