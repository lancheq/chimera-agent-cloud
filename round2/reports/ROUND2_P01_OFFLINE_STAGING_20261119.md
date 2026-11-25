# P01-OFFLINE-STAGING · 实机、资产与候选镜像

2026-11-19，北京时间。内部记录，私有。把 `P01-PROD-CANDIDATE-V1` 的独立 staging 建起来，核对旧归档身份，跑通宿主侧能跑的全部检查。**没有产出镜像 digest**：这台服务器没有容器运行时，也没有可用的 builder。

## 1. 结论

候选的源码侧已经闭合：49 份运行时文件与锁定清单逐件一致，context 干净可构建，宿主 13 项检查全部通过，三个任务的 Socket 形状符合本地契约。

构建侧全部停在门外。服务器上 `docker`、`podman`、`buildctl` 都不存在，常见 socket 也没有；Mac 直连被 `Operation not permitted` 挡住，phase 回执没有到手。因此 base 镜像 digest、`requirements.lock` 的 wheel 来源、实际镜像 ID 都是 UNKNOWN，不能填。

T3 保持数值 HOLD。候选没有引入 60 的默认值，也没有删字段或绕过校验；`6-60` 这个范围提示按冻结源码原样保留，它的诱导效应归入 T3 数值研究，不在本轮改动。

## 2. 现场与 phase 回执

服务器侧实测（`field_receipt_pending.md` 记录原件）：

| 项 | 实测 | 判定 |
| --- | --- | --- |
| 系统 | Linux x86_64，glibc 2.35 | 满足 native runtime 的 GLIBC 2.34 |
| CPU | 原生 AVX2/FMA 与 AVX512F | 满足 |
| 磁盘 | 工作区与 /tmp 共用 /dev/sda2，可用 141,655,334,912 B | 约 131.9 GiB |
| 容器运行时 | PATH 无 docker/podman/buildctl，常见 socket 不存在 | **阻断** |
| Mac 直连 | 读取会话返回 `[Errno 1] Operation not permitted` | **缺口** |
| phase 环境 | 无证据 | UNKNOWN |

请求已交给 Mac 持有人，等待落到 `output/p01_offline_staging_20261119/builder_phase_receipt.md`。该文件目前不存在，属于预期：服务器无法代为转交。

二轮 phase 的 ID、运行模式（exec/invoke/batch）、实例规格、VRAM、scratch、时限、模型安装路径、账号可用操作，全部按 UNKNOWN 登记。9 月旧档不作为推断依据。

## 3. 旧归档身份

两份归档都用单次串行流式读取，边读边算 CRC 与 SHA，没有解包到临时目录，也没有复制。全程 1 个 CPU 作业。

**v15 镜像**（6,349,228,960 B，sha256 `6d2369fc889c1abe6702411b12ecd479dbb7e75a79ad1a475ff9b5efe4a414b9`，读取 38.4 秒）：

- RepoTag `chimera-agent:v15`
- config digest `sha256:ba8e714a909030fb6ffd167fd3db291cedc45227b6dd70ee68f89bc527c565e9`
- 平台 `linux/amd64`，29 层，36 个归档成员
- `user=user`，入口 `["bash","/opt/app/docker/start_server.sh"]`
- 归档内另有一个 `architecture=unknown/os=unknown` 的 config 对象（`b4e465b6…`），属 manifest list 成员，不是可运行镜像

v15 只作 ABI、入口和构建形状的对照。它的代码层不进入候选。

**模型归档**（21,703,790,075 B，sha256 `500ddb0f…`，读取 319.5 秒，66 个成员）：

- 主成员 `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`，22,134,528,992 B，sha256 `ac0e2c11…`，与裸文件逐字节同摘要
- 附带 `embedding_model/`、`reranker_model/`，以及 `predictor/` 下 5 个 joblib

**predictor 资产必须排除**。候选裁定 D03 关闭 predictor，归档里的 5 个 joblib 一个都不进挂载清单。归档原件不动。

**native runtime**：`/home/hedongze/llama-build/bin/llama-server`，216,565,472 B，sha256 `5e8d6905…`。`-march=native` 编译，286 条 fatbin，ELF 需要 GLIBC 2.34 / GLIBCXX 3.4.30 / CXXABI 1.3.13。`libcuda.so.1` 由目标驱动提供，`libcublas.so.11` 与目标 SM 仍需现场核对。可移植性状态 `PENDING_ACTUAL_TARGET_ABI_AND_ISA`。

**冻结 RAG**：manifest 原址 `13fbd478…`，可迁移版 `2d783285…`，alignment binding `c3aaefd1…`，成员计数 corpus 8 / encoder 13 / reranker 27，宿主校验 PASS。

## 4. 候选配对

`P01-PROD-CANDIDATE-V1` 的单一定义写在 `pairing_manifest.json`，与 `configs/p01_prod_candidate_v1.lock.json` 一致。

源码 49 份逐件核对：`inference.py` `d7afd779…`、`reasoning_align.py` `bc2c8483…`，加 `src/chimera_agent_baseline/**/*.py` 47 份，全部等于锁定值，0 差异 0 缺失。

配置差异是裁定落地，不是污染：`atomic_check=false`、`predictor.enabled=false`、`decision_override=false`、`decision_override_tasks=[]`、`information_access.enabled=true`、`budget_units=3`、`max_attempts=6`、`shared_plan.control_interface_version=2`、`max_model_turns=5`。路径已切到容器内 `/input`、`/output`、`/opt/app/resources`、`/opt/ml/model`。

镜像标签 `p01-prod-candidate-v1:staging-20261119` 已预留，**当前无 digest 可记**。

## 5. context 与指纹

`context/` 66 个文件，49,368,267 B（47.1 MiB），聚合指纹 `d395df8955af15acca6209994089d769e45320b205170ee87f7d7f1bddf0ffb9`，逐件清单在 `verification/context_fingerprint.json`。

构成：49 份运行源码、`Dockerfile`、`offline_preflight.py`、`resources/knowledge`（含 manifest、vector_compatibility、chroma 库与语料）、`configs/config.yaml` 与候选 lock、prompt 模板、`.dockerignore`。

不含：病例、GT、逐例 trace、per-case 许可、私有试跑启动器、内网 supervisor、`.git`、AppleDouble、凭据。`case_pilot.py` 与 `case_loader.py` 在源码树里，是候选冻结的组成部分，不是试跑材料。

## 6. 宿主验证

**13 项全部通过，281.58 秒，墙时 4:43，峰值 RSS 510,112 KB，单核 95%。**

覆盖：三任务 good 路径、错误 profile、预算耗尽、表单缺失、错误工具、合成 purpose、夹具篡改、评价文件混入输入、T3 源字段扩展、投影篡改、T3 预算耗尽。

三个 good 用例的产物直接用于 Socket 形状检查，三任务各两个文件全部通过，`t3_numeric_hold=true` 写在回执里。T1 的 decision 摘要 `6c76a933…` 与真实首批运行相同，T2 的 `65cbc205…` 也相同；T3 因合成值是 25.0，摘要 `5afbc629…` 与真实 18.0 的 `41fde3ce…` 不同，符合预期。

### 6.1 这次修复的两个问题

**第一，9 项失败到 13 项通过。** 上一轮留下的 `host_run_retry` 是 9 failed / 4 passed，根因不是代码：pytest 的 rootdir 插入把项目根放到了 `context` 前面，`reasoning_align` 因此从仓库根而不是 staging 导入，撞上 `Imported module path mismatch: reasoning_align`。冻结 profile 校验导入路径是有意设计，不能放宽。加 `verification/tests/conftest.py` 把 `context` 与 `context/src` 固定到 `sys.path` 最前，重跑即 13 项全过。同一份源码在仓库根跑是 81 项通过，在 staging 跑失败，差异只可能来自导入顺序。

**第二，context 混入了运行残留。** 在 `context/` 里跑测试会就地落 `.chimera_run.json`（4,216 B，`status=failed`，带本机 pid 与 `/tmp` 路径），并留下 12 个 `__pycache__/*.pyc`。这些都会被打进镜像。已删除，并新增 `context/.dockerignore` 排除同类文件；指纹在清理后重算。这一条是上一轮没有的，属本轮实测发现。

## 7. 阻断与缺证

按能否继续推进分三类。

**A 类，无 builder（硬阻断）。** 没有容器运行时就没有 base 镜像 inspect、没有 `requirements.lock` 的真实 wheel 身份、没有 `docker buildx build`、没有 image ID 与 OCI manifest digest。附带的离线容器检查（非 root、`/input` 只读、无外网、MCP stdio、错误清理、跨例隔离）全部做不了，因为它们需要在容器里跑。

**B 类，phase 回执缺失。** 二轮的目标 phase ID、运行模式、实例规格、scratch、时限、模型安装路径、账号操作，缺一项就不能确认候选与目标平台配对。这些只能由现场持有人填。

**C 类，验收依赖前两类。** 一次 35B 实例的冷启动、真实模型离线加载、三任务各 12 次发送的真实离线验收（36 次 / 9 单位 / 585,216 预留 token / ≤30 分钟 GPU 墙时），要等 A 和 B 都清了才能排。上线前还要规划确认可用显卡和新生成预算。

## 8. 资源与用量

| 项 | 数值 |
| --- | --- |
| 归档流式读取 | 28.05 GB，357.9 秒 CPU 36.5 + 304.1 秒，单作业 |
| context 生成与清理 | 66 文件，47.1 MiB |
| 宿主验证 | 4:43 墙时，281.58 秒测试，峰值 RSS 510,112 KB，单核 |
| 形状检查 | 3 次，各 <1 秒 |
| 新增下载 | 0 |
| 模型请求 | 0 |
| GPU 作业 | 0 |
| 平台操作 | 0 |

后台活动：无。所有 ssh 会话已结束，无残留进程。

## 9. 工件索引

路径相对 `output/p01_offline_staging_20261119/`。

**本轮新增**

- `pairing_manifest.json`：模型／镜像／RAG／挂载／开关的单一来源
- `verification/context_fingerprint.json`：66 件逐文件 SHA 与聚合指纹
- `verification/host_run_fixed2_203840/`：13 项通过的 junit、pytest 日志、资源回执
- `verification/socket_shapes/task1.json`、`task2.json`、`task3.json`
- `verification/tests/conftest.py`：导入顺序修复
- `context/.dockerignore`：残留排除规则
- `fingerprint_context.py`：指纹重算脚本

**上一轮实物，本轮核对后保留**

- `v15_archive_receipt.json`、`model_archive_receipt.json`、`host_asset_receipt.json`
- `build_and_test.md`、`Dockerfile.staging`、`context/Dockerfile`
- `prepare_context.py`、`check_host_assets.py`、`check_socket_shapes.py`、`offline_preflight.py`、`read_legacy_archives.py`
- `field_receipt_pending.md`
- `verification/host_run_retry/`：修复前的 9 失败记录，保留作对照

**构建配方**

`build_and_test.md` 里的命令保留原样。实际执行时 `P01_BASE`、`P01_WHEELS`、`P01_RUNTIME` 三个变量需要在 builder 上填真实值，`--network=none`、`--pull=false`、`linux/amd64` 三项约束不变。

## 10. 下一步

1. Mac 持有人执行回执卡，把 `builder_phase_receipt.md` 写到约定路径。最低需要：Docker/Buildx driver、`linux/amd64` 能力与一次最小 build 探针、基础镜像 `repo@sha256` 且已在本地 store、构建盘可用空间。
2. 拿到 base 后，在本机或 builder 生成 `requirements.lock`，逐件对 wheel SHA。
3. base 与 wheel 齐了再执行构建，记录 image config ID 与 OCI manifest digest，两者都要能回读。
4. 镜像可用后做 A/B 两段离线检查，最后才谈真实模型冷启动与有限生成。

平台上传、正式 validation、生产晋级都不在本轮授权内。
