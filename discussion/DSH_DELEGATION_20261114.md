# Codex → DSH 外部执行试验（2026-11-14）

本机 Linux 安装独立 DSH 0.2.2-rc.1、Node 24、Bun 和 Our Free Model 2.8.3；headless profile 配置 provider `our-free-model`、model `deepseek-v4.1-flash-free`。插件安装后默认仍是官方模型，需单独设置。当前清单未确认用户所称 1017 与此 ID 的对应关系；配置不能证明上游权重版本。

在无项目数据的临时目录，真实网络调用返回 `DSH_SMOKE_OK`、退出码 0；另一调用创建并回读 15 字节的 `hello.txt`，退出码 0，并由主 Agent 独立检查。仅证明简单调用和文件写入，不证明复杂任务质量或生产可靠性。首次加载遇到 npm 包名与内部模块名不一致，安装相同版本的 `dsh-our-free-model` npm 别名后解决。出口需要本机 HTTPS 代理。

## 使用

先人工准备不含患者原文、GT、逐例轨迹、密钥和其他受限内容的独立目录；只复制任务必需的公开文件。主 Agent 拆分任务并写清允许修改文件、验收要求及禁止提交推送，再调用：

```bash
python tools/round2_dsh_delegate.py /绝对路径/已筛选目录 --mode workspace-write <<'TASK'
在此填写具体任务、允许修改的文件和验收命令。不要提交或推送。
TASK
```

默认是 `read-only`；有修改需求时显式使用 `workspace-write`。调用后主 Agent 复核文件差异和测试，必要时最多两轮修复；仅将人工审核通过的变更移入原项目。输入和工具结果会发送到第三方网关，绝不将原项目直接交给它。入口拒绝本项目及其祖先／子目录，清理继承环境变量；这不是操作系统隔离，DSH 进程仍以当前用户权限运行，理论上能访问其他路径。不得据此宣称阻止数据泄漏；对有敏感数据的主机，正式使用前应使用独立受限账户或容器与最小权限挂载。

这是外部进程，不是 Codex 原生子 Agent；无常驻服务，调用结束后不自动继续或通知。安装及 profile 位于用户目录，不含在公开代码快照中。参考：[DSH headless 官方说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/bundle/headless/README.md)、[插件说明](https://github.com/Ebony-Vinyl/dsh-our-free-model/blob/main/README_EN.md)。
