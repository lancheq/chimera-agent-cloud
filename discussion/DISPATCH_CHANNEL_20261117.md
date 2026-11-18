# 派发通道迁移：codex_app MCP → codex-dispatch CLI

2026-11-17。适用于 CHIMERA-agent 全部跨会话派发。原作者保留，旧通道作废。

## 2026-11-18 最新入口

`codex-dispatch` 现在默认连接当前主机上桌面端使用的 Codex app-server。客户端通过 `$CODEX_HOME/app-server-control/app-server-control.sock`（默认 `~/.codex`）使用官方 App Server 协议。已有会话可以由同一 app-server 继续，无需释放桌面持有的写入权。[Codex App Server](https://learn.chatgpt.com/docs/app-server)

```bash
codex-dispatch new --name "<标签>" --cd <工作目录> --prompt-file <任务卡>
codex-dispatch send <会话UUID> --cd <工作目录> --prompt-file <任务卡> --goal-file <Goal文件>
codex-dispatch read <会话UUID> --limit 3
codex-dispatch status <dispatch-id>
```

- `new` 在桌面保留原会话 ID 并设置标题和置顶。 `send` 等待目标空闲后追加一轮。同一会话的 CLI 派发由脚本互斥。桌面端手动输入与派发器之间没有共同的客户端锁。
- `--goal-file` 可选。派发器写入原生 Goal，同时把目标文字随任务卡交付。 `turn.completed` 是该派发轮回执；原生 Goal 的结果通过 `read` 查询。派发器保留未结束的不同 Goal，并在回读缺失时明确报错。
- `read` 返回最近对话、分页游标和原生 Goal，无需恢复目标会话。用 `--cursor` 接续读取历史。
- app-server 不可用时，派发会保留失败日志；保守处理已提交但连接丢失的请求，根据日志里的会话与轮次读回核对。 `--backend exec` 保留旧操作，适用于无需桌面可见性的独立 CLI 会话。
- 已安装的入口为 `~/.local/bin/codex-dispatch`，App Server 客户端位于 `~/.local/share/codex-dispatch/app-server.py`。重装时将本目录两个源文件分别安装至上述路径，并在 `~/.local/share/codex-dispatch/venv` 安装 `websockets==15.0.1`。

[公开版省略逐例记录；汇总与机制结论保留。]

| 验证 | 派发回执 |
|---|---|
| 新建会话，桌面置顶列表回读标题与同一 UUID | `d20261118132640-3e031d` |
| 桌面 app-server 持有目标写锁时续写，同一会话复述首轮标识 | `d20261118132708-e36218` |
| 通过 `--goal-file` 写入原生 Goal，轮后读回有效目标和计量 | `d20261118132832-fdebe3` |
| 目标有未结束轮次时自动等待，再发起独立轮并取得完成回执 | `d20261118133035-6e0059` |

另验证了 `read` 的对话和分页。验收结束清理了测试 Goal 和测试置顶。

下文保留第一版 `exec` 通道的排障与验收记录。

## 结论

跨会话派发改用 `codex-dispatch`（封装官方 `codex exec` / `codex exec resume`）。
`create_thread` / `send_message_to_thread` / `fork_thread` / `handoff_thread` 一律停用。

## 为什么旧通道必须废掉

旧工具走 `codex_app` 这个 MCP，transport 是 HTTP，URL 形如 `http://127.0.0.1:<port>/mcp`。三个特征凑成一个死局：

1. **端口是随机的**。桌面端每次启动重新分配。日志里先后出现过 `55924`、`60680`，现在活着的是 `51342`。
2. **会话把端点固化了**。会话创建那一刻的 URL 会被写进上下文。服务器 rollout 里 `127.0.0.1:55924/mcp` 的出现次数：22:02 规划会话 53 次、22:12 会话 4 次、22:19 会话 2 次。
3. **app 重启就失效**。22:42 app 重启换端口后，22:02 那个会话的所有派发永久指向死端口，报 `HTTP 127.0.0.1:55924/mcp Transport send error`，没有 threadId 回执。

所以"改成当时活着的端口"这种修法只能撑到下一次重启。B01/B02 那次成功，是因为它们恰好创建在 60680 可用的时间窗内。

## 新通道

`~/.local/bin/codex-dispatch`（源码：本仓库 `discussion/codex-dispatch.sh`）

通过 `~/.codex` 下的会话存储和登录态运行。它不连接桌面端的临时 MCP 端口，模型调用仍需要连通模型服务。脚本还需要 Python 3、`setsid` 和 `flock`。

```bash
# 续写已有会话（等价 send_message_to_thread）
codex-dispatch send <thread-id> --cd <工作目录> --prompt-file <任务卡>

# 新建独立会话（等价 create_thread）
codex-dispatch new --name "<标签>" --cd <工作目录> --prompt-file <任务卡>

# 查看回执
codex-dispatch status <dispatch-id>
codex-dispatch log <dispatch-id> -f
codex-dispatch list
```

CHIMERA-agent 后续运行默认 `gpt-6.2-sol` / `high`。可显式传入 `--model gpt-6.2-sol --effort high`，使回查命令时可直接看到两项选择。
默认沙箱 `workspace-write`、审批 `never`，可用环境变量 `CODEX_DISPATCH_SANDBOX` / `CODEX_DISPATCH_APPROVAL` 覆盖。

运行产物落在 `~/.codex-dispatch/`。每次请求保存 CLI 日志 `logs/<id>.log`、后台进程 PID `logs/<id>.pid`、原始任务卡 `logs/<id>.prompt` 及退出码 `logs/<id>.exit`。派发台账保存于 `registry.jsonl`。

## 回执判定（硬性）

- `{"type":"thread.started","thread_id":"..."}` = **会话已建立**，不等于任务完成。
- 新请求在 CLI 正常退出且日志有真实顶层 `turn.completed` 事件时，`status` 显示 `completed`。旧日志保留按事件核查的能力。
- 没有完成回执就退出、出现 `turn.failed` 或退出码非零时，`status` 显示 `failed`。查看对应日志定位失败原因。

## 旧 → 新对照

| 旧调用 | 新命令 |
|---|---|
| `create_thread(model=gpt-6.1-sol/high, prompt=...)` | `codex-dispatch new --name "..." --cd <dir> --prompt-file <card>` |
| `send_message_to_thread(threadId=..., prompt=...)` | `codex-dispatch send <thread-id> --cd <dir> --prompt-file <card>` |
| `fork_thread(...)` | `codex-dispatch new` + 任务卡里说明继承哪份证据 |
| 靠工具返回值判断成功 | `codex-dispatch status <id>` 看 `turn.completed` |

## 已知限制

- **2026-11-18实测补充：idle不代表释放写入者。** 对原主会话的CLI resume返回 `thread-store conflict: ... already has an active writer`，未产生thread.started/turn.completed。只查UI的idle、无codex exec进程，不能保证桌面app-server或其他客户端已释放该thread。应由现有持有者接续，或先释放该thread的客户端占用后再send；不删除锁/数据库、不并发重试同thread。CLI避开旧MCP端口，不保证绕开线程写入互斥、额度或模型服务故障。
- **索引可写不等于桌面可见。** 2026-11-18原生list_threads最近50项未找到手工登记的01a1ea18测试thread；这不是全历史或UI刷新后的验证，仍不能宣称侧边栏注册通过。维持服务器dispatch-id/日志管理，暂不批量改session_index。

- `exec` 新建thread按服务器dispatch-id/log管理。此前“手工追加session_index可使其出现在侧边栏”的判断已被用户后续实测撤回：追加无效、测试行已清除，app继续维护该文件。禁止再手工制造索引条目；文件JSON合法不代表UI导入成功。
- `send` 续写已有 thread 正常，app 内可见新 turn。
- `resume` 没有 `-C` 参数，工作根由进程 cwd 决定，所以 `send` 必须显式给 `--cd`。
- 同一派发目录下，脚本按 thread UUID 对 `send` 互斥，第二笔并发请求直接拒绝。退出失败后先根据回执排查原因。Codex 仍会独立检查桌面端及其他客户端占用的写入权。

## 排障

| 症状 | 原因 | 处理 |
|---|---|---|
| `No prompt provided via stdin` | prompt 未正确接入 stdin | 用 `--prompt-file`，脚本已修正重定向 |
| `Not inside a trusted directory` | 缺 `--skip-git-repo-check` | 脚本已默认带上 |
| `state: failed` 且日志只有一行 | 参数或路径错 | `codex-dispatch log <id>` 看原文 |
| app 默认列表看不到会话 | `codex exec` 会话记录为 `exec` 来源，默认列表只枚举交互来源 | 先用 `status <dispatch-id>` 获取 UUID，再用桌面端的置顶操作加入侧边栏 |

## 桌面端查看 CLI 派发

2026-11-18 在 `graduate3090` 实测，默认 `thread/list` 未返回 G1A、A02 和派发冒烟会话。显式查询 `sourceKinds: ["exec"]` 后三个原 UUID 均可查到，桌面端按 UUID 也可以直接读取原历史。[OpenAI App Server 的 thread/list 说明](https://learn.chatgpt.com/docs/app-server#list-stored-threads)

需要在桌面中查看时，派发会话仍通过 `codex-dispatch status <dispatch-id>` 核对回执。取得 UUID 后，通过桌面端工具 `move_thread_to_sidebar_section` 在对应 SSH 主机上将原会话移至 `pinned`，并用 `list_threads` 回读。需要可辨认的名称时，用 `set_thread_title` 将派发标签写为桌面标题。置顶与命名是桌面操作，无需修改索引、重新派发或启动模型轮次。用户可从「置顶」打开原会话。

已回读桌面置顶列表中的 `P01-G1A 公共运行时` 和 `A02 Reflection路径只读专项`。冒烟会话用于验证后已移出置顶。

## 验收记录

2026-11-18 北京时间，桌面会话通过本机已安装的 `codex-dispatch` 发起隔离冒烟，模型为 `gpt-6.1-sol/high`。

| 操作 | dispatch-id | 回执 |
|---|---|---|
| 11:53 新建 | `d20261118115313-a3b3e1` | `turn.completed`，退出码 0 |
| 11:53 续写 | `d20261118115347-e21905` | 同一会话，`turn.completed`，退出码 0，准确复述第一轮的测试标识 |

会话 UUID 为 `01a1ecc9-8a80-7b53-a23c-e915db7b1280`。使用模拟 CLI 另验证了同一 UUID 大小写变体的并发拒绝、退出后释放派发锁、原样保存任务卡、失败退出码，以及含完成字样的普通消息不会被当成完成事件。用原主会话的历史失败日志验证了写入冲突提示，无重投。

2026-11-17 23:20 真实调用（非 mock）：

- `new` → thread `01a1ea18-d206-7011-9339-388ae743ddc6`，`thread.started` 带出 id，回复"收到"，14798 in / 5 out，`turn.completed`。
- `send` → 同一 thread，输入涨到 29624（确认带上了历史上下文，不是新会话），回复"确认"，`turn.completed`。

## 回退

- AGENTS.md 备份：`AGENTS.md.bak.20261117_2327`
- 索引备份：`session_index.jsonl.bak.20261117_2327`
- 回退脚本时保留 `~/.codex-dispatch/`，供既有任务查回执。
