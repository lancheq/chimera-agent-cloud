# P01-G1T｜真实工具/结构请求的精确模板计数（后继任务）

[公开版省略逐例记录；汇总与机制结论保留。]

北京时间 2026-11-18 12:29:57 尝试续写，CLI 回执 `d20261118122957-4c8b6c`。原 G1R 轮已经结束，CLI 仍因主会话存在 active writer 拒绝接续，退出 1。未取得 `thread.started` 或 `turn.completed`。由当前持有主会话的客户端直接接续，或在确认释放写入后再通过 CLI 续写。

## 目标与前置

G1A正确地对未验证tool role/tool_calls/schema/response_format fail-closed，但这仍会阻断真实候选工具/结构请求。Goal是证明候选现有后端的实际模板路径与完整计数输入一致，并在可证明范围内接好精确计数适配；不能删除tools/schema、转为旧纯文本或放宽预算通过。

先读 AGENTS、本卡、`reports/ROUND2_P01_G1R_PLANNING_REVIEW_20261118.md`、G1A runtime_contract/request_coverage 及当前 model_context/request_meter。用 `output/p01_g1r_reflection_20261118/manifest.json` 绑定接续版本并保全旧证据。主会话是本阶段唯一源码写入者。

## 执行范围

1. 用无患者、无GT夹具列出现有候选实际SDK请求形状：完整role/tool_call id/返回/tool schema及所需结构输出约束。只覆盖实际待接路径，控制器选择仍不在此Goal决定。
2. 核对现有llama.cpp构建/模板/原生模板与分词接口及服务端chat处理路径，优先本地现有源码/作者文档。不更换/重启模型，不安装依赖/大下载。服务不可用或模板不可证明即报告BLOCKED，不自建服务。
3. 允许已有服务的非生成`/props`、`/apply-template`、`/tokenize`等必要探针，最多30次；计入独立CPU tokenizer账本。禁止`/chat/completions`、`/completion`等生成请求，不因所谓max_tokens=0冒充无生成；0真实生成/GPU新任务。
4. 证明模板渲染使用与真实SDK请求一致的完整语义和选项，包括tool_choice/并行选项/JSON约束/生成前缀等实际生效项。接口不接受的字段不能静默丢弃；约束不占prompt token也须有服务端实现证据和独立配置绑定，不能凭猜测。
5. 若原生apply-template与chat路由不等价，先报最小匹配适配方案与依据，不用json序列化长度、人工估算token或截断上下文代替精确计数。已知模型/template/依赖身份、context limit、最大输出和预留必须绑定。
6. 实现获证实范围内的最小model_context适配和对应fake/真实非生成模板测试，连接PrivateRuntime计数；budget/identity/unknown-option仍在发送前拒绝。工具/schema不支持的分支保持显式拒绝和缺口，不放宽G1A真实生成/正式profile门槛。
7. 交完整shape→模板选项→token回执→预算判定→payload digest映射，正向及超限/错误role/id/选项/身份/端点故障测试。只有预先登记的合成探针，不读真实患者请求正文，不跑诊断。

允许修改model_context及必要共享计量适配/专项tests；如需改entry_profile，仅限无生成探针身份检查，不批准真实load_model或默认配置。临床Prompt、规则、rank、策略、资源/冻结快照、模型版本均不变。

## 输出、验收与停止

独立`output/p01_g1t_exact_tool_context_20261118/`和`reports/ROUND2_P01_G1T_EXACT_TOOL_CONTEXT_20261118.md`，含handoff、版本/代码diff、后端证据定位、探针清单/原始回执、形状覆盖/拒绝矩阵、源码/资源身份、预算与回归、保全。非生成调用与真实生成0分别记录。

验收要求是完整语义没有丢、计数和实际服务模板有可核对对应、边界错误仍fail-closed；非生成模板验证不等于真实工具决策或诊断验收。D01–D05完整机制组合、真实profile、生成预算和G2仍待规划另行冻结。不得自动进入G2或容器。

资源最多1进程/2线程/2GiB本地验证，不抢常驻服务；预估1–3小时、非硬截止。Plus余量未知，不开代理、不扩搜已有方法或批量重测。关键模板/服务信息缺失、探针超限、需真实生成/新服务/下载或修改医学行为时停止报缺口。共享导航与发布由规划维护，不上传/平台操作，不与G1R/其他代码任务并发。
