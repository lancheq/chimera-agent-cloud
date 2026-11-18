# Codex会话创建与消息通道诊断/恢复

2026-11-17，北京时间。结论：当前规划会话的会话创建与消息发送均已取得真实成功回执及目标会话回读。没有运行CHIMERA工程、临床模型或平台操作。

## 逐层证据

| 层级 | 实际核查结果 |
|---|---|
| 工具能力 | 存在mcp__codex_app__create_thread、send_message_to_thread；另有codex_app原生list_projects/list_threads/read_thread。两类通道分别验证，不以读取成功推定MCP写操作成功。 |
| 历史原始错误 | 从原规划rollout提取6次完整传输错误：4次send_message、2次create_thread。不是只有消息失败记录；原始返回保存在output/mcp_session_diagnostics_20261117/historical_mcp_errors.json。 |
| 原生读取 | 项目、线程列表、已有主会话读取成功；当前原有主会话已完成P01-MECH-G0，等待规划，不再是在运行中。未给它发送诊断消息。 |
| 旧端点 | 127.0.0.1:55924 TCP实际返回errno111/Connection refused，无法到达HTTP/MCP处理层。历史工具错误未提供HTTP状态码，不虚填401/403/429。 |
| 新端点 | 127.0.0.1:60680 TCP可达。无凭证GET /mcp返回401；这只是该无凭证探针的结果，不能写成用户认证失效。随后实际带认证的MCP创建/消息成功。 |
| 本地服务状态 | CLI及已有app-server均0.167.0，daemon正常运行；用户config.toml无静态mcp_servers条目，不能靠硬编码旧地址修复。 |
| 刷新 | 22:11通过官方app-server的config/mcpServer/reload完成MCP配置刷新，result={}；未停止或重启daemon/模型，无全局配置文件修改。刷新后能读取codex_app的4个工具及bearerToken认证状态，并观察到daemon与60680的已建立连接。 |
| 创建真实验收 | 22:12:38，create_thread返回isError=false，threadId=01a1e9da-451c-7113-8b39-1fbf5c93b801，host=graduate3090。目标为此前已获用户授权的B01，首轮仅读任务卡和AGENTS、回复B01_READY，不运行分析。 |
| 消息真实验收 | 向上述B01发送单次带nonce的通信探针，send_message返回isError=false；目标新一轮真实回复MCP_ACK_20261117_2212，wait_threads回读completed。 |

## 定位与边界

可确认的直接断点是旧loopback MCP端点不可达、对应传输worker已关闭。当前《研究规划 (2)》分叉会话经刷新后使用可达端点，创建与发送均通过。端点变化与客户端重连/重启后遗留旧连接缓存相符，但没有Mac端事件日志，故不能把具体触发操作及刷新单一因果写成已证实；本轮也未在刷新前重试一次有副作用的创建操作。

这不支持“Codex不支持新建会话”的结论；当前已实际创建。旧错误也不是权限拒绝/参数拒绝/Plus额度错误的回执。后续任务仍可能受额度影响，当前未测剩余额度，不保证足够。SSH四文件传输的自动审批拒绝属于另一条链，本轮不重传、不绕过、不声称已修复。

修复操作只涉及当前连接刷新；未证明所有旧的闲置会话都会永久免于同类端点失效。再次出现时先比较端点和实际连接，再按官方机制刷新，不批量重复创建，不修改安全认证、不转发猜测端口、不以另一套执行方式绕开原通道。若需要重启整个后台服务，须先处理在途任务；本轮无需这样做。

## 官方依据与执行方式

使用[OpenAI Docs技能](/home/hedongze/.codex/skills/.system/openai-docs/SKILL.md)。官方[App Server协议](https://learn.chatgpt.com/docs/app-server)提供config/mcpServer/reload和mcpServerStatus/list，并说明Unix socket采用WebSocket升级。按现有0.167.0生成的schema核对请求后，通过现有用户控制socket调用这两个管理接口；不调用原始RPC代替用户会话创建/消息工具，不开新app-server。

[官方MCP排障](https://developers.openai.com/plugins/deploy/troubleshooting)要求分层检查服务/端点和认证，并保存日志。本轮保留完整原始工具错误、配置刷新回执、端口探针、创建/消息回执与目标回复。[后台服务命令说明](https://learn.chatgpt.com/docs/developer-commands)提示整服重启可能中断其他任务，本轮未使用。

两个最初以JSONL连接app-server proxy的只读管理探针未取得响应；查官方Unix/WebSocket说明后改用正确传输，管理调用成功。未把探针超时解释成服务宕机，没有安装新依赖。

## 交付及当前任务状态

证据目录：output/mcp_session_diagnostics_20261117/。包含历史完整错误、transport_probe、mcp_refresh_result、刷新后status、create_thread_result、created_thread_settings、creation_readback、send_message_result及send_message_readback。

B01会话已创建并通过两轮接收/通信验收，模型指定gpt-6.1-sol、high；仅进行了任务卡读取及精确回执，66条分析和自动Goal均未启动，当前idle。B02本轮未创建。P01-G0已交付但尚未由本轮规划验收，G1/G2没有启动。

本轮无患者读取、GT读取、临床生成、GPU任务、训练、生产源码修改、模型服务变动、打包、上传或平台提交。诊断证据仅本地，未公开同步。无后台诊断或研究任务需要等待。
