# P0 修复报告：跨折泄漏 + 分折门升级 + tool 分量口径（2026-09-28）

> 承接 [p9_overnight_results_20260928.md] 的"下一步建议 ①②③"。
> 全部改动均有备份（`.bak_p0` / `.bak_p0b`），默认行为保持兼容。

---

## 0. 结论速览

| # | 项目 | 结果 |
|---|---|---|
| 1 | **变体跨折泄漏（T1/T2）** | 已修。泄漏配置 CV **0.764 → 0.695**；默认路径 **no-op**（0.848 不变） |
| 2 | **T3 评估折污染** | 已修。**3 处**（Cox 主路径 / sklearn fallback / MLP 对比），残留 0 处 |
| 3 | **门的 G2** | 升级为**机器判定**；且**它揪出了我第一版漏改的 2 处**。现 **EXIT=0 通过** |
| 4 | **reflect 节点** | 已关闭（`self_refine: false`），注释写明 **"+7.4pp" 的真实出处 = N=30 净 2 例** |
| 5 | **tool 分量口径** | ★ **推翻长期误解**：T1 真值 ≈ **0.633**（不是 0）；T2 **本地不可测 → 报 N/A** |
| 6 | **重训 bundle** | **不需要**（默认无 flag 时新逻辑退化为原行为） |

---

## 1. 泄漏 1（T1/T2）：扩充行无差别进每个训练折

**机制**：GT 变体由父 case 的 `structured_prompt` 派生、沿用父的 `decision`（父子同标签）。
旧代码把**全部**扩充行塞进**每一个**训练折：

```python
tr = np.concatenate([gt_idx[tr_gt], pseudo_idx]) if pseudo_idx.size else gt_idx[tr_gt]
#                             ^^^^^^^^^^ 全量，不看去重来源
```

→ 父 case 落在**评估折**时，其子行把父标签泄漏进训练折。

**修法**：以「来源分组键」为准，把父子/同源行锁在同一折：

| 来源 | 分组键 |
|---|---|
| GT / 共识伪标 / 旧伪标 | 自身 `case_id`（无派生关系） |
| **GT 变体**（`syn_<variant>`） | **`original_case_id`** ← 父子必须同折 |
| 灰区合成（`syn_gz_*`，凭空合成） | 无键 → `__nogroup__:` 标记 → **不进训练集**（按项目纪律） |

新增 `_row_groups()` + `_train_fold_with_extras()`；每折日志会打印剔除数量。

**验证**：

| 配置 | 修复前（泄漏） | 修复后（诚实） |
|---|---|---|
| T2 GT-only | 0.848 | **0.848**（no-op ✓） |
| T2 `--include-all --knn-features` | **0.764** | **0.695** |
| T3 GT-only | 0.8684 | **0.8684**（no-op ✓） |
| T3 `--include-all` | — | **0.8614** |

→ 泄漏此前给 CV 贡献了约 **+0.069** 的虚高。
→ 修复后"扩充数据有害"的结论更干净：**0.695 vs 0.848 = −0.153**。

---

## 2. 泄漏 2（T3）：评估折含扩充行

```python
for tr, te in kf.split(raw_num):     # ← 对**全行**分折，伪标/变体会落进评估折
```
→ 改为 `kf.split(raw_num[gt_idx3])` + `_train_fold_with_extras`。

**共 3 处**：Cox 主路径、sklearn **fallback** 路径、**MLP 对比**路径。
（后两处是**门 G2 揪出来的**——我第一版只改了主路径。）

---

## 3. 门 G2 升级：从"须人工确认"到机器判定

原 G2 输出：
```
⚠ GT 变体（父 GT 派生） n=607 分组键 607 —— **有键但须确认分折实现真的用了它**
```
—— 把最关键的"实现是否真的消费了键"留给人工，等于没检。

新增两个静态检查：
- `_split_impl_uses_groups()`：检出旧写法（全行分折 / 全量 concat）→ FAIL；检出分组消费 → PASS
- `_split_impl_excludes_nogroup()`：无来源键的数据是否被实现**真的排除**

**旧写法识别用正则精确匹配代码形态**（`kf\.split\(raw_num\)\s*:` 等），
避免被注释里的引用误伤（第一版就被误伤过）。

现输出：
```
✓ GT 变体（父 GT 派生） n=607 分组键 607 | ✓ 分折实现已消费分组键 ['_train_fold_with_extras', '_row_groups', 'raw_num[gt_idx3]']
✓ T1 grayzone（凭空合成） n=80 分组键 0 个（答不出分折键）→ 实现已按纪律**排除**此来源 ✓
```
**G2 EXIT=0** ✓（此前永久 ✗ —— 无法消除的噪声正是假报警温床）

---

## 4. reflect 节点关闭 + "+7.4pp" 的真实出处

`configs/config.yaml` 原注释：

> ENABLED — S2 ablation (2026-08-31, **N=30**) confirmed positive:
> accuracy **+7.4pp** (66.7%→74.1%), **fixed 3 / broke 1**, refine triggered 9/30

★ **"fixed 3 / broke 1" 在 n=30 上就是净 +2 例** —— 这正是"小样本噪声当提升"的教科书案例，
且此后从未在大样本复核，直到 P9 Arm A（n=71，配对 Δ/SE=−0.33，胜/负 33/33）**证否**。

已改为 `self_refine: false`，并把上述依据写进注释（不是静默翻开关）。
复现实验：`CHIMERA_FORMFILL_SELF_REFINE=1`。

---

## 5. ★ tool 分量口径修复（推翻长期误解）

**问题**：官方 `_reveal_keys()` **只接受 dict 项**（取 `item["key"]`），字符串项被直接跳过。
而本地 GT 的 `reveal_sequence` 形态因任务而异：

| 任务 | 本地 GT `reveal_sequence` | 后果 |
|---|---|---|
| **T1** | `["radiology_report", "psa_trend", ...]`（字符串，4 种取值） | `expected` 恒空 → tool **恒为 0** |
| **T2** | `[]`（空） | 同上，且**本地不可测** |

→ 此前所有报告里的 `tool = 0.0000` 是**度量假象**，不是"agent 工具选择差"。
（tool 权重 **0.15**，等于 case_score 的 15% 长期被误读为 0。）

**修复**：本地 GT 的 4 个字符串名与 pred 的 section key 存在**干净 1:1**（91 例 / 285 条全量比对）：

| 本地 GT 字符串 | 官方 key |
|---|---|
| `radiology_report` | `section_s3-mri` |
| `psa_trend` | `section_s3-psa` |
| `laboratory_results` | `section_s3-labs` |
| `previous_notes` | `section_s3-prev` |

新增 `tools/local_gt_reveal.py`：可映射则映射；**不可映射一律返回 `None` → 调用方必须报 N/A，不得报 0**。

**验证（`tools/tool_score_local.py`）**：

| 批次 | 原始（假象） | **修复后真值** | extra 工具均值 |
|---|---|---|---|
| T1 基线 note ON | 0.0000 | **0.6332** | 1.73 |
| T1 C 关锚定 | 0.0000 | **0.6013** | 1.94 |
| T2 基线 | 0.0000 | **N/A**（GT 无该字段） | — |

**两个新结论**：
1. **T1 的 tool 分其实是 0.633**，不是 0 —— 此前对 T1 case_score 的绝对水平判断是错的。
2. **关锚定反而使工具使用变差**（0.6332→0.6013，extra 1.73→1.94）
   → 结合 P9 的决策 +1 例，**T1 关锚定净值为负**（−0.032×0.15 ≈ −0.005，决策 +1 例不显著）。

---

## 6. 为什么不重训 bundle

`--include-pseudo / --include-all / --include-pseudo-v2` **均默认 false**。

→ 默认路径下 `pseudo_idx` 为空，`_train_fold_with_extras()` 直接返回 GT 训练折 →
**已部署的 T2 RF / T3 Cox bundle 完全不受本次修复影响**（已用 CV 0.848 / 0.8684 逐位复核）。

本修复影响的是**用法**：今后任何"扩充数据有没有用"的实验，CV 不再虚高。

---

## 7. 复现

```bash
cd ~/CHIMERA-agent
PY=~/miniconda3/envs/chimera/bin/python

# 泄漏修复验证（默认 no-op vs 泄漏配置）
$PY scripts/train_predictor.py --task 2                                  # 0.848（不变）
$PY scripts/train_predictor.py --task 2 --include-all --knn-features     # 0.695（原 0.764）

# 门
$PY tools/connectivity_gate.py groups data --src src                     # EXIT=0

# tool 口径
$PY tools/tool_score_local.py
```

---

## 8. 变更记录

- 2026-09-28：创建。
  ① 修 T1/T2 变体跨折泄漏（来源分组 + 训练折过滤）；② 修 T3 三处全行分折；
  ③ 门 G2 升级为机器判定（并揪出 ①漏改的 2 处）；④ 关闭 reflect 节点并写明
  "+7.4pp 实为 N=30 净 2 例"；⑤ 修 tool 分量口径（T1 真值 0.633 / T2 报 N/A），
  推翻"tool=0"的长期误解；⑥ 确认无需重训 bundle。