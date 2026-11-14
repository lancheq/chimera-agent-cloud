# S2/P4：工具信息权限与成本契约 v1

后续更新：P5范围与知识资源授权已完成，见[范围验收](../reports/ROUND2_S2_FULL_SCOPE_ACCEPTANCE_20261113.md)。以下保留P4当时状态，不能将“知识manifest未实现”等旧文字视为当前实现。

2026-11-13。状态：契约与首个可执行MCP边界候选；尚未接入统一agent runner，不能据此宣称S2/P5整体通过。此次不改变默认生产行为，不运行诊断性能批次。

## 文献、协议和实现核查

- [Shim等，NeurIPS 2018，论文§3](https://papers.nips.cc/paper_files/paper/2018/file/e5841df2166dd424a57127423d276bbe-Paper.pdf)：将已观察特征集合、取证动作与成本明确分开。此处借鉴可见信息边界，不采用其分类／策略联合训练，也不移植文中的检查价目。
- [MedAgentBench作者仓库](https://github.com/stanfordmlgroup/MedAgentBench)：核对其真实FHIR环境与任务服务的部署组织；不复制患者资料、参考解或其数据的价格。本次只借鉴工具执行须在环境中验收的原则，未复现其完整服务。
- [MCP 2025-06-18工具规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)：核对工具schema、访问控制和执行错误`isError`接口。现有stdio客户端／服务端保留，预算拒绝通过工具执行错误返回，不能伪装成功取得资料。
- [SQLite事务说明](https://www.sqlite.org/lang_transaction.html)：采用短`BEGIN IMMEDIATE`事务原子预留，后端调用时不持有写锁，完成后另事务结算。避免两个工具同时看到相同余额而超支。
- 本地冻结官方evaluator的`cost_aware_tool_score`仍是揭示栏集合的使用精度；本轮不改公式。它不是下面的运行预算。

以上是方案依据与本项目适配，不是论文已证明本项目候选更优。

## 唯一研究目标与当前改动

先使“未获授权的信息不能返回、预算不能通过重连重置”成为执行事实。新增独立`information_access.py`，MCP服务增加显式`--access-session`选择该边界。不开启该参数，既有临床返回路径不变；本轮不把新边界默认加到生产runner。

选用持久化SQLite而非进程内计数：现有`MultiServerMCPClient`工具可能在不同子进程／连接执行。账本绑定session、病例、任务、输入快照、契约、预算和初始来源；重启只能复用相同绑定，不能悄悄提升预算或更换病例／资源。

## 动作与权限

| 动作 | 可返回字段 | 注册任务 | 信息单位 |
|---|---|---|---:|
| get_psa_trend | psa_trend | T1/T2 | 首次成功返回1 |
| get_lab_results | laboratory_results | T1/T2 | 首次成功返回1 |
| get_mri_report | radiology_report | T1/T2/T3 | 首次成功返回1 |
| get_pathology_report | pathology_report | T1/T2/T3 | 首次成功返回1 |
| get_surgical_pathology_report | surgical_pathology_report | T3 | 首次成功返回1 |
| get_previous_notes | previous_notes | T1/T2/T3 | 首次成功返回1 |
| get_family_history | family_history | T1/T2/T3 | 首次成功返回1 |
| search_guidelines | query/results | 任务共享知识 | 患者信息单位0，调用次数／耗时仍计 |
| get_image_predictor | 影像衍生输出 | 授权待验收 | 受控模式暂拒绝，不视为免费计算 |

这是预存信息栏的等单位代理，不是实际给患者做检查、货币费用或指南与真实检查的价格比。费率无法核实的现实货币／等待／风险成本留空；诊断指标、官方工具分、信息单位、执行次数和计算耗时分别报告。后续模型token／GPU成本由runner单列，本次账本不声称覆盖它们。

患者信息、指南资源与其派生输出都需要来源绑定。当前受控MCP只验收临床字段；指南资源manifest验证尚未实现，故该模式下显式拒绝检索而不是绕过预算。独立账本核心可用明确知识快照做0信息费／有调用上限测试；不等于真实RAG链路已验收。影像来源与下游predictor／few-shot权限仍是P5后续工作。

## 计量与状态转换

1. **执行前**检查病例、工具注册与参数，再检查获准调用次数上限、缓存、在途请求和可用信息余额。信息预算先原子预留，拒绝时后端fetch不能执行。
2. **成功返回**只接受同一病例与工具声明字段；未知字段、错误病例及不可序列化结果均拒绝，不原样泄露异常信息。新返回字段含显式`[]`／`null`也算一次揭示，避免免费获取否定信息。
3. **资料栏不存在**不写成成功揭示：记录调用、释放预留、返回“无数据”元信息，不向visible集合加入该字段。即使预计无数据也不能先读后决定免授权；余额不足仍在读取前拒绝。
4. **重复／缓存**同一快照已成功返回的字段不重复付信息单位，但每次获准调用仍消耗一次调用额度并记录耗时。缓存内容源于首次成功返回；不能通过caller声称“已看过”生成缓存。
5. **初始完整资料栏**只有可信启动配置、初始prompt实际包含相同内容且hash一致，才能免费写入初始可见集合。已有PSA标量不等于完整lab栏已揭示，故打开整个lab仍是新增信息。
6. **失败／重试**后端失败消耗获准调用额度，不收成功揭示费并释放预留；重试是新请求。返回错误payload不算成功字段。`BaseException`清理可处理的取消；进程崩溃遗留预留保持占用，不自动超时回收后再次免费执行。
7. **并发**相同字段在途时拒绝重复fetch；不同字段共同检查已用＋预留，不能各自独立批准超预算。服务重连共享同一SQLite账本。
8. **停止／快照**账本可导出已用／预留、可见资料和事件；`require_visible`只返回已合法得到的字段。自主停止动作、向agent提供合法预算状态以及各consumer接线仍待P5/S3，不能拿当前余额限制当成已实现策略。

事件记录病例、session、工具、授权／成功、请求字段、预算前后、缓存、失败类型、时间、响应摘要与大小；原临床缓存仅为受限执行状态，不作为模型自由可读文件。审计台账、GT／reasoning与模型API密钥不作为信息来源。

## 接入与验收门槛

可信会话JSON需显式给出：`session_id`、`case_id`、`task`、`snapshot_id`（本例clinical原始字节SHA256）、`ledger_path`、`budget_units`、`max_attempts`；可选`initial_sections`及对应prompt SHA256。预算值只是实验配置；本次工程测试的1单位等数值不自动成为S4选定预算。

MCP服务新增可选参数，不新增模型可调用的提额／改病例工具。当前任务级runner共享工具客户端，尚需逐病例绑定／恢复配置及测试；**不要把一个病例配置直接套到全任务运行**。

本轮目标是单元正反例及真实stdio跨进程工程验收：超支前拒绝、错误病例／未知字段、并发预留、失败／空资料／缓存、初始来源和重连不重置。通过只说明这个工具边界可用，不能证明planner、few-shot、predictor、图像和全部读取路径已闭环。完成这些后再交付S3候选，才可做S4公平对照。
