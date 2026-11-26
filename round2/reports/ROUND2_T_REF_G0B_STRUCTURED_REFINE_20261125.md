# T-REF-G0B Structured Refine 与确定性 Producer 实现报告

2026-11-25 · 分支 `refl-g0b` · worktree `/home/hedongze/CHIMERA-agent-refl`（基线 `606b6df`）
真实 35B 生成：0 次。本轮全部为零模型调用的工程实现与正负对照。

## 1 交付物

| 文件 | 作用 |
| --- | --- |
| `src/chimera_agent_baseline/agent/reflection_patch.py` | 补丁 Schema、严格解析、确定性构造、独立工程审核、契约桥接、Structured Refine prompt |
| `tests/test_reflection_patch_g0b.py` | 15 项正负对照，对应任务卡 §6.1 八场景 + 4 项附加守卫 |

## 2 Schema 设计依据（H04，34 例 T2 可信轨迹）

样本口径：可信 58 例中 34 例首轮真正报了 issues 并发生 Refine，24 例首轮收敛。`refined_full.txt` 与输入长度明显不同的正好 34 例，与 issues 分布完全吻合。

| 观测 | 数值 | 对 Schema 的含义 |
| --- | --- | --- |
| issues 总数 | 72，平均 2.1/例 | 单例补丁数不宜设上限过紧，2–3 条是常态 |
| issues 已带类型前缀 | `fabricated_facts:` / `internal_inconsistency:` / `under_treatment:` / `incomplete_rationale:` | `error_type` 直接复用模型自发产生的分类，不另造词表 |
| 含具体数字的 issues | 61/72 | `evidence_quote` 必须逐字，否则数值类修正无法核验 |
| 字段提及次数 | PSA 42、ISUP 14、PSAD 11；`primary` 仅 1、`active_treatment` 5 | **错误集中在证据与理由层，不在决策字段** |
| Refine 输出形态 | 整段重写（21 段→10 段，43 段→8 段） | `/free_text` 走全量替换，不做增量 diff |
| `severity` | 34 例全为 `None` | 历史版本 critique 未写 severity，与当前 prompt 不一致（版本差异新证据） |

结论：Producer 的路径域必须以 `/free_text` 为主，决策字段为辅。若按"模型会改决策"来设计，会与真实分布错配。

## 3 Schema

每任务允许的路径（越界即拒）：

| 任务 | 允许路径 |
| --- | --- |
| T1 | `/biopsy_decision`、`/confidence`、`/variable_weights/<key>`、`/free_text` |
| T2 | `/treatment_recommendation/primary`、`/confidence`、`/variable_weights/<key>`、`/free_text` |
| T3 | `/event`、`/months_to_recurrence`、`/free_text` |

`variable_weights` 用前缀白名单，键集由平台常量决定，不在 Schema 里硬编码。

补丁字段：`path`、`before`、`after`、`error_type`、`evidence_quote`、`reason`。
错误类型复用模型自发的 7 类，外加 `other` 兜底。

强制约束：
1. `before` 与 J0 当前值 canonical 全等，否则 `before_mismatch`。
2. 类型严格一致，禁止隐式强转（`18.0 → 24` 视为类型错，不自动转 float）。
3. `after` 为 null 仅允许 `NULLABLE_PATHS`（T3 months）。
4. `evidence_quote` 必须逐字出现在 upfront 记录区，否则拒。
5. 决策字段变更必须同批带 `/free_text`，否则 `cross_field_violation`。
6. T3：`event` 翻转必须联动 `months`，且 `event=0 → months=null`、`event=1 → months>0`；`event` 仍为 1 时单独调 months 属合法。

## 4 校验链与拒绝码

14 个拒绝码：`malformed_patchset`、`stale_base`、`no_patches`、`field_not_allowed`、`path_missing`、`before_mismatch`、`type_mismatch`、`enum_violation`、`evidence_missing`、`evidence_not_in_record`、`unknown_error_type`、`duplicate_path`、`no_change`、`cross_field_violation`。

一律整体拒绝，不做部分应用——避免"三条里两条生效"这种不可审计的中间态。

## 5 Bound Review 的绑定方式

- `rule_based_review()` 独立于 `apply_patches()` 重新执行不变量检查（字段权限、证据可定位、确有变化），不复用 Producer 的中间结论。
- 返回的 `method` 固定为 `rule_based_independent`，**如实标明这只是工程级审核**。
- 医学正确性审核不由本模块承担。Producer 不得给自己的候选出通过声明；生产路径的审核者须独立实现，且 `AuditVerdict.reviewed_fields` 必须覆盖候选全部结构字段（契约 `reflection_contract.py:230-231`）。
- 桥接 `to_correction_proposal()` 输出契约要求的 `CorrectionProposal`，`citations.record_id` 固定 `rendered_upfront`，`quote` 逐字来自 upfront。

## 6 正负对照结果

`pytest tests/test_reflection_patch_g0b.py` → **15 passed**。

| # | 场景 | 结果 |
| --- | --- | --- |
| 1 | 合法修改构造完整候选，未提及字段保原值 | 通过 |
| 2 | 空补丁集不生成候选 | 通过（`no_patches`） |
| 3 | 含糊/缺字段补丁不可转换 | 通过（`malformed_patchset`） |
| 4 | 未授权字段 | 通过（`field_not_allowed`） |
| 5 | 引用未获取证据 | 通过（`evidence_not_in_record`） |
| 6 | before 不符 / base 摘要过期 | 通过（`before_mismatch` / `stale_base`） |
| 7 | 合法候选通过独立审核且 method 不冒充医学 | 通过 |
| 8 | 决策改但理由未同步 | 通过（`cross_field_violation`） |
| 9 | 权重字段可补丁 | 通过 |
| 10 | 枚举越界 | 通过（`enum_violation`） |
| 11 | 数值不隐式强转 | 通过（`type_mismatch`） |
| 12 | 重复路径 / 空改动 | 通过 |
| 13 | T3 event 与 months 联动（含置空） | 通过 |
| 14 | 候选可包装成 CorrectionProposal 且满足 resolve() 字段级不变量 | 通过 |
| 15 | Structured Refine prompt 覆盖三任务路径与空集合约定 | 通过 |

开发中由测试暴露的两处设计错误已修：`variable_weights` 漏进白名单；`months` 置 null 被类型守卫误拦。若只写正向用例，这两处都会带进生产。

## 7 剩余阻塞项

1. **未接入 `self_refine.py`**：当前是独立模块，尚未替换原有 `refine_user` 调用链，也未把补丁集落到 trace。接入前需确认不影响 `run_self_refine` 的历史行为。
2. **无 LLM 级 Bound Review**：目前只有工程级 reviewer。医学审核者需单独实现并经 C2 验证。
3. **C1 无法用历史文本验证 B**：历史 refined 是 prose，无补丁。C1 只能验确定性构造与契约（已由 15 项覆盖），新 prompt 的效力必须由 C2 的 3 例真实调用证明。
4. **`tests/` 未被 git 跟踪**：主仓 `git ls-files tests/` 为空，整个测试集只存在于工作区。这是 151 项无法绑定版本的根本原因之一。本报告的两个文件已提交到 `refl-g0b` 分支。
5. **C2 授权未动**：仍是 3 例 / ≤12 次，须 B1+G0-A/B+C1+仲裁全通过后才可执行。

## 8 两个审计副产物

- 历史 critique 的 `severity` 全为 `None`，与当前 `_CRITIQUE_SYSTEM` 要求的 `none|mild|moderate|severe` 不一致，再次印证历史 trace 属旧版本。
- `tests/` 全目录 untracked，测试与代码版本之间没有绑定关系。

---

## 9 H04 取样口径更正

三个数字容易被读成同一件事，实际各自独立：

| 集合 | 数量 | 定义 |
| --- | --- | --- |
| 可信轨迹 | 58 | 目录单日期 + critique 文件同一运行窗口（≤2h） |
| 首轮 EMPTY 收敛 | 58 | 首轮 `parsed_issues` 为空即退出 |
| 实际发生 Refine | 34 | 首轮报了 issues 并进入 refine |

- 「可信 58」与「首轮 EMPTY 58」**是不同集合**，数量相同属巧合。可信集内部 34 例发生 Refine、24 例收敛。
- 34 例的 `refined_full.txt` 与输入长度明显不同，正好 34 例，与 issues 分布一致——这是唯一一次独立交叉验证。
- **字段提及次数不等于修改次数，也不等于医学错误次数**。PSA 42 次是 issues 文本中出现的次数，同一 issue 可提及多个字段，且未与 GT 核对。

**外推限制**：这批数据全部来自 2026-09 旧版本、72 例为 T2、91 例为伪病例，其中没有一个真实 T3 病例。不得用于推断真实 T3 的检测或修改能力。

## 10 C2 调用预算重算

单例完整两轮 + 医学审核：

| 环节 | 调用 |
| --- | --- |
| Critique 1 | 1 |
| Structured Refine 1 | 1 |
| Critique 2 | 1 |
| Structured Refine 2 | 1 |
| Medical Bound Review | 1 |
| 合计 | **5** |

3 例 = 15 次，**超出原定 12 次上限**。不含初始判断、Form Fill、异常重试。

建议配置（二选一，均不自行启动）：

| 配置 | 单例 | 3 例合计 | 余量 | 说明 |
| --- | --- | --- | --- | --- |
| 单轮 + 医学审核 | 3 | 9 | 3 次重试 | **C2 开发验证配置**，只验新 Prompt→Producer→Review→Consumer→Calibration 能否工作 |
| 两轮 + 医学审核 | 5 | 15 | -3 | 超出上限，需单独申请 |

单轮配置不得在未经记录的情况下成为正式实验配置；两轮的真实效果留给 G1/G2。

若医学审核最终未实现（当前 `UnavailableMedicalReviewer` 返回 `not_run`），单例降为 2 次，3 例 6 次。

## 11 接入与测试（本轮新增）

| 项 | 状态 |
| --- | --- |
| Structured Refine 以 opt-in 方式进入 `self_refine.py` | 完成，`CHIMERA_STRUCTURED_REFINE` 默认 0 |
| `run_self_refine` 历史路径 | 未改动，返回值与行为保持原样 |
| 多轮状态 J0→J1→J2→相对 J0 收敛 | 完成，`collapse_rounds` 不拼接补丁 |
| 医学 Bound Review | 接口留位，`UnavailableMedicalReviewer` 返回 `not_run`，下游不得当作 accepted |
| 连带拒绝计量 | 完成，`Outcome.collateral_rejected` + 逐字段 `field_report` |
| 测试总数 | **33 项全通过**（模块 25 + 集成 8），全部零真实调用 |

trace 产物命名与旧路径分离：`critique_iter{n}.json`、`patch_iter{n}.json`、`apply_iter{n}.json`、`run_manifest.json`。refine 的输入与输出不再共用文件名。

### 本轮由测试暴露的四处设计错误

1. `variable_weights` 漏进允许路径白名单。
2. T3 `months` 置 null 被当作合法——官方语义是取末次随访时长（`prompts.py:202-203`），null 会被 `postprocess.py:74` 修补成 36.0。
3. `months` 被归入决策字段，导致单独调整时间也强制重写理由。
4. `changed_fields` 粒度错配：契约要求顶层键（`reflection_contract.py:205`），本模块内部用 JSON pointer，跨边界未降级映射导致端到端 `binding_or_authority_rejected`。

前三条属业务规则错误，第四条只会在真正跑 `resolve()` 时暴露——只测模块内部接口发现不了。

---

## 12 生产入口的真实拓扑（本轮关键发现）

**J0 在 reflect 节点运行时并不存在。**

- `graph.py` 的边是 `reflect → form_fill`（`builder.add_edge("reflect", ... "form_fill")`），reflect 在 Form Fill 之前。
- J0 由 form_fill 解析产生：`form_fill.py:320` 校验、`:410` 记 `parsed_judgment`。
- 因此若把 Structured Refine 接在 reflect 节点，要么拿不到 J0，要么得再调一次 Form Fill——后者等于隐式增加一次模型调用，且改变实验流程。

正确入口是 `form_fill.py:479-488` 的 `authority.review(context)`：此处 J0（`context.initial_json`）、upfront 证据、`subject` 全部就绪，且下游 `resolve()` 已在消费 `ReviewBatch`。这就是 G1R 已有的钩子，只是把 `SyntheticReview` 换成生产实现。

**结论：`graph.py` 与 `form_fill.py` 一行未改**（`git diff --stat` 为空），生产入口通过替换 authority 实现接通，没有新增调用点。

## 13 三个阻塞项的完成情况

### G0-B1 生产入口
`reflection_authority.py` 的 `StructuredReflectionAuthority.review(context)` 走真实契约签名。链路：Critique → Structured Refine → Producer → 工程检查 → 医学审核 → `ReviewBatch` → `resolve()`。异常一律降级为 `ReviewBatch('error')`，下游回退保留 J0。

### G0-B2 最小医学审核
同模型独立调用，四问（事实一致性 / 修改必要性 / 证据支撑 / 整体一致），输出 `accept|reject|uncertain`。解析失败、非法取值、未审核一律按 reject 处理。`method` 记为 `structured_refine_independent_llm_review`，测试断言其不含 "expert" 字样——不冒充独立医学专家。

### G0-B3 仲裁冻结
`configs/reflection_arbitration_v1.json`：`reflection_priority=false`、`override_recording=required`，加载时强制校验，违反即抛错。`arbitration.py` 只做机械分类（保留 / 授权覆盖 / 未解释覆盖 / 丢失 / 内部字段），**不判断覆盖的医学对错**，留给 G2。

同时登记两处已知事实：S12 的 `t3_event_calibration` 覆盖属预期；`months=36.0` 是 `postprocess.py` 的默认回退，不等于有证据支持的预测，报告须区分。

## 14 测试与回归

| 组 | 数量 | 内容 |
| --- | --- | --- |
| 模块（Patch/Producer） | 25 | 八场景 + 多轮 + 三层有效性 + 契约不变量 |
| 集成（桩模型驱动 self_refine） | 8 | 开关默认关、历史路径、多轮、trace 产物、拒绝路径 |
| 生产入口（authority.review） | 11 | 真实契约入口 → `resolve()` 全链路 |
| 仲裁冻结 | 5 | 策略校验与覆盖分类 |
| 合计 | **49** | 全部零真实调用 |

历史路径回归：`test_legacy_path_untouched` 断言 `run_self_refine` 仍返回 `(text, warnings)` 且不被补丁逻辑污染；`graph.py` / `form_fill.py` / `reflection_contract.py` 的 `git diff` 为空。

## 15 C2 执行清单

`configs/c2_execution_manifest_v1.json`：单轮配置，3 例，基础 9 次 + 新 J0 预留 1 次 + 重试预留 2 次 = 12 次硬上限。三个槽位用途已定，病例 ID 与 J0 可复用性标记 TBD，属执行时核实项。已知可复用资产仅 T3 试点 2 例。**未启动。**

## 16 状态

`INTEGRATION_PASS / REAL_VALIDATION_PENDING`

- 达成：生产入口可达、异常不默认通过、仲裁冻结、49 项零生成测试。
- 未达成：真实模型下的审核效果、T3 病例的 J0 复用性、C2 未执行。
