# predictor ON vs OFF —— 本地配对对照（2026-09-14）

> 触发：用户要求"最快的双卡验证判断 predictor 作用实验"。
> 结论：**predictor 是强正资产，不是拖累；去掉它 T2 决策准确率从 0.826 掉到 0.652（−17.4 pt，净 −12 例）。**
> 之前"predictor 是否拉低 LLM 决策正确率"的怀疑，在本地上被**决定性否证**。

---

## 0. 一句话

```text
predictor ON  (v12 argmax)  acc = 57/69 = 0.8261
predictor OFF (纯 LLM)      acc = 45/69 = 0.6522
                            delta = −0.1739   （净 −12 例）
```

---

## 1. 实验设计

- **病例**：70 例带标签 T2（与 predictor 的历史数字同批），双卡各 35 例，72 例请求中 **69 例成功配对**（A 臂 34、B 臂 35）。
- **ON 臂**：已有基线（提交配置，predictor override ON）——无需重跑，直接取自本地复刻与快照。
- **OFF 臂**：`configs/config.nopredictor.yaml` ⇒ `enabled:false` + `decision_override:false` + `decision_override_tasks:[]`，
  即**既没有提示词锚定、也没有覆盖**，最终决策完全由 LLM 自己给出。
- **硬件**：GPU1/8766 与 GPU0/8767 各起一份**相同 35B gguf**（避免模型差异混淆）。
- **耗时**：10:46:01 → 13:34:17（2h48m，~2.3 min/例两卡合计）。

## 2. 结果

```text
ON  对 → OFF 错 : 17 例
   T2-008 009 010 012 020 031 032 035 042 044 046 049 056 059 060 104 108
ON  错 → OFF 对 :  5 例
   T2-017 018 061 106 110

各分类召回率（真值）        ON        OFF
  active_surveillance      19/23     12/23      （−7）
  active_treatment         22/30     24/30      （+2）
  continued_surveillance   14/14      9/14      （−5）
  watchful_waiting          2/2       0/2       （−2）
```

## 3. 判读

1. **predictor 必须保留。** 本地差距 17.4 pt（净 12 例）远超任何噪声量级，且方向单一。
2. **一个重要的反转**：纯 LLM 的 **AT 召回反而更高**（24/30 vs 22/30）——
   也就是说"predictor 在 AT 上偏保守"这个判断是对的；但 LLM 为了这个优势在
   AS/CS/WW 三类上崩掉了 14 例。**这正是平台 T2 失败模式的镜像**：
   predictor 的问题集中在 AT，而 LLM 的强项恰好也是 AT、代价是其它三类。
3. **对 v13 的意义**：v13 **保留了 predictor**，只在 AT 方向做了一个小修正
   （10 例翻转、7 救回 3 误伤、净 +4）。方向与这份数据一致（predictor 在 AT 上确实弱），
   而**幅度远小于"关掉 predictor"**——后者本地已经证明是灾难性的。
4. **不出 v14（predictor-off）**：该路线被本地数据否决。

## 4. 诚实边界

- 这是**同分布**结论（本地 train_release 分布）。平台是 OOD：predictor 的 AT 召回
  在平台上从 0.73 掉到 **0.31**、整体准确率 0.826 → 0.667。
- 但要让"纯 LLM 在 OOD 下反超"，predictor 的 OOD 退化必须比 LLM 多出 17 pt——而
  LLM 走的是同一条管线、面对同一份 OOD 输入，**没有任何证据支持这个假设**。
- 因此结论：**保留 predictor**；v13 的 AT 微调是这条路线内的合理补丁。

## 5. 复现

```bash
# 服务器上（双卡各 ~2.8h）
bash /tmp/predictor_off_full.sh
# 产物：/tmp/off_a.json /tmp/off_b.json；配对对照打印在 /tmp/off_main.log 末尾
```
