# A-T3-INPUT · T3 输入准入及上传前置复核

2026-11-19，北京时间。对象是已受信的 T3 Adaptive 工程入口。真实 A-G2 工件保留为历史证据。官方将年龄、术前 PSA、DRE、术前治疗指标列在 T3 upfront，将复发／末次随访时间和 event 列为目标。此次只采用这四个公开输入字段，不承认额外别名。 [官方数据字段和目标](https://chimera-agent.grand-challenge.org/data-sources-and-imaging/) · [官方 T3](https://chimera-agent.grand-challenge.org/task-3-prostatectomy-pathology-prediction/) · [A-G2 规划验收](ROUND2_A_G2_ADAPTIVE_HOST_SMOKE_PLANNING_REVIEW_20261119.md)

## 缺口与修复

冻结 F06 的四个真实请求（planner、两次 policy、diagnosis）原样带入通用 months=12。旧真实诊断草稿将其描述为已完成随访，填表请求随后承接这句话。工具回执未提供该随访。既有结果只用于回查这一传播链，不作为隔离后模型表现。 [旧执行报告](ROUND2_A_G2_ADAPTIVE_HOST_SMOKE_20261118.md) · 请求索引

受信 T3 Adaptive 路径在入口物化时从 structured-prompt Socket 建立独立视图。只准入 age 整数、psa 有限 JSON 数值、dre 文本和 active_treatment_prior_to_surgery 文本或 null。case_id 与 task 由入口适配器绑定。通用 months 记为时间语义不清，event、已知复发／末次随访字段标为非 upfront，未知扩展和错形状字段按原因剔除。原有 GT、参考理由及质量审计字段仍硬拒绝。数值和文本原样保留，字段准入只依据已认证的来源、字段和类型。

原始 Socket 字节在私有运行目录单独保存，记录来源和 SHA-256。病例目录只写准入视图，首轮渲染和 Guard 渲染都从该文件读。Guard 在取证和模型请求前复核病例、任务、投影字节摘要，再把同一投影与临床快照、会话、原件及权限账本链接。规划、观察后决策和诊断共用该上下文，填表用其下游诊断草稿。合法的独立临床 Socket、已获准的病理与既往记录工具、Adaptive 策略、信息预算、G1T 线序列化计量和双 Socket 合同沿用。 [入口准入](../inference.py#L137) · [Guard 绑定](../src/chimera_agent_baseline/guarded_runtime.py#L58)

范围是根入口已有受信 Adaptive T3。默认生产配置与其它候选未切换，T1/T2 的 months 仍按距上次 PSA 的间隔使用。其它 Guard 分支仍按原入口读记录。目前 T3 guarded ReAct 只有代码注入的合成验收，若拟接正式入口，需为它的渲染、JSON 后缀、模型请求及后处理补同等准入验收。本轮的字段准入不认证自由文本中的时点或真假。正式 B 的迟到原文隔离、可比性与截止另待验。 [B-TIME 验收](ROUND2_B_TIME_PLANNING_REVIEW_20261118.md)

## 零生成复验

**130 项通过**，单 CPU 测试 worker，墙时约 5 分 50 秒，主进程峰值 RSS 513,128 KiB。保留既有 Pydantic 枚举序列化告警日志，输出仍逐一回读。A-G2 工件、依赖报告与固定夹具共 962 个登记文件的 SHA-256 复核一致。真实生成、新患者输入、GPU 作业、下载、镜像及平台账号操作均为 0。无后台任务。

| 路径 | 验收内容 |
|---|---|
| 冻结 F06 原件 | planner、两次 policy、diagnosis 只收到 age、psa 和入口绑定身份。填表经诊断草稿传下同一视图，无 months。MRI 和手术病理按既有权限取得，两路正式输出已回读 |
| F06 合成扩展副本 | DRE 和手术前治疗字段送达；通用 months、参考 event、复发和末次随访、未知扩展以及放错来源的既往记录均未送达。原件与投影可分别核验 |
| T1/T2 合成副本 | 距上次 PSA 的合法 months 保留在各自渲染与决策视图。各 5 次模拟发送，工具与 Socket 合同通过 |
| 拒绝路径 | 非法 T3 工具、投影篡改、预算耗尽、非对象输入、审计字段混入、错误 profile 和缺有效表单均有回归。拒绝时没有占位结果或双 Socket 成功回执 |

每个正例 5 次 SDK MockTransport 发送、2 次合成病例工具、2 个信息单位。诊断桩回送实际收到的准入记录，用于检测填表传播。模拟计数器与序列化 HTTP 摘要逐一匹配，真实模板计数和真实模型判断留待一例复验。首轮回归发现旧分支的故障测试替身无法接受多余关键字参数，已把新参数的调用限定在 T3 Adaptive 分支并重跑原测试。 回归索引

## 一例真实模型复验提案

先交规划独立验收本轮证据。若需确认真实 35B 理由，只用原冻结 F06 一例和当前修复指纹，复用获批可信 loopback 实例及原生计数合同。保留原输入、权限、工具和输出字段，沿用每例 12 次请求、3 信息单位、6 工具申请／受理、5 决策轮和 195,072 最大预留 token，单次和槽位约束照 A-G2。记录允许字段、真实工具、诊断草稿和两路结果，人工核对理由是否把未取得随访写成观察。遇身份、证据边界、预算或输出错误即停，不换样或扩到 T1/T2。本卡没有执行该次真实复验。

## 上传状态

本地未找到可核对的容器 builder。二轮目标 phase 的进程式／invoke、设备、限时和账号实际额度保持 UNKNOWN。当前镜像仍须解决 35B 启动、非 root 模型和审计挂载、SDK 与知识栈依赖、生产 D01–D07 及 C02/B 范围。若平台操作占用首次正式 validation，须先完成 B 正式验收。 上传门槛矩阵

