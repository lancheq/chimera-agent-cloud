# G2-CASE-PILOT · 冻结病例准入与试跑接续

2026-11-19，北京时间。限本地审查。沿用首轮的三个冻结训练病例和原先的调用限额。病例材料、模型请求、逐例证据及任何训练集结果留在私有目录。 [首轮报告](ROUND2_G2_CASE_PILOT_20261119.md) · [本轮任务卡](../discussion/tasks/20261119_G2_CASE_PILOT_INTAKE_CONTINUATION.md) · [赛事规则](https://chimera-agent.grand-challenge.org/rules/)

## 病例准入

按官方任务输入面核对六个原始输入文件，逐一匹配首轮登记指纹。筛查以官方栏目、冻结工具权限、实际栏目内容和有据可查的源日期为基础。源日期只与就诊记录作比较，月粒度以区间保存。历史决策截点、原报告签发和首次可得性仍为 UNKNOWN。未打开 GT、参考推理、影像表征或冲突台账。 [官方输入](https://chimera-agent.grand-challenge.org/data-sources-and-imaging/) · [官方工具](https://chimera-agent.grand-challenge.org/tools/) · [私有逐栏矩阵](../output/g2_case_pilot_20261119/continuation/channel_and_eligibility.json)

| 任务 | 准入视图 | 主要扣留依据 |
| --- | --- | --- |
| T1 | 最小结构首屏，MRI、PSA 趋势、检验和既往记录按需取得 | 当前活检病理和家族史按冻结权限排除。首屏混合自由文本不进入视图 |
| T2 | 最小结构首屏，MRI、PSA 趋势、检验、活检病理和家族史按需取得 | 混合随访与治疗信息的既往记录整栏扣留。首屏的混合自由文本也扣留 |
| T3 | 官方术前结构字段，独立 MRI、活检病理及手术病理 | 结局、末次随访、通用 `months` 和跨任务工具排除。无内容的栏不注册 |

原值保留，未填补日期或临床字段。结构、工具语义和风险检查仅适用于这三个冻结病例的官方输入面。 [私有首轮回执](../output/g2_case_pilot_20261119/handoff.json) · [锁定文件](../output/g2_case_pilot_20261119/continuation/batch_lock.json)

## 单批许可与正式入口

新增单批病例许可，将任务、冻结名次、原件和投影摘要、配置、源码、MCP 工具子集和原预算绑定。根入口在读 Socket 前核对只读输入挂载和身份。在实际 MCP 子进程注册前使用同一访问账本筛减工具。正式入口与 MCP 均在每例独立的文件系统和 PID 命名空间执行，正式数据根与事后参考区域不可直接读取。最小依赖与获批知识路径只读挂载。病例推理复用原 Adaptive 控制、预算停止、SDK 计量、诊断、填表和双 Socket。B/C02、Reflection、predictor、few-shot、影像取材和诊断覆写保持排除。 [正式入口](../inference.py) · [病例许可](../src/chimera_agent_baseline/case_pilot.py)

外层唯一 llama-server 位于独立回环网络，病例进程共享该回环并隔离进程表。原生计数经私有 Unix socket 交由监督进程核验同一服务器进程与确切 SDK 载荷。真实发送仍经过原 G1T 计量和 HTTP 核验。正式入口模式和工程夹具许可分别验证。没有变更默认配置及旧 F06 的固定夹具校验。 [隔离运行与监控](../output/g2_case_pilot_20261119/continuation/orchestrate.py)

## 零生成与预检

最终七项正式入口检查通过。三个允许路径使用各自真实输入视图和实际 MCP stdio 子进程完成工具注册，账本、模型发送与 Socket 均为零。四个拒绝路径覆盖漏挂、错任务、投影指纹变化和模拟可读的宿主数据根，均在模型边界之前拒绝。另有五项原有权限、并发账本与合成/固定夹具隔离回归通过。隔离挂载早期缺少环境依赖和 Jinja 模板，保留零请求失败记录。补齐静态依赖并重验过程留档。最终封存附带模板和原有阈值资源指纹。 [正式入口门槛](../output/g2_case_pilot_20261119/continuation/dry/gate.json) · [旧权限回归](../output/g2_case_pilot_20261119/continuation/legacy_boundary_regression.log)

真实服务启动后沿 A-F06 原协议核对双资产指纹、服务进程、单 slot、16,384 上下文与模板指纹。五个冻结 SDK 载荷通过原生计数路径，填表前缀经模板与分词交叉核对。真实发送逐案另行按 SDK/HTTP 绑定记录。 [原生非生成证据](../output/g2_case_pilot_20261119/continuation/live/native_preflight/host_run.json)

## 真实试跑与批次停点

| 任务 | 状态 | 请求／信息单位 | 结论 |
| --- | --- | --- | --- |
| T1 | 正式入口完成。两份 Socket 回读通过 | 6／3 | 初始规划后收到 3 条已授权工具结果。发生非空重规划，并在第 3 策略轮由模型正常停止。诊断实际消费了这 3 条证据。填表与正式输出均完成，冻结 RAG 未触发 |
| T2 | 正式入口零生成验收通过，真实试跑未启动 | 0／0 | 预定病例与最小视图保留 |
| T3 | 正式入口零生成验收通过，真实试跑未启动 | 0／0 | 观测随访 UNKNOWN，预测时间和首次出现阶段均未产生 |

T1 的 6 次请求逐一匹配 SDK 载荷、HTTP JSON 和原生输入计数。总计 11,503 输入、4,737 输出、23,791 预留 token，工具受理 3 次，账本无在途。实际控制请求中的工具列表均与注册允许表相同。病例执行约 98 秒，北京时间 18:43:08–18:44:46。未打开参考结果，未做诊断正确率对照。 [T1 私有复核](../output/g2_case_pilot_20261119/continuation/T1_offline_reconciled_readback.json) · [控制请求复核](../output/g2_case_pilot_20261119/continuation/private_trace_checks.json)

**批次在 T1 输出完成后的监督回读处误停。** 批次回读器错误地从 `tool_return` 顶层提取工具名。实际工具名位于序列化的 `ToolMessage` 内。监督进程将空名称判作权限异常，按失败关闭规则退出并释放唯一已启动的服务。离线复核逐条交叉对齐控制请求、工具消息、访问账本、批准投影、诊断消费与两份 Socket，确认 T1 回执完整。原失败结果与双 Socket 原样保留。回读修正候选已在原 T1 日志离线通过，另有 6 项回归，包含同形状正例及错工具、错调用 ID、错指纹、越权和重复负例。 [原误停回执](../output/g2_case_pilot_20261119/continuation/live/T1/case_readback.json) · [修正候选](../output/g2_case_pilot_20261119/continuation/read_case_supervisor_fix.patch) · [回归](../output/g2_case_pilot_20261119/continuation/tool_receipt_regression.log)

唯一的 35B 服务于北京时间 18:41:50 开始本轮预检和启动流程，18:44:47 已退出，退出码 0。只用 GPU 0，运行中采样 21,360 MiB，进程峰值 RSS 21,835,824 kB。收尾两卡回到启动前闲置占用，宿主端口空闲，无本卡后台任务。原授权的一次启动及八次非生成预检已用完。T2/T3 接续需要规划复核回读修正，并另行确认一次启动和新实例原生预检额度。保留原选例，无需再跑 T1。 [宿主运行](../output/g2_case_pilot_20261119/continuation/live/live_status.json) · [设备采样](../output/g2_case_pilot_20261119/continuation/live_resource_samples.log)

执行期间未公开同步、上传或提交平台结果。旧报告、首轮阻断、F06 工件和暂存区均保全。 [私有交接](../output/g2_case_pilot_20261119/continuation/handoff.json)
