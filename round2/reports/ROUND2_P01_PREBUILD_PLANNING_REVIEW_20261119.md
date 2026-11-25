# P01 构建前复盘与交接裁定

2026-11-19，北京时间。内部规划。独立 trace 和 staging 工件已通过文件指纹回读。原自动请求未返回完成回执，规划依据用户转交的工件和现场证据验收。后续任务提示词由用户交给现有执行 Agent。

## 首批证据复核

- **T3 更正。** 本文取代首批规划报告中的数值阶段归因，原报告保留以维持已登记指纹。真实病例的 `Months to Recurrence: 18` 已出现在诊断响应 seq 32，并逐字进入填表输入。表单、后处理及 Socket 沿用 18。原审查器漏掉标签式结果并把年龄计为时域。具体数字来自诊断模型估计，准入证据足以支撑定性讨论，无法把 18 锚定为可校准的病例数值。观察随访及观察复发 UNKNOWN。接口工程 GO，数值晋级 HOLD。
- **F06 分列。** 检出草稿明确写时间不可得，必填字段在表单首次输出 60，配套理由描述五年预测范围。模板给出 6–60 与 `NEVER 0`。60 与提示范围上界相同，现有单次 trace 无法定量或证明该提示对具体选择的因果作用。官方目标的结果字段与排行榜排序合同不指定固定 60 个月。[官方 T3](https://chimera-agent.grand-challenge.org/task-3-prostatectomy-pathology-prediction/) · [计分实现说明](https://chimera-agent.grand-challenge.org/baseline-and-evaluation-repositories/)
- **T1/T2。** T1 误停位于事后读回，原推理与两份 Socket 可复核。修复以请求号、摘要和出账绑定回执，原误停与离线修复均保全。T2 检索回执真实进入诊断，八条命中均无该病例分期的直接治疗推荐。最终方案的指南归因缺少对应命中，可能由模型已有知识生成。检索改变最终决策的因果关系 UNKNOWN。工程消费证据与指南适用性分开登记。[逐层审查](ROUND2_G2_TRACE_REVIEW_20261119.md)

## 容器状态与顺序

- **staging 已就绪。** 独立核对 context 66 文件、49,368,267 B，49 份运行源码一致。实际宿主合成检查 13／13。两份旧归档已做单次流式 CRC 与 SHA，模型主成员匹配已冻 GGUF，predictor 成员按关闭裁定排除。context 已清除 `.chimera_run.json` 和 `.pyc`。历史 v15 专作 ABI 和构建对照。[staging 报告](ROUND2_P01_OFFLINE_STAGING_20261119.md)
- **先做 Docker 准备。** builder、amd64 base、wheelhouse、可移植后端和现场 phase 回执并行核对。当前服务器没有已核实的容器运行时，staging 未产 OCI digest。现有 Dockerfile 默认只跑预检，正式 phase adapter 仍待核对与绑定。条件齐备可先出独立预检镜像；通过真实平台适配与同配对离线检查后，再裁定是否生产晋级。[唯一转交卡](../discussion/tasks/20261119_P01_DOCKER_BUILD_HANDOFF.md)
- **新二进制单独验收。** 历史使用 `-march=native` 的 ELF 与目标 AVX2 基线存在移植风险。收到新二进制的编译身份、哈希、依赖及无 AVX512 主机实测前，不授权纳入候选锁定。规划观察到编译已至 98%，预构建 UI 仍尝试下载；可由当前执行者按回执卡处置。无须重新开会话。
- **资源与平台。** 本地 llama-server 峰值 RSS 约 21.8 GB，首轮候选要求 A10G 及 32 GiB 主机内存，并核对 phase 是否允许。通用表上的 A10G 为 24 GB，AWS 表对 G5 实例给出 22 GiB。先用目标的实际可用显存、冷启动时间和 cgroup 峰值验收。现有 3090 实测约 21,696 MiB，迁移显存余量有限。Mac 只承担构建和通用 CPU 检查。[GC 运行环境](https://grand-challenge.org/documentation/runtime-environment/) · [AWS 实例规格](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)
- **阶段边界。** Try-out 与 phase 正式提交分开，Try-out 自身受平台积分或使用条件约束，旧激活和上传耗时只用于估算。独立模型槽及可重用配对须核对本次账号和 phase。B 暂停、T3 数值 HOLD、完整离线验收及单独授权继续作为正式 validation 前置。[Try-out 规则](https://grand-challenge.org/documentation/try-out-your-algorithm/) · [提交操作](https://grand-challenge.org/documentation/making-a-challenge-submission/)
