# 外接 API 伪数据的诚实评估（2026-09-14）

> 触发：用户提出"再次考虑怎么用上之前使用外接 API 生成的假数据"。
> 结论：**唯一可发货的用法（predictor 扩训）在诚实 CV 下只有噪声级、且随 head 反转的收益 ⇒ 不建议使用。**
> 过程中发现并修掉一个**会让结论反过来的 CV 协议 bug**。

---

## 0. 一句话

之前看起来"伪数据把 T2 CV 从 0.627 拉到 0.757（+0.13）"的结果，是**评估折里混进了伪标签行**造成的假象。
修好协议后，真实收益是 **+0.022 Macro-F1（< 折间 σ=0.11）**，且 HistGB 涨、MLP/LR 跌 —— **噪声，不是信号**。

---

## 1. 资产盘点（服务器 `/home/hedongze/CHIMERA-agent/data/`）

| 文件 | task1 | task2 | task3 | 说明 |
|---|---|---|---|---|
| `pseudo_labels.json` | 104 | 68 | 186 | 外接 API 单模型伪标签 |
| `pseudo_labels_v2.json` | 45 | 80 | 187 | G5 反思式伪标签 |
| `pseudo_labels_consensus.json` | 104 | 74 | 2 | 多教师共识伪标签 |
| `pseudo_labels_glm_checkpoint.json` | — | — | — | 中途检查点 |
| `sft/train.jsonl` | 363 行 | — | — | 聊天格式 SFT 数据（仅 T1） |
| `synthetic/gt_variants.jsonl` | 217 | 172 | 218 | 真值病例的扰动变体（607 行） |
| `synthetic/t1_grayzone.jsonl` | 80 | — | — | T1 灰区合成样本 |

**两条决定性事实**：

1. **所有伪标签都落在没有真值的病例上**（与有真值的病例交集 = **0**，对三个文件、三个任务都成立）
   ⇒ **伪标签本身的准确率无法本地测量**，只能靠"加进训练折后在真值上做 CV"来间接验证。
2. **T3 的伪标签没有 `event` 字段**（186/187 条 `decision=None`）⇒ 对 T3 决策完全不可用。
3. 伪标签的 T2 决策分布**严重偏 AT**（50/68、37/80、54/74 ⇒ 74–84% 是 AT），
   而有真值的 T2 里 AT 只占 45% ⇒ 伪标签自带一个"多治"偏置。

---

## 2. 发现的 bug：CV 把伪标签行放进了**评估折**

`scripts/train_predictor.py::run_task1_2`（修复前）：

```python
rows, labels, ... is_gt = load_task_data(task, include_pseudo=..., include_all=..., ...)
y_idx = np.array([label_names.index(v) for v in y])     # y 含伪标签行
skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=42)
for tr, te in skf.split(raw_num, y_idx):                # 在"GT+伪"全集上切折
    ...
    accs.append(accuracy_score(y_idx[te], pred))        # te 里混着伪标签行
```

`is_gt` 在这个循环里**从未被使用**。于是 `--include-*` 的"CV"是在
**真值行 + LLM 自产标签行**的混合集上算的 —— 模型当然能"预测对"那些伪标签，
分数被系统性地抬高。

**识破它的指纹**：`多数类基线` 从 **0.431 → 0.553**。
多数类基线只取决于**评估集**的类别分布；加伪数据只该影响训练折。
它变了 ⇒ **评估集本身变了** ⇒ 就是泄漏。

---

## 3. 修复

```python
is_gt_arr = np.array(is_gt, dtype=bool)
gt_idx = np.where(is_gt_arr)[0]
pseudo_idx = np.where(~is_gt_arr)[0]
skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=42)
...
for tr_gt, te_gt in skf.split(raw_num[gt_idx], y_idx[gt_idx]):
    te = gt_idx[te_gt]
    tr = np.concatenate([gt_idx[tr_gt], pseudo_idx]) if pseudo_idx.size else gt_idx[tr_gt]
...
maj = np.bincount(y_idx[gt_idx]).argmax()
maj_acc = (y_idx[gt_idx] == maj).mean()
```

即：**折只在真值行上切；伪标签行只并入训练折；评估永远只看真值行。**

服务器：`scripts/train_predictor.py`（原文件备份为 `.bak_pseudo_cv`），本地副本 `tools/train_predictor_cvfix.py`。

---

## 4. 修复前后的 T2 CV 对比

修复后四次运行的 `多数类基线` 全部回到 **0.431**，证明评估集已固定。

| 配置 | LR Acc / F1 | MLP Acc / F1 | HistGB Acc / F1 | 最优 F1 |
|---|---|---|---|---|
| **baseline（无伪数据）** | 0.679 / 0.580 | 0.666 / **0.627** | 0.554 / 0.422 | **0.627** |
| `--include-pseudo` | 0.638 / 0.571 | 0.627 / 0.527 | 0.610 / 0.517 | 0.571 |
| `--include-all` | 0.610 / 0.545 | 0.627 / 0.526 | **0.679 / 0.649** | **0.649** |
| `--include-pseudo-v2` | 0.612 / 0.545 | 0.555 / 0.448 | 0.650 / 0.574 | 0.574 |

（修复前 `--include-all` 报的是 HistGB 0.815 / F1 0.757 —— 那个数字作废。）

**判读**：

- 最优 F1：0.627 → 0.649 ⇒ **+0.022**，而 HistGB 的折间 σ 本身是 **±0.113** ⇒ **远在噪声内**；
- 按**同一个 head** 比更清楚：MLP **0.627 → 0.526（变差）**、LR 0.580 → 0.545（变差）、
  只有 HistGB 0.422 → 0.649（暴涨）⇒ **收益随 head 反转**，是典型的不稳定/噪声特征，不是稳健信号；
- 项目既定判据：**CV 提升必须 ≥ 0.05 量级才值得考虑** ⇒ 本条不达标。

---

## 5. 结论与建议

**不要用这批伪数据重训 predictor。** 理由三层：

1. **可验证性**：伪标签全在无真值病例上，其质量不可测；唯一间接验证（真值 CV）显示收益在噪声内。
2. **偏置方向**：伪标签 74–84% 判 AT（真值 45%），会把"多治"偏置注入模型；
   而平台上的失败模式恰恰是 `AT→AS` 欠治疗 10–12 例 —— 方向看似"对症"，但**没有任何本地证据**支持它真的能救回来（且这正是"无本地证据的盲改"）。
3. **发货成本**：predictor 在 `/opt/ml/model/predictor/` 里，换它要**重传 25.8GB 的 `model.tar.gz`**
   （或改代码把 predictor 内置进镜像，再传 6.3GB）—— 为一个噪声级的改动付这个代价不合理。

**其它用法同样否决**：

| 用法 | 否决理由 |
|---|---|
| `sft/train.jsonl` 微调 35B | 要把 LoRA 合进 22GB gguf 重新上传；T1 已是 0.840 最好项；且不可本地验证 |
| `synthetic/gt_variants.jsonl` 扩训 predictor | 变体只有 `structured_prompt`，缺 `radiology_report`/`pathology_report` ⇒ 最强特征（`path_isup` AUC 0.862、`pirads_max` 0.721）建不出来；且标签同样不可验证 |
| 把合成样本注入 few-shot / RAG | 改 prompt 影响全部 task，属盲改；本地只能在同分布上验证，证不了平台收益 |
| T3 伪标签 | 无 `event` 字段，不可用 |

---

## 6. 复现命令

```bash
ssh hedongze-server 'cd ~/CHIMERA-agent && ~/miniconda3/envs/chimera/bin/python scripts/train_predictor.py --task 2'
# 依次加 --include-pseudo / --include-all / --include-pseudo-v2
# 判据：看「多数类基线」是否为 0.431（是=评估集干净）；再看最优 Macro-F1 是否 ≥ 0.677（0.627+0.05）
```

> ⚠️ 注意：`scripts/train_predictor.py` 不带 `--save` 才是纯评估；带 `--save` 会用全量数据重拟合
> 并**覆盖** `model/predictor/` 里的发货 bundle。
