# Codex 规划派发到 WorkBuddy

2026-11-20。Codex 负责规划、写任务卡和验收。个人桌面版用 `--backend workbuddy-desktop` 生成交接卡，在 WorkBuddy 选择模型并执行，由 Codex 导入结果。托管版用 `--backend workbuddy` 调用企业版托管智能体（WMA）的 OpenAPI 和 ACP。[托管接入](https://cloud.tencent.com/document/product/1831/140759) · [桌面助理 Open API](https://open.workbuddy.cn/docs/openapi)

## 个人桌面版

用户提供的 WorkBuddy 5.11.3 截图确认模型选择器有内置 DeepSeek-V4.1-Flash，显示 1017。Codex 当前在 SSH 远端 Linux，WorkBuddy 在用户电脑。任务卡和结果文件可以手动传输，也可以放在双方都能访问的目录中。本路径直接使用已登录的 WorkBuddy 桌面端。[桌面端新建本地任务](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Task-Bar)

```bash
codex-dispatch new --backend workbuddy-desktop --name WB-DESKTOP-SMOKE \
  --cd /home/hedongze/CHIMERA-agent \
  --prompt-file /home/hedongze/CHIMERA-agent/discussion/tasks/20261120_WORKBUDDY_DISPATCH_SMOKE.md

codex-dispatch status <dispatch-id>
codex-dispatch receive <dispatch-id> --result-file /绝对路径/结果.json
codex-dispatch log <dispatch-id>
```

- `new` 将任务原文和回执模板写入交接卡，输出 `card` 路径。默认目录为 `~/.codex-dispatch/desktop-outbox`，可用 `CODEX_DISPATCH_WORKBUDDY_DESKTOP_OUTBOX` 指向已有共享路径。仅把获准交给 WorkBuddy 的任务卡放入该目录。
- 桌面端新建任务，选 **DeepSeek-V4.1-Flash**，粘贴卡片或作为文件交给 WorkBuddy。将它回传的 JSON 保存为文件，供 Codex 导入。可以直接将 JSON 粘贴回当前 Codex 对话，由 Codex 完成导入。
- `receive` 核对派发编号、任务卡 SHA-256、返回状态和正文。成功回收显示 `returned`，模型证据以该轮 WorkBuddy 界面为准。冒烟预期 `result` 为 `WB-SMOKE-618`。

重新安装 CLI 时，把 `discussion/codex-dispatch-workbuddy-desktop.py` 安装为 `~/.local/share/codex-dispatch/workbuddy-desktop.py`。

## 托管版配置

托管路径需要一个配置为 DeepSeek V4.1 Flash、已启用并可通过公网 API 访问的 Agent 版本。个人桌面助理公开 API 的请求格式未提供逐轮模型锁定和型号回执。[桌面助理 Open API](https://open.workbuddy.cn/docs/openapi)

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

## 托管版派发和验收

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

个人桌面版文件交接通过回环验收。覆盖任务原文与指纹、错误指纹拒绝、重复导入、不同结果覆盖拒绝和模型证据标记。真实冒烟交接卡 `d20261120150433-a0b807` 已生成，当前等待桌面结果。用户截图只用于确认模型可选。

本地回环 HTTP/SSE 验收通过。测试覆盖新建和续写、GET 和 POST 两种 SSE 返回、原任务卡逐字传递、排除会话回放内容、模型与用量回读、错误及缺失型号拒绝、配置错误拒绝、超时取消，以及 Codex CLI 原有完成回执。运行 `python tests/test_workbuddy_dispatch.py -v` 可复跑。

当前主机缺少 WMA 企业 ID、Agent ID、版本 ID 和调用凭据。真实 WorkBuddy 模型执行仍待完成。合成冒烟无项目数据、无真实模型请求，无后台任务。
