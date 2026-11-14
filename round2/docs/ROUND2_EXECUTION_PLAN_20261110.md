> 2026-11-13 排序更新：执行顺序以 [新总计划](ROUND2_MASTER_PLAN_20261113.md) 为准。本文保留历史设计与事实，不继续默认推进其中旧的下一步。

# CHIMERA-agent 第二轮优化执行计划

建立日期：2026-11-10。按用户提供的 11 月 15 日—12 月 15 日上传窗口排期；官网旧 timeline 尚未明确这个窗口，最终截止时间/时区以第二轮 phase 公告为准。

## 结论与目标

先修复实验可信度，再比较取证策略，最后决定是否训练策略模型。固定工作流与自主 agent 之间没有天然的正确答案。当前代码已经包含自主选择工具的 ReAct 循环，真正需要比较的是额外 planner、强制工具、检索与停止策略的边际收益。

MCP 是工具接口；RAG 是获取外部证据的动作；推理是模型处理现有状态的过程。三者可以组合。保留任务工具接口、确定性校验与结构化输出，把取证阶段做成可观察、可比较、可替换的策略。官方 baseline 同样采用 ReAct，并允许替换 agent 流程，要求提交离线运行。[官方 baseline 与评测说明](https://chimera-agent.grand-challenge.org/baseline-and-evaluation-repositories/)、[规则](https://chimera-agent.grand-challenge.org/rules/)

研究参考：[ReAct](https://arxiv.org/abs/2210.03629) 支持交替推理和动作；[Agent Lightning](https://arxiv.org/abs/2508.03680) 提供轨迹与训练解耦、信用分配的思路。它们提供候选方法，不构成本项目必然提分的证据。

## 已核实的项目状态

| 项目 | 核实结果 | 对后续实验的影响 |
|---|---|---|
| 实际执行链 | `plan → agent ↔ tools → predictor（如启用）→ form_fill`；外层 self_refine 默认关闭 | 旧“四阶段全开”描述不能作为现状依据 |
| 任务差异 | T1 默认保留 LLM 决策，T2/T3 默认 predictor override | 架构实验中，T2/T3 决策不变可能来自 override，不能误判 agent 没作用 |
| 强制行为 | planner 空工具回退、T3 mandatory tools、T1 deny；另有灰区强制 RAG 开关 | 区分模型选择、控制器干预与执行结果 |
| 历史 RAG | `EXPERIMENTS.md` 的 2026-09-05 记录显示强制灰区检索在当时样本上负收益 | 反对直接全量强制检索；不能据此断言所有检索策略无效 |
| 数据矛盾 | 10-28 报告在 197 例 T1 中发现 61 例至少一项冲突 | 应保留来源、时点和冲突，不能按验证标签选择“正确数值” |
| 现有更改 | 开始本次工作前，`inference.py`、`fact_check.py`、`form_fill.py` 等已有未提交修改 | 保留原更改；新结果记录当前输出边界版本 |
| 11-09 T1 跑批 | 197 个输入只有 88 个预测、109 个 `.failed`；104 个 APIConnectionError、4 个 TimeoutError、1 个 ExceptionGroup | `done` 标记不能代表产物完整；修复服务后显式补跑失败病例 |

额外待核查：planner 只截取每条上下文前 500 字；外层 reflect 的产出消费路径已有历史疑点。晚间接线测试已推翻“plan 导致主 system prompt 漏传”的猜想：真实状态首条仍为病例 HumanMessage，agent 会补入主 system prompt，因此不为此增设修复组。

## 数据审计与防过拟合

本次服务器审计只读取输入及标签文件的存在性，未读取未导入的本地材料或保留集标签。

| 服务器现有目录 | 输入病例目录 | 带决策文件 | 带 reasoning 标签文件 |
|---|---:|---:|---:|
| T1 | 197 | 91 | 91 |
| T2 | 156 | 72 | 72 |
| T3 | 76 | 73 | 0 |
| 合计 | 429 | 236 | 163 |

[公开版省略逐例记录；汇总与机制结论保留。]

完整输入审计：`output/round2_20261110/cohort_audit.json`。可重复执行：

```bash
python tools/audit_cohorts.py --sources configs/round2_sources.json \
  --output output/round2_20261110/cohort_audit_repeat.json
```

新来源以独立条目加入 `configs/round2_sources.json`，声明 root、role、label_provenance 和 identity_namespace。默认不要在不同 release 之间共用 identity_namespace；只有确认 ID 语义一致后才能据此联结。可声明患者映射字段 `patient_id_field`；未提供时，工具明确报告无法认证患者独立性。输入 hash 可以发现改名副本，不能排除近重复/不同版本的同一患者。

数据使用协议：

1. 现有反复调参数据、已分析的上一轮 dev/test 记作 `legacy_exposed`，用于开发/历史回归；不能重新命名为“未见测试集”。
2. 上轮尚未接触的新输入，如果只有输入没有标签，可检验覆盖率、失败率、分布变化和稳定性，不能测真实准确率；不把模型生成标签当作真值。
3. 有可用且获准用于开发的额外标签时，先登记发布批次、来源、患者组和重复关系，再预注册 selection/holdout；未知来源暂隔离。先确认旧 test 标签的第二轮使用范围，尚未把它们加入训练。
4. 患者组跨任务绑定；原样本、改名副本、合成变体、同患者不同时间点尽量在同组。ID 冲突按 `(release, task, case_id, input_hash)` 记录，不做简单跨批配对。
5. 小样本以分组嵌套交叉验证为主：外层评估，内层选择 prompt/阈值/策略。PCA、插补、标准化、特征选择、kNN、few-shot 索引、权重表、伪标签生成都只能使用训练折。
6. 给候选策略固定实验预算、预先指定主指标与晋级条件；相同病例、相同评测版本做配对比较。报告患者组 bootstrap 区间、退化病例、失败率和成本，保留所有尝试；不把多次调试后的最大值当无偏结果。
7. 真正未接触的 holdout 只做最终确认。若所有数据都已接触，明确报告交叉验证/历史回归，承认没有独立最终测试。

## 本地材料：已知线索与尚未读取的内容

用户确认 Mac 项目实际目录为 `/Users/lancheq/Desktop/项目汇总/Chimera-agent`，RP 位于 `/Users/lancheq/Desktop/留学申请/RP`。两处均无挂载/自动同步。当前聊天执行器位于服务器；旧 git_auto 中转试探没有返回，已取消 READY，避免延迟执行。

以下是用户提供的材料线索，**本次尚未读取其正文**：

- `reports/platform_test_20261104/PROVENANCE.md`：444 个任务-病例对，T1/T2/T3 为 162/154/128，overall 0.6312。
- `docs/EXPERIMENTS.md`、`docs/CHIMERA-agent_双线状态与待验证_20261026.md`。
- `docs/OPEN_ISSUES_未解决问题清单.md`：11-09 更新；A–E 分类的具体内容待读，不能声称已解决某个编号。
- RP 中的 Evidence Retrieval 11-10 修订版与旧版看起来是同一方向的版本，不自动视为两个研究方向。应阅读正文，另行确认策略学习方向材料的位置；不会把写作骨架当成研究方案。

用户补充的关键线索纳入后续审计：`platform_result_20261104` 与 `platform_test_20261104` 是同次 test 的副本；dev/test 非同批重测；T3 有重名 ID 但 GT/预测不同；test 与 train_release 有 175 个同名任务-病例对。**同名不是已证实的数据泄漏，无交集也不是独立性的证明**，均需比较真实输入与发布映射。

为材料进入当前执行器准备了 `tools/collect_round2_materials.py`：只复制上述文档、两份平台结果目录的文本/JSON/CSV及 RP 文档到一个新暂存目录，记录 SHA256，不同步项目代码。Mac 端可执行：

```bash
scp hedongze-server:/home/hedongze/CHIMERA-agent/tools/collect_round2_materials.py /tmp/chimera_collect_round2.py
python3 /tmp/chimera_collect_round2.py --output "$HOME/Desktop/chimera-round2-materials-20261110"
rsync -av "$HOME/Desktop/chimera-round2-materials-20261110/" \
  hedongze-server:/home/hedongze/CHIMERA-agent/data/round2_materials_20261110/
```

暂存脚本只收集审查材料，不搬运尚未定位的原始 development/test 输入。独立目录中的材料不会覆盖服务器补丁。材料到位后先读项目 `AGENT.md`，再核实文档与输入/标签包的实际关系。

## 评测器对齐：先处理版本差异

本次直接通过 GitHub 获取未修改的官方 `evaluation/evaluate.py` 和 section mapping，保存到 `output/round2_20261110/official_evaluator/`，内容 pin 在 `configs/round2_evaluator.json`。

| 文件 | Git blob SHA |
|---|---|
| 当前官方 evaluate.py | `adfee366f64942ea8a38a038aaf19afcc1901bcc` |
| 当前官方 section mapping | `a38b6793014de0545fa81a6721d4177dbdaf67b8` |

服务器旧 evaluator 与当前官方内容不同：当前 T2 是精确决策门控；缺失预测计入失败；T2 排名使用 weighted F1；T1/T2 排名结合 mean case score 与任务 F1，T3 用 C-index。旧脚本的 partial-credit、macro-F1 或逆推权重结果不能自动继承为第二轮正式指标。[当前官方源码](https://github.com/DIAGNijmegen/CHIMERA-agent/blob/main/evaluation/evaluate.py)

新增 `tools/round2_regression.py` 只调用原样官方函数，先验证 evaluator/mapping SHA256，再经过当前 `inference.py` 输出边界转换，输出逐例 CSV 和报告。保留原旧 evaluator，避免改变历史复现口径。

```bash
python tools/round2_regression.py \
  --evaluator output/round2_20261110/official_evaluator/evaluate.py \
  --mapping output/round2_20261110/official_evaluator/section_variable_mapping.json \
  --data-root data --predictions-root test/output.full_20261031b \
  --task 1 --task 2 --task 3 --cohort-role legacy_exposed \
  --output output/round2_20261110/regression_full_repeat
```

已完成的完整历史产物回放：

| 任务 | 有标签/有预测 | 决策 accuracy | 任务 F1 | judge-off ranking diagnostic |
|---|---:|---:|---:|---:|
| T1 | 91/91 | 0.7912 | yes F1 0.8155 | 0.7161 |
| T2 | 72/72 | 0.8611 | weighted F1 0.8611 | 0.7368 |
| T3 | 73/73 | — | — | C-index 0.8587 |

这些数字仅证明回放链路能覆盖现有标签：使用缓存预测与当前输出边界，未重新运行 agent；关闭了 rationale judge；T2/T3 predictor 可能在同批 GT 上训练，不是 OOF 或泛化结果。不能与用户提供的 test overall 0.6312 作提分对照。正式候选还需经过真实容器输入输出、固定 judge 版本和完整官方评测。

## 架构与两条研究路线

### 先做可解释的策略消融

本次已增加两个可切换模式：

- `agent.workflow=planned_react`：默认，保留现有 planner 与 ReAct。
- `agent.workflow=react`：跳过 planner，模型在现有工具集合中决定是否获取病例证据/检索指南，以及何时结束取证。

两组共用 predictor、表单、校验、输出边界。删除 planner 也移除了它内部的 few-shot 路径和强制工具行为，因此这是“移除 planner 整体”的实验，不能声称只测了一个抽象的自主性变量。若有差异，再拆 planner 文本、预执行工具、few-shot、mandatory tools 等因素。

配置 `agent.max_model_turns` 可限制取证阶段模型轮数，默认 null。最后一轮请求的工具先执行完，再进入输出阶段；不制造工具结果。此上限不包括 planner、refine、form_fill 的 LLM 调用，也不是 token/工具调用/总耗时预算。`max_iterations` 仍是 LangGraph 总递归限制。

实验例子（只在已声明的开发病例上运行，模型服务需先恢复；每组必须用新输出目录）：

```bash
python -m chimera_agent_baseline.run agent.tasks=[1] agent.limit=3 \
  agent.workflow=planned_react paths.output_dir=test/round2_planned_smoke
python -m chimera_agent_baseline.run +experiment=round2_react agent.tasks=[1] agent.limit=3 \
  paths.output_dir=test/round2_react_smoke
```

预算限制另做一个对照，不与首次移除 planner 的实验混用。之后才比较按证据缺口检索、冲突触发核验，以及条件性 refine。所有实验同时检查诊断、理由忠实性、实际工具行为、失败率和成本。

### 路线 A：节点策略学习

状态只含当时可见资料、已揭示证据、缺失项、来源冲突、模型/predictor 分歧和已花费预算；不含 GT、未来工具结果或测试标签。动作先约束为病例工具、指南检索、补充核验、结束取证；refine 作为独立候选，不默认执行。

当前新增 `policy_trace` 记录 planner 选择/成功执行/失败工具、ReAct 请求与结果、模型轮数、批次时间和可用 token usage，并保留 tool-call ID、工具名、参数与状态。**这是初步观测接口，不是完整 RL 训练数据**：尚未覆盖表单/全部后处理的成本，失败图的中间状态、行为概率、反事实回报也未完整采集。

先做患者组内、同初始状态的有限分支干预，比较继续推理、获取某条证据、检索或结束的实际差异。记录分配策略和随机化概率；历史确定性轨迹没有这些信息，不能伪造 propensity 或声称已做离线因果评估。缓存重放只适合已有证据的后处理，不能代替未发生工具动作的真实 rollout。

先尝试规则策略/小型 contextual bandit 或模仿学习，采用分组外层验证。若重复患者数量、独立标签与训练稳定性不足，维持规则路由。RL 的训练奖励可结合官方得分与成本/无依据断言惩罚，但成本权衡只在训练侧选择；对外仍报告原官方指标与独立成本，不把 shaped reward 当挑战赛分数。T3 C-index 是队列指标，不能冒充逐病例真实奖励。

### 路线 B：低成本指南与原子证据检索

保留离线 BM25/混合检索基础；只有明确的证据缺口或规则冲突才发起查询。指南回答“什么条件支持某决策”，病例工具回答“本患者是否满足条件”，两类证据分开记录。

原子证据候选字段：`claim_id / statement / source / guideline_version / section_or_page / applicable_population / conditions / exceptions / recommendation_strength / retrieval_score`。患者事实另带原始字段、单位、时点与工具来源；支持/矛盾/未知均保留。检索分数不是证据真实性或患者适用性。初期先人工核对小规模规则，不直接大批自动抽取后进入生产。

对照无检索、现有检索、条件触发检索，再比较原子规则通道；词法召回优先，只有候选不明确时才加 embedding/reranker。检查引用能否支持实际断言、例外是否遗漏、冲突是否诚实表达。离线检索命中率提高必须能传导到端到端指标，才纳入提交方案。

## 排期与晋级条件

| 时间 | 工作 | 交付/晋级条件 |
|---|---|---|
| 11/10–11/14 | 材料归档、问题清单对照、版本/来源审计、恢复服务、完整基线 | 完整病例清单、患者组映射、无漏例回放；所有已接触数据显式登记 |
| 11/15–11/22 | planned-ReAct vs 直接 ReAct；再逐项拆 forced tools/few-shot/budget | 固定候选预算、配对比较；增益不能来自漏评/训练患者混入/评测版本差异 |
| 11/23–11/30 | 条件检索与原子证据小规模试验；节点分支干预采集 | 证据正确、端到端无系统退化；成本与质量形成可选方案 |
| 12/01–12/07 | 数据足够才训练小型策略；不足则继续规则路由 | 外层分组验证；禁止在旧 test 上反复选动作或阈值 |
| 12/08–12/12 | 冻结方案、容器与完整官方评测 | 完整输入/输出契约、离线依赖、固定 judge、失败病例清零 |
| 12/13–12/15 | 按实际 phase 截止提前上传与复核 | 提交产物与冻结版本一致；保留回滚包与评测报告 |

优先级是决策正确性与评测可信度，其次证据可靠性与效率，再考虑研究复杂度。若阶段效果不确定，保留较简单的已验证配置；不为“更像 agent”强行引入 RL、额外推理或检索。

## 本次已落地 / 未完成边界

已落地：输入来源审计、官方评测内容 pin 与确定性回放、直接 ReAct 实验入口、取证轮数限制、策略事件记录、trace 状态覆盖错误修复、批次完整性清单与失败重试开关。`agent.retry_failed=true` 只重试失败病例，已有成功预测继续跳过；缺失预测使批次标记为 `incomplete`，而非 `done`。

验证：`python -m pytest -q tests` 共 20 项通过；覆盖实际 LangGraph 的规划/直接 ReAct 路由、最后一轮工具执行后停止、无工具结束、输入重复与跨任务 embedding 接触、标签不读取、失败/成功 trace 状态、部分跑批与失败重试、评测 pin 拒绝不一致文件。Hydra 实验配置组合与 Python 编译检查通过。测试使用脚本模型和本地工具，未执行新架构的真实 LLM 诊断实验。

尚未完成：本地 RP/问题清单正文阅读、额外 dev/test 输入导入、跨 release 患者身份核实、独立/OOF 基线、新架构真实配对跑批、原子检索上线、策略模型训练与提分验证。当前不宣称准确率提升，也未做平台提交。

晚间更新：根据用户授权，已启动真实配对跑批，详见 `reports/ROUND2_NIGHT_20261110.md`。旧计划仅作参考，实验组按新发现调整。真实冒烟发现 MCP stdio 子进程不继承自定义运行环境，已增加有限环境变量传递，保证独立 embedding socket、RAG 开关及冻结源码路径进入子进程；真实 MCP 子进程测试验证该修复且不传递模型 API 凭据。测试合计 24 项通过。
