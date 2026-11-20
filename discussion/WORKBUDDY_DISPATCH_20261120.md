# Codex 规划派发到 WorkBuddy

2026-11-20。Codex 负责规划、写任务卡和验收。执行端按每张任务卡显式选择，WorkBuddy 自动路径使用腾讯 WorkBuddy 企业版托管智能体（WMA）的 OpenAPI 和 ACP。启用该路径需要一个配置为 DeepSeek V4.1 Flash、已启用并可通过公网 API 访问的 Agent 版本。个人桌面版的本地助理 API 可以触发任务，其公开请求格式未提供逐轮模型锁定和型号回执。[托管接入](https://cloud.tencent.com/document/product/1831/140759) · [桌面助理 Open API](https://open.workbuddy.cn/docs/openapi)

## 配置

在启动派发的 shell 注入以下环境变量。复用本机凭据管理方式，勿把值放进任务卡、参数或 Git。安装后的入口见[原派发通道](DISPATCH_CHANNEL_20261117.md)。

| 环境变量 | 值 |
|---|---|
| `CODEX_DISPATCH_WORKBUDDY_ENTERPRISE_ID` | WMA 企业 ID |
| `CODEX_DISPATCH_WORKBUDDY_AGENT_ID` | 公网 Agent ID |
| `CODEX_DISPATCH_WORKBUDDY_VERSION_ID` | 已启用并固定使用 DeepSeek V4.1 Flash 的版本 ID |
| `CODEX_DISPATCH_WORKBUDDY_API_KEY` | 可访问该企业 Agent 的 WorkBuddy `ck_` 开放平台 key 或 `pt_` 令牌 |
| `CODEX_DISPATCH_WORKBUDDY_API_ORIGIN` | 可选。控制面 HTTPS origin，默认 `https://www.workbuddy.cn` |
| `CODEX_DISPATCH_WORKBUDDY_AGENT_ORIGIN` | 可选。Agent HTTPS origin，默认 `https://<AgentId>.ma.workbuddy.cn`，与返回的 PUBLIC 接入点核对 |
| `CODEX_DISPATCH_WORKBUDDY_TIMEOUT` | 可选。单轮最长等待秒数，默认 180 |

新装时把 `discussion/codex-dispatch.sh` 安装为 `~/.local/bin/codex-dispatch`，把 `discussion/codex-dispatch-workbuddy.py` 安装为 `~/.local/share/codex-dispatch/workbuddy.py`。WorkBuddy 客户端只用 Python 3.9+ 标准库。

## 派发和验收

按项目约定使用任务卡明确输入范围、交付格式、停止条件和验收标准。WorkBuddy 在云沙箱执行，`--cd` 用于本地登记，任务卡正文是唯一发送内容。任务卡须用已批准外发的自含材料。新会话会创建计费沙箱，同一项任务的接续可复用已核对的 WorkBuddy 会话 ID。

```bash
codex-dispatch new --backend workbuddy --name WB-SMOKE \
  --cd /home/hedongze/CHIMERA-agent \
  --prompt-file /home/hedongze/CHIMERA-agent/discussion/tasks/20261120_WORKBUDDY_DISPATCH_SMOKE.md

codex-dispatch status <dispatch-id>
codex-dispatch log <dispatch-id>

codex-dispatch send wb:<session-id> --backend workbuddy \
  --cd /home/hedongze/CHIMERA-agent --prompt-file /绝对路径/接续任务卡.md
```

1. 派发器读取 Agent 版本，核对启用状态和配置型号，再创建会话或确认接续会话空闲且属于该版本。[查询版本](https://cloud.tencent.com/document/product/1831/140354) · [创建会话](https://cloud.tencent.com/document/product/1831/140347)
2. ACP 回传 `stopReason=end_turn`，且本轮 `usage_update` 只报告腾讯示例中的 `deepseek-v4.1-flash` 或 `deepseek-v4.1-flash-ioa` 时，记录 `workbuddy.completed`。退出码 0 后 `status` 才显示 `completed`，`log` 可核正文、实际模型和服务端返回的用量字段。[ACP 结束与型号示例](https://cloud.tencent.com/document/product/1831/140759)
3. WB-SMOKE 的回复正文应为 `WB-SMOKE-618`。`workbuddy.session` 只给出会话 ID。若提交后连接中断，先核对该 ID 在 WorkBuddy 的执行情况，再决定是否续写。派发器不自动重投会话创建或模型请求。Codex 回执继续使用 `turn.completed`。[Codex App Server](https://learn.chatgpt.com/docs/app-server)

## 2026-11-20 冒烟结果

本地回环 HTTP/SSE 验收通过。测试覆盖新建和续写、GET 和 POST 两种 SSE 返回、原任务卡逐字传递、排除会话回放内容、模型与用量回读、错误及缺失型号拒绝、配置错误拒绝、超时取消，以及 Codex CLI 原有完成回执。运行 `python tests/test_workbuddy_dispatch.py -v` 可复跑。

当前主机缺少 WMA 企业 ID、Agent ID、版本 ID 和调用凭据。真实 WorkBuddy 模型执行仍待完成。合成冒烟无项目数据、无真实模型请求，无后台任务。
