# C02证据包候选工程验收：工程PASS，医学NO_GO

2026-11-17。任务S6前置，唯一主要改变为显式启用的来源／适用条件证据包组合契约。临床新生成0、GPU模型计算0、训练0。工程候选及合成消费验收完成，提交规划复核；**不具备直接开展正式医学批次的晋级依据**。

## 修了什么、为何如此

A01发现任务二次规则未区分来源级别、目录混入、条件/强度丢失。本候选绑定可核对的来源和完整条件，不调整query、rank/top-k、计划/停止、临床预算、诊断/填表Prompt、predictor或35B。默认配置未启用；只有另行合并新opt-in配置才激活，且要求新账本/输出。

原始hit顺序和文本身份保持，私有日志记录全部原命中及去向；目录、未证实任务捷径和未映射来源不提升为确定行动依据，不补检索、不静默回退。正向结果保留完整条件化原文及来源版本、文件SHA、位置、强度、例外和条件核对。输出始终是条件参考，不替患者给出诊断/行动结论。条件元数据明确是at_retrieval，不能把缓存状态当永久事实；后续仍应依完整原条件及当前合法观察理解。

## A01更正

先前把lenticular与“半月形”区分为已证实翻译错误，理由过强：已下载PI-RADS2.1词汇表SHAPE（印刷p32）明确包含crescentic，撤回该子理由。MARGINS同页将non-circumscribed释为Ill-defined，与仅non-encapsulated不同，该子问题保留。P60仍列内容偏差，P61仍列继承条件不全；行级36/17/13/3总数不变，不代表独立错误数。原A01台账/报告未覆盖，修正保存在新review_addendum及本候选映射，旧错误理由未沿用。[ACR原始版本入口](https://www.acr.org/Clinical-Resources/Clinical-Tools-and-Reference/Reporting-and-Data-Systems/PI-RADS)。

## 实际范围及未知

- 69条摘要都有原行、原摘要文件SHA、审计定位与条件、二次来源级别和处理理由。此映射不是69条官方推荐已获运行时授权。
- 运行时仅核验两个已在冻结KB中的完整EAU2025条件化引文载体，另识别两个目录及两个任务捷径。其他KB命中保持unknown并记入日志；不以组织标签猜版本或权威。原文按固定SHA和声明的版式列处理核验，未使用模糊语义匹配。
- 上下文只来自绑定upfront和InformationAccess已获cache；不接收模型传入的可见性字典，不读新患者、GT、质量标签或隐藏检查。既往阳性与阴性复评分开；缺失/None不推定为初次。
- 文本解析最终仅支持完整规范三行IMPRESSION/PI-RADS/带单位密度，或无额外时点修饰的明确定义结构值。普通真实报告、历史/否定/多时点修饰及不能可靠映射的格式保持unknown；不把未知家族史当阴性。这是实际覆盖限制，**不是已验证真实91例条件覆盖**。
- 初始按行前缀取值存在历史/未确认数字误读，已根据独立规划复核收窄并加入同句和跨行反例。没有扩大临床NLP、按GT清洗或调Prompt。

## 最后冻结版本的验收

独立候选目录`output/evidence_package_candidate_20261117/frozen/`，271文件；overlay SHA为`2f61e4232e6b54fcc6a82a289fa73a5083ccf6cb54df62a92dfb5b632f5b43f9`。最终测试与集成命令均以该目录为cwd、其src/工具为PYTHONPATH执行。公开工作文件与受验收冻结文件逐项相符。较早的unit_tests/integration/regression_final日志只是准备检查，不能替代最终frozen_*证据。

| 门槛 | 结果 | 证据与边界 |
|---|---|---|
| P60更正、原A01保留 | PASS | 新addendum，原账本不变 |
| 69条来源行级绑定 | PASS | 69个单独原行/SHA/无权威提升测试；不认证医学真值 |
| 缺版本、源SHA/overlay、破损命中 | PASS | 明确异常/严格失败，无旧捷径回退 |
| 目录/任务捷径/未知/重复/空结果 | PASS | 私有保留命中及理由；消费者不接收未经授权的原捷径 |
| 条件匹配/不符/未知/冲突及管理人群 | PASS | 完整原条件保留；automatic_action_authorized始终false |
| 伪造可见性、跨病例、源改写 | PASS | 只接受真实InformationAccess绑定与匹配upfront |
| 历史/未确认/跨行修饰 | PASS | 非规范完整格式unknown，保留正向路径 |
| 字节/配置token长度上限、错误费用 | PASS | 不静默截断；失败仍计获准尝试；缓存返回也检查 |
| 最终专项及既有默认/控制回归 | PASS | **204 passed in 136.68s**，包括真实stdio边界 |
| 实际CPU检索→MCP→核验→stub消费 | PASS | 正向条件参考确实被收到，源与消费者digest一致 |
| 实际原query/命中/rank一致 | PASS | 默认与启用组8条原hit SHA及顺序完全一致 |
| 旧资源及源码保全 | PASS | 3321既有文件、278旧冻结源码未变；新271文件指纹复核 |
| 真实输入格式/条件/来源覆盖 | NOT_DONE | 没有读取额外患者材料做覆盖认证；医学晋级阻断项 |
| 全库来源可用/独立患者效果 | NOT_CERTIFIED | 不是全库标注、独立医学验证或提分证据 |

实际同一合成正例的默认/启用两路结果如下，不是患者准确率：

| 指标 | 默认 | C02 |
|---|---:|---:|
| 原始检索命中 | 8 | 8，同顺序 |
| 最终知识条目 | 8 | 2个完整条件参考 |
| 证据payload字节 | 8743 | 6581 |
| 同加载GGUF tokenizer的payload token | 3089 | 2828 |
| 合成新增临床信息单位 | 1 | 1 |
| 获准尝试 | 3 | 3 |
| 实际检索回调/缓存返回 | 1/1 | 1/1 |
| stub模型调用 | 5 | 5 |
| 真实临床生成/训练 | 0/0 | 0/0 |

CPU集成两路共约168.13秒。两条保留参考分别展示条件matched和unmet；两者都没有自动行动授权，otherwise分支未删。6条未映射命中只说明本候选有限覆盖，不能断言其医学无用。上述token只是证据payload，不是整个请求/批次成本，也不是质量/准确率收益。

最终候选配置对本机已加载同GGUF的`/props`、`/tokenize`执行CPU身份/计数，交付日志记录精确token；从未调用生成端点。纯单元接口未配置tokenizer时明确未知，不用字符估成token；整体模型上下文仍须通过已有模板/长度门槛。GPU常驻权重不等于本轮GPU计算。

## 改动及复现

新增`evidence_package.py`，只在InformationAccess、MCP及guarded_runtime作显式opt-in连接：overlay身份进入账本绑定、检索返回包装、实际消费校验。原rag.py/shared_plan.py与诊断/填表Prompt没有本轮修改。新增构建/验证工具、专项测试和opt-in配置；本轮准确源码差分保存`source_diff.patch`，不把脏工作区相对旧Git HEAD的全部差异冒充本任务改动。

可复现命令/工作目录见私有`reproduce.json`；machine证据为`acceptance.json`、`frozen_manifest.json`、`frozen_regression.log`、`frozen_integration/summary.json`及`preservation.json`。最终测试日志SHA：`92b741827b10326869dfb663dbafdf84dc48344f98052a56dbcefb85b4a61899`；集成摘要SHA：`2f632becff1d3bad68d5283e2851e7d18c1df7e6f5be01dde5e6f5311a74265e`。源长文、临床原文/GT/trace不上传，公开汇总和代码不包含患者记录。

工程从北京时间15:51启动；资源与日志约36MiB，CPU线程变量4，GPU计算0、训练0、临床生成0；未独立采集峰值RSS/CPU秒和Codex token费用，不伪造账单。所有有限验收已完成，没有后台实验等待。

## 下一步与临床NO_GO

提交规划验收C02，保持默认关闭。下一正式实验前先按GT盲、仅合法已见资料核对真实输入格式/条件与资料来源覆盖，预注册失配、空包、未知、失败处理；C02没有完成这些门槛，也不在本轮扩大医学NLP。

[后续有限医学验证草案](../docs/ROUND2_EVIDENCE_PACKAGE_VALIDATION_DRAFT_20261117.md)只提出最多12个保守组、两种呈现、基础48/上限120次新调用及成本和停止口径，未执行或自动授权。固定已获证据的诊断重放可先隔离知识呈现，不能把检索/控制一起改后归功于来源字段。已暴露91例仍只能内部探索；来源完整和合成通过不能代替医学收益。

当前交付状态为ENGINEERING_PASS_CLINICAL_NO_GO；后续规划复核尚待进行，不自行启动临床批次、其他工作线或新会话。

公开存档沿用既有scrubber，仍可能误匹配GPT模型名称并省略相应规划行；本地原文保留，本轮未为此修改发布工具。临床原文/GT/逐例trace和原始指南长文不上传。
