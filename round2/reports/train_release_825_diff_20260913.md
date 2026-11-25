# 官方 8.25 训练数据 vs 本地旧版 · 逐字段对比

> 生成：2026-09-13 · 输入：用户下载的官方发布包 `train_release_updated_25_08.zip`（8,677,177 B）
> 对比对象：本地 `chimera-agent-baseline/train_release/`（旧版）
> 方法：**全量逐 case、逐字段 diff**（非抽样），并核对真值 reasoning 口径

---

## 一句话

**只删字段、不改值**：T1 全部 195 例删掉 14 个"活检病理"泄漏字段；T2 全部 153 例只删 `id`；**T3 零差异**。所有保留字段的值**逐字节未变**。

---

## 1. 体量与文件级差异

| task | 案例数（新=旧） | 有差异的文件 | 差异覆盖 |
|---|---|---|---|
| T1 | 195 = 195 | **仅 `structured-prompt.json`** | **195/195 例** |
| T2 | 153 = 153 | **仅 `structured-prompt.json`** | **153/153 例** |
| T3 | 75 = 75 | **无** | **0** |

> `clinical-data` / `modality-level-neural-representations` / `-reasoning` / `-decision`
> 四类文件**全部逐字节相同**。

---

## 2. T1：删除 14 个字段（195/195 例，无例外）

| 字段 | 性质 |
|---|---|
| `bx_isup` | 活检 ISUP 分级 |
| `bx_gl_prim` / `bx_gl_sec` / `bx_gl_tert` | 活检 Gleason 主/次/三级 |
| `bx_isup_pred` | 预测的 ISUP |
| `cores_positive` / `cores_total` / `max_core_pct` | 穿刺阳性针数 / 总针数 / 最大占比 |
| `growth_pattern` / `high_risk_patterns` | 生长方式 / 高危模式 |
| `pni` / `lvi` | 周围神经侵犯 / 淋巴血管侵犯 |
| `tumor_location` | 肿瘤位置 |
| `id` | 病例内部 id |

对照官方页面 **Version 3 updates** 原文：
> "Task 1: Biopsy- and pathology-derived fields (`bx_isup`, `bx_gl_prim`, `bx_gl_sec`, `bx_gl_tert`,
> `bx_isup_pred`, `cores_positive`, `cores_total`, `max_core_pct`, `growth_pattern`,
> `high_risk_patterns`, `pni`, `lvi`, and `tumor_location`) were removed from `structured-prompt.json`
> **to avoid data leakage into the biopsy-decision**"

⇒ **与官方说明逐字对应**，是**有意的防泄漏清理**。

⚠️ **具体案例证据**（`PT-pseudo_0020cfca66c8`）：被删字段在旧版里**确实带着答案**
```
bx_isup = 1        bx_gl_prim = 3      bx_gl_sec = 3
```
即旧版 prompt 直接把"活检结果"给了模型，而 T1 问的正是"要不要做活检"——**这是真泄漏**。

---

## 3. T2：只删 `id`（153/153 例）

| 字段 | 变化 |
|---|---|
| `id` | 删除（`pseudo_xxxx` 形式的内部标识） |
| 其余 40 键 | **保留且值不变** |

⇒ T2 的临床字段（含 `bx_isup` / `bx_gl_prim` / `bx_gl_sec` / `ct` 等）**全部保留**——
符合任务语义（T2 是**活检之后**的治疗决策，需要病理信息）。

---

## 4. 值是否被改动：**没有**

```
T1: 同键但值不同 = 无
T2: 同键但值不同 = 无
T3: 同键但值不同 = 无
```
⇒ 这是一次**纯删字段**的更新，不是数据修订。

---

## 5. 对我们要紧的三件事（已核）

### ① T1 的关键变量 `bx` 仍在
```
新版 T1 prompt 含 bx = "Positive"
```
⇒ 真值 reasoning 的 10 个权重键里，**只有 `fh` / `comorbidity` 不在 prompt 里**
（这两个本来就由 MCP 工具 `get_family_history` 提供，属设计如此，非本次变化）。

### ② 真值 reasoning 口径未变
新旧两版的 `prostate-biopsy-decision-reasoning.json` **完全相同**，
权重键都是那 10 个 ⇒ **删掉的字段本来就不在评分口径里，无 schema 缺口**。
⇒ **我们的对齐层无需改代码**（T1 固定表恰是这 10 键）。

### ③ 代码不依赖被删字段（静态核查）
| 检查 | 结论 |
|---|---|
| `bx_isup` / `bx_gleason_*` 从哪来 | `predictor.py:392-410` 对 **`pathology_report` 文本正则解析**，非 prompt ⇒ 安全 |
| `prompt["id"]` | 发货代码中**无读取** ⇒ 安全 |
| `atomic_fact_check` 对 `bx_isup/bx_gl_prim/bx_gl_sec` 有规则 | `form_fill.py:804-815`、`fact_check.py:160` ⇒ **T1 少这些字段可能改变 atomic 告警与置信度校准** ⇒ **必须实测** |

---

## 6. 实测状态

已把新数据合并为 `~/data_v825` 并在**容器路径**（真 `inference.py` + 官方分量打分）跑通：

```
新 T2-001                case=0.6955  dec=OK  confidence=1.0000  grounding=1.0000
新 PT-pseudo_016946...   case=0.7692  dec=OK  confidence=1.0000  grounding=1.0000
```
⇒ 适配器 / 特征提取 / 工具装载 / `atomic_fact_check` **均未报错**；对齐层在新 prompt 上仍正常写出 10/11 键。

⚠️ 样本 2 例，建议扩到 T1/T2 各 10 例再定论（尤其看 `atomic_check` 告警率是否变化）。

---

## 7. 结论

1. **8.25 版是官方有意的防泄漏清理**，与页面 Version 3 说明逐字吻合；
2. **只删字段、值未改**，T3 完全未动；
3. **对评分口径无影响**（真值 reasoning 未变），**对齐层无需改动**；
4. 唯一需实测确认的是 **T1 的 `atomic_check` 行为**（因它对这些被删字段有规则）；
5. **建议把训练/评测一律切到 8.25 版**——旧版让 T1 白拿病理答案，会让本地 T1 指标虚高，
   与平台真实表现（acc 0.840）不可比。
