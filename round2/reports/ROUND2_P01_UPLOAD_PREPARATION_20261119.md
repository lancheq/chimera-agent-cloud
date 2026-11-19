# P01-UPLOAD-PREP · 构建与官方上传前置调查

2026-11-19，北京时间。**只读调查已交付，正式构建与上传仍待放行。** 上一轮构建路径已经从已验传的 Mac 台账定位。服务器的 v15 镜像包和独立模型包均在，当前候选仍缺可用 builder、目标 phase 合同及同一镜像／模型配对的离线验收。首次正式 validation 还须完成 B 正式验收。

## 本轮新增可核对证据

- **上一轮 builder 有直接台账。** 11 月 11 日传输批次中三份 Mac 文档的 SHA-256 均与原清单一致。《Phase2提交包清单》记载 v15 用 `docker buildx build --platform linux/amd64 -f Dockerfile_Baseline_nb2 -t chimera-agent:v15 --load .`；《执行手册》给出 Colima default Docker socket、Mac `.submission_repo` 和 arm64 VM 构建 amd64 的路径。9 月原日志记录 Apple M5 arm64 和 `docker-container` driver。现时 Mac builder 版本、路径可用性及宿主／VM 余量仍 UNKNOWN。11-17 踩坑总账在授权可读范围未找到原件或同名副本。 传输清单及证据表
- **服务器归档定位完成。** `chimera-agent-v15-20261105.tar.gz` 为 6,349,228,960 B，`model-phase2-20261105.tar.gz` 为 21,703,790,075 B。较早的同日镜像包另有文件，旧手册中的名称不能直接沿用。模型归档的 `gzip -t` 来自历史台账，v15 的镜像 ID／OCI manifest digest、两包当前全量 SHA、实际归档成员身份及跨平台大包传输回执仍待核。GGUF 与 native server 已有 A-G2 全量 SHA，本轮沿用其证据并核对现场 stat。当前 Linux x86_64/glibc 2.35/AVX2、同一卷约 132 GiB 可用，未发现可核对容器构建器。 归档与现场矩阵
- **T2 predictor 本地身份可绑定。** 服务器 9 月 26 日版为 726,861 B，SHA-256 `47ace1a8009a3d65ab901656c62963ecc329b8df6c6bf8e7c06b7e2a62d92ec6`，与同目录 `.bak_pre_p14` 相同。Mac 旧台账区分约 24 KB 老版本；旧包所称 727 KB 成员的 SHA 未见。纳入由 D03/D05 另裁，批准纳入需验证实际成员、依赖、合法披露后特征及结果写回。T2 宿主 11-11 的执行链与特征契约已有分开结果。 离线配对及另裁项 · [11 月 11 日复验](PHASE2_ACCEPTANCE_20261111.md)
- **二轮官方信息给出候选范围。** Awards 确认 2026 年 11 月第二轮。历史 Mac 手册的 11/15–12/15 为 provisional，指向的二轮公开路径本轮受 403 限制。9 月本地留有三次 Development 汇总、一次 Test 提交及 Active v13 的旧回执。二轮 phase slug/ID、开放状态、进程式可用性／invoke／batch、GPU/CPU/RAM、时限及实际账号额度仍 UNKNOWN。通用 exec 仍受支持，batch 由 phase 启用。Try-out 的每 unique image 5 次编辑器调试和赛事公开 5 次 validation／1 次 test 是分别记录的规则。 目标 phase 与账号矩阵 · [CHIMERA Awards](https://chimera-agent.grand-challenge.org/awards/) · [GC batch 公告](https://grand-challenge.org/blogs/november-2026-cycle-report/) · [GC Try-out](https://grand-challenge.org/documentation/try-out-your-algorithm-and-publish-a-test-case/) · [CHIMERA 提交](https://chimera-agent.grand-challenge.org/submission/)

## 目标离线验收的最小路径

按目标 phase 决定是否保留进程式入口；若要求 invoke，复用现有 Socket 核心并验证初始化和跨例隔离。独立 Models 通路获确认后，将经验证的 GGUF 与需要的模型目录放入单独 tarball，在 `/opt/ml/model/` 挂载。现有 11-05 模型包来自 11-11 裸目录补齐检索权重之前，需以成员证据确认能否复用。用同一候选冻结 SDK、RAG/C02 manifest／overlay、非 root 启动和资源权限，记录 image ID、model member SHA、归档 SHA 及有效 profile。 离线 staging 步骤与命令 · [GC 模型挂载](https://grand-challenge.org/documentation/upload-the-model-weights-separately/) · [GC exec 兼容](https://grand-challenge.org/blogs/july-2026-cycle-report/)

阶段证据已分层。9 月留有 glibc/AVX2、A10G、断网、MCP 与平台 Socket 的旧回执。A-G2 有固定 35B/GGUF 的 3090 宿主启动、SDK 精确计数和三个任务的双 Socket，T3 旧真实结果含已定位的输入污染。A-T3-INPUT 证明修复后合成分支，A-F06/G2-CASE-PILOT 由原执行／规划流程独立验收。新的目标离线矩阵覆盖 ABI、AVX2、35B/CUDA、冷启动限时、无外网、MCP/RAG/SDK、非 root、三个任务六个文件的名称和目标 JSON Schema、输入拒绝及失败清理。 [A-G2 宿主证据](ROUND2_A_G2_ADAPTIVE_HOST_SMOKE_20261118.md) · [T3 修复验收](ROUND2_A_T3_INPUT_UPLOAD_PLANNING_REVIEW_20261119.md) · 目标回归矩阵

## 最小阻断及决策责任

| 门槛 | 能决策者 | 最小补证与资源 | 停止条件 |
|---|---|---|---|
| **可用 builder 与资产身份** | Mac／builder 持有人和《研究规划》 | 已有 builder 实例、可复用 v15 recipe／base 摘要、宿主／VM data root 容量和缓存；能读到 11-17 总账时核对相关段落。优先用台账；大包扫描等存储时窗批准。 | 无已有可用 builder、容量预算超额、缺基础依赖或证据不能绑定大包时，停在对应阶段。 |
| **二轮 phase 与账号** | 赛事组织方／GC 管理员确认 phase。团队账号持有人确认角色、版本和余额。 | 一份带日期的脱敏 phase/账号回执或组织方确认：phase slug/ID、窗口和时区、exec/invoke/batch、接口/schema、GPU/CPU/RAM/scratch、启动及运行时限、Models 通路、Try-out／validation／test 可用及扣费规则。 | phase 或按钮性质不清即停在公开资料核对；未确认硬件时不冻结 CUDA 配置；无账号／额度证据时不消耗机会。 |
| **生产 profile 与离线配对** | 《研究规划》逐任务裁定；原《主会话》作为生产执行者；指定 builder 和目标等效环境持有人落实资源。 | D01–D07、A-F06 与 pilot 独立验收、T2 predictor 纳入与否、C02/B 范围、SDK 和实际容器回执。获批后使用一次独立 staging 与目标规格试验，先 stub 再单列有限真实模型预算。 | profile 缺权威、资产／依赖错版、ABI/权限/Schema 失败、预算或资源超限时停。旧实现不得临时顶替待验机制。 |
| **首次正式 validation** | 《研究规划》及团队提交持有人 | 同一 image/model/profile 的离线验收签字，B 正式范围验收（时间资格、可信截止、迟到原文本隔离、T3 时间零点及实际消费），并确认 phase 资格、余量和一次操作的预算。账号／上传动作按各自授权另审。 | 任一前置缺失、Try-out 和正式额度无法确认，或 active 配对与验收不符，停在提交前。 |

本轮只新增本报告和本任务输出目录中的三个交付。使用静态读、轻量 stat、小文件 SHA 和公开查询；真实生成、GPU 使用、构建、安装、批量下载、大文件整文件哈希、跨端传输、平台账号操作和上传均为 0。A-F06/G2-CASE-PILOT 不依赖本调查完成。无本任务后台作业，无需等待。 机器可读交接
