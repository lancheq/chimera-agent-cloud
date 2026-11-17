# P01-PACK｜服务器提交工程只读审查

日期：2026-11-17。Task ID：**P01-PACK**；阶段 S0/S2 运行准备；结论 **NOT_READY**。这是独立只读工程审查，不是新机制实验、镜像构建或平台提交。真实临床模型调用 0，训练 0，GPU 模型计算 0，上传/额度消耗 0。没有启动后台任务，无需等待。

本轮只新建 `output/p01_packaging_audit_20261117/` 和本报告。未更改默认配置、源码、冻结 C02、CURRENT/INDEX 或公开 pending；统一发布由父会话处理。本报告没有患者原文、GT、逐例 trace 或密钥。

## 核验范围和文件身份

按 AGENTS.md、运行顺序计划和 P01 任务卡，读取真实 inference/config/run/MCP、打包脚本、收到的 Phase2 验收材料；以 CPU 环境进行导入、配置和已有公开知识资产身份检查。未安装任何软件、未启动模型服务、未请求临床生成。宿主导入证明 `inference.run_agent is chimera_agent_baseline.run.run_agent`，实际模块路径均在当前工作区，而不是冻结目录或备份副本。

完整路径、字节数、SHA256：`output/p01_packaging_audit_20261117/inventory.json`，含 41 个控制/代码文件、77 个可复用资产文件。`host_static_probe.json` 记录实际模块 `__file__`、SHA、配置与清单核验。当前 HEAD 是 `7301642d0eedda2f143ba44b93d42f948249d813`，工作区脏，HEAD 不代表所审第二轮代码全量身份。

| 实际文件（相对 `/home/hedongze/CHIMERA-agent/`） | SHA256 |
|---|---|
| `inference.py` | `af79babd5ac6f61a2135dce4fe445783cad3158e70f0376fb28b23c78a9a3f4e` |
| `src/chimera_agent_baseline/run.py` | `dc81ef01fb7659115c26781208b1a23395528ff26bb827f4a1f24b38769ddd5a` |
| `Dockerfile_Baseline` | `7e11203b7d17e8d1c30e6c8911c0dfbb72a64a9496577bd3df101b785c06ab3c` |
| `configs/config.yaml` | `fd372111531f20183fadc7288527f7616338d069c60a806f2110bfbe8c01b692` |
| `configs/round2_evidence_package_candidate.yaml` | `8cb8f312b92a950d6bf972455791296ef36bcd4b5713280a5470270b3d114e14` |
| `requirements.txt` | `45c01c5f3b0b2d9f87b913a8b788772d014954d7e1a72cb03f8abbbcde9e0e0c` |
| `.dockerignore` | `ddc12125d36cbce47f9afad1a49c927df206778ddc120175840b418f2b7c7368` |
| `output/s2_knowledge_snapshot_20261113_v4/manifest.json` | `13fbd478a3c4f2f3fa2f2fe60a9b48c5c838df097f15eab83077a36fc41bb4da` |
| `output/evidence_package_candidate_20261117/frozen/resources/overlay.json` | `2f61e4232e6b54fcc6a82a289fa73a5083ccf6cb54df62a92dfb5b632f5b43f9` |

## 真实入口和机制可达性

`inference.py:371` 只加载 `/opt/app/configs/config.yaml`，覆写路径和当前 task；不使用 Hydra experiment/defaults 合并 C02 或 shared-plan overlay。`CHIMERA_BASE_URL` 只能改模型地址。真实路径是 socket → 临时 task 树 → `_load_config` → embedding 服务 → `run_agent` → `create_graph` → MCP → form-fill/后处理 → `prediction.json` → `reasoning_align`/平台字段过滤 → 两个 socket。

| 项目 | 已验证的当前行为 | 提交缺口 |
|---|---|---|
| 默认生产图 | `workflow=planned_react`；T1/T2/T3 由 clinical slug 路由 | 这是当前代码事实，不是本审查建议选用的研究架构 |
| 信息权限 | 默认 `information_access.enabled=false` | C02 所需账本/预算/合法可见性链没有开启 |
| C02 | 默认无 evidence_package；opt-in 配置只指向宿主 `output/.../overlay.json` | `.dockerignore` 排除 output；只改 enabled 也不够，缺 run_id、预算/尝试、模型轮数、知识 manifest 及 predictor 禁用契约 |
| C02 正向消费链 | 手工合并原 v2+C02 后 `validate_options` 通过；MCP `search_guidelines` → EvidencePackager → 账本授权 → `verify_messages`/`verify_consumer` 可达代码存在 | 本轮没有运行工具或模型，不能将此静态可达性计为实际已触发/已消费 |
| C02 任务边界 | `legal_context` 明确拒绝 task≠1 | 必须逐任务 profile；T2/T3 不能继承 T1 C02 开关后声称可运行 |
| Shared Plan | `tools/run_shared_plan_candidate.py` 显式调用 `shared_plan.run_pair`；正式 `run_agent` 调用 `create_graph` | 仅把 shared_plan 配置写进 config 不会切换到研究 runner；Fixed/Adaptive/Direct ReAct 的提交选择交规划冻结，本审查不选 |
| predictor/override | 默认 predictor=true、decision_override=true、override_tasks=[2,3] | guarded 模式会拒绝这些选项；不能为了打包自动沿用。T2/T3 权限 profile 尚未确认 |
| Reflection | 默认 self_refine=false | 不因补验收而开启；旧正向叙述不是当下启用证据 |
| 原子核验及后处理 | 默认 atomic_check=true；run/form-fill 有 predictor、决策层、校准和边界逻辑 | C02 所用研究 overlay 是 atomic_check=false。提交 profile 必须明确保持/关闭项与后处理消费，不能把默认与候选混成一个机制 |
| sockets | T1 yes/no、T2 primary token、T3 event/months；T1/T2 reasoning 对象，T3 字符串 | 本轮只读代码；待上传镜像中三任务输入/输出及当前平台 schema 仍需实测 |

`inference.py:423` 无条件启动 embedding 服务；而 `run.py:704` 的本地 guarded 入口会跳过它。guarded 知识检索在 MCP 自己加载 CPU encoder，因此新提交若保留前者可能额外占用 CPU 内存、增加启动时间，且子进程等 `ready` 没有显式超时。应在确定 profile 后做生命周期最小修复与验收，不把宿主两个入口当同一路径。

## 构建、依赖和服务缺口

1. `Dockerfile_Baseline:58` 是 `python3 inference.py`；没有启动 8765 模型服务、等待就绪或回收服务的步骤。安装 vLLM 不会启动 OpenAI HTTP 服务，也不会自动变成 llama.cpp。`configs/experiment/container_qwen.yaml` 所称 `docker/start_server.sh` 本机不存在。
2. C02 不仅需要 `/v1/chat/completions`，还在 `validate_delivery` 请求原生 `/props` 和 `/tokenize`，验证模型名并检查 token 上限。必须验收同一服务的这三个接口；不能仅有 `/v1/models` 或旧 Python FastAPI 服务就报通过。禁用 token 核验以绕过启动缺件不在授权修复范围。
3. 知识 manifest 不是可任意搬动的文件索引：`load_manifest` 校验 corpus/encoder/reranker 全量文件、精确 package 版本和 alignment report；C02 再校验 overlay 的 knowledge_snapshot_id 等于 manifest SHA。原清单经本轮 CPU 只读核验 PASS，绑定 `torch=2.6.0+cpu`、ST=5.7.0、Transformers=4.57.6、Chroma=1.5.9、NumPy=2.4.6、rank-bm25=0.2.2。现 Docker requirements 的 ST=5.6.0、Transformers=5.12.1 与 GPU torch/vLLM 安装链不匹配。现 `chimera` conda 环境也不匹配；只有 `venvs/chimera_access` 通过本轮清单检查。
4. 搬运应在新 staging：固定容器绝对路径 → 绑定实际 corpus/encoder/reranker 文件和最终依赖 → 重新形成 alignment 证据（依赖/文件变更时必须重测几何兼容）→ 新 manifest SHA → 更新新 overlay 的 knowledge_snapshot_id → 新 overlay SHA → 新提交 config/账本绑定。原冻结资源及原 manifest 不改。依赖不变且资源字节不变时可核对复用原 alignment binding，但 report_path 仍须容器内存在；不能仅文本替换 host 路径后跳过验证。
5. 当前 Docker 同时 COPY `resources/`（约4.42 GiB）及 `model/`（约22.85 GiB），检索权重重复，且把 GGUF 写进镜像；入口注释又称权重来自外部 model 包。镜像携带/独立模型包的策略须核对 Mac 和平台当前挂载规则，避免平台模型挂载遮住镜像内资产。可删冗余副本的前提是新 manifest 全量重绑定，不能凭主权重 SHA 相同忽略模块/配置/隐藏缓存文件差别。
6. Dockerfile 使用 base tag、无 digest，升级 pip/wheel/setuptools/uv，额外重装 vLLM；requirements 也不是完整 transitive lock。`pyproject.toml` 可选 vLLM=0.23.0，Docker=0.25.0+cu129。实际解析/安装、CUDA/driver、CPU 指令集/ABI 仍未知。保持 openai 服务路径时 vLLM 是可审查的依赖精简候选，需先核对最新可用 recipe，不改变模型或研究策略。
7. 非 root `USER user` 已存在；尚无真实镜像证据证明 `/tmp`、Chroma 私有副本、日志、输出、模型目录可按要求写/读，或错误时退出状态与 socket 一致。默认宽松 fallback 可能保住 JSON 却隐藏组件失败，工程冒烟应记录/拒绝关键 fallback。

`tools/gc_package.sh` 的 docker save→gzip、版本标签/校验思路可复用；它会移动根目录旧包，实际执行应改为独立 staging/version 输出，保留旧包不动。`tools/verify_submission.sh` 当前默认锁旧 v13 ID/MD5、要求 configs 仅 config.yaml、引用缺失 `.probe_mount`、旧训练/映射路径及 CPU lib 字节大小；不能直接作为本次验收门。`tools/check_submission_imports.py` 仅复制 Python/少量 JSON 后用宿主依赖导入，不是 Docker/CUDA/离线执行验证。

## 可复用资产、收到的 Phase2 材料

| 资产 | 实测大小/身份 | 可复用与限制 |
|---|---|---|
| `model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` | 22,134,528,992 B；SHA `ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61` | 不需重新下载；未测新镜像加载/显存 |
| `model/embedding_model/model.safetensors` | 1,211,486,072 B；SHA `cbf5a78393b6a033e0b8a63a57549964f7ed5c6fbeb4ba0694214f36123f2fd2` | 全套模型目录已逐文件登记，不能只拷主权重 |
| `model/reranker_model/model.safetensors` | 1,112,206,140 B；SHA `ced967c45fd1902eb92716c9ceeca7c95a936770ea9db611f5a841b926e33fbd` | 与 manifest 所绑 resources 目录并非仅靠主权重相同就可互换 |
| C02 frozen resources | overlay+归档原文/摘要，总计约10 MiB | 原字节可复制到独立 staging；仅两个核验原文载体、限定格式，医学 NO_GO 未改变 |
| v4 知识快照 | 48,622,866 B，含 corpus/manifest/alignment | 本轮原路径精确身份检查 PASS；新容器路径/依赖需重绑定 |
| `/home/hedongze/llama-build/bin/llama-server` | 216,565,472 B；SHA `5e8d69056250cb5f263d721fae79bbc7b0b3246b038696493c50ee8155a3ad9a` | 是可复用候选，未直接启动。ldd 绑定本机 cudart12、cublas11、宿主 libstdc++，不能只 COPY 二进制就称便携。证据见 native_server_dependencies.json |

收到的 Mac transfer 位于 `data/round2_materials_20261111/mac_transfer/20261111_101222_bn72g8xr/`。既有两端/服务器 verification 均 PASS（3,431 清单文件、3,432 payload 文件、58,133,528 B）；本轮仅核对这些汇总回执与目录数量，未重新读取全部患者文件做 SHA 传输验收。

两个 release 的当前文件计数均是 T1 195/T2 153/T3 75 个 prompt/clinical，总423；神经表征 T1 191/T2 153/T3 75，总419。两个目录不能当独立患者队列。收到的是训练发布和历史材料，不能认作最新可用镜像/启动脚本或 Mac 新踩坑总账。

`reports/PHASE2_ACCEPTANCE_20261111.md` 的六例真实入口结果可作为历史链路定位材料：使用宿主依赖、8766 服务、T1 v2，明确不是 Docker 验收；headline 特征契约仍失败。该脚本 `tools/phase2_acceptance.py` 会真实调用模型，且准备路径引用宿主 symlink、默认旧选择/配置；本审查没有执行它，下一批必须另冻结有限输入与调用预算，不能作为一键无成本验收直接运行。

## 资源成本与当前执行条件

服务器：Linux x86_64，40逻辑 CPU；约62.4 GiB内存、当时可用42.5 GiB；磁盘可用约131.2 GiB。无 docker、podman、buildah、nerdctl、ctr、apptainer 或 singularity。两张3090各24 GiB，当时已占约21.2 GiB；本审查未停止、抢占或更改现有服务。

本轮成本是 CPU 文件 SHA/元数据检查和导入，不加载临床模型或检索 encoder，不占推理 GPU；峰值 RSS 未测，代理费用未计量。下一步复制本地资产无需下载 GGUF/检索权重。现 recipe 冷构建还需下载 base、vLLM/PyTorch及依赖，精确网络量/时间因构建宿主缓存未知，不能给实测承诺。

空间估算：现 recipe 仅资源 COPY 就约27.3 GiB，另有 base、安装层、构建缓存、docker save 原始流/压缩包；准备80–140 GiB独立空间是容量规划区间，不是测得的镜像大小，现131.2 GiB不足以保证峰值安全。先查实际构建宿主缓存、旧镜像和可清理范围再给准确预算；本轮不安装服务、不删文件、不拉镜像。新的实际 GPU 小冒烟时长必须在相同服务启动/单例吞吐测量后估算，旧六例16分钟不能当新镜像吞吐保证。

## 下一最小实现分工（交父会话安排，不自动派发）

1. **规划冻结 profile**：明确 T1 的真正控制路径/共享规划关系、C02、后处理及预算；明确 T2/T3 权限安全路径与 predictor 禁用/未验收边界。只冻结这批工程提交组合，Reflection 不开，不选 Fixed/Adaptive 研究方向，不调规则、Prompt、阈值或模型。
2. **资产执行**：独立 staging 只复制必要源代码/模板/公开 KB/C02/模型资产，生成路径稳定的新 manifest/alignment/overlay/profile 和逐文件 SHA，验证原 frozen SHA 未变；既有临床 data、GT、audit sidecar 不入 image/model 包。
3. **启动/构建执行**：基于 Mac 新总账核对后的 recipe 固定 llama backend/CPU检索依赖、离线变量、非 root、服务 readiness/超时/失败退出和回收。模型服务必须提供 C02 tokenizer/props 端点，先 CPU/stub 入口再构建实际镜像，记录 image digest 与 export SHA。
4. **独立验收**：同镜像加载导出包后，T1/T2/T3 三接口、C02正向/不触发/未知/拒绝/错误/消费分别查证；无外网、只读输入/模型、可写输出，关键失败不悄悄产默认结果。通过才排有限真实模型小冒烟及一次 validation 提交清单。平台当前阶段/资源/剩余次数单独核实；本轮不执行。

## 具体验收命令与完成标准

以下是下一实施合同，**未执行**。`${STAGING}`、`${IMAGE}`、`${CASE_DIR}`、`${OUT_DIR}`、`${MODEL_DIR}` 是待冻结路径/标签；不把这些占位符或尚不存在的文件当交付。测试代码可复用 frozen tests 的断言，但新入口三任务夹具、机制事件和容器清单探针仍需实施。

```bash
# 构建宿主只读检查，先落实空间/缓存与 GPU 预算
command -v docker
docker info
docker system df
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv
df -h "${STAGING}"

# 独立 profile/资源验收应在最终镜像依赖内执行；不沿用宿主通过结论
docker build --platform=linux/amd64 --file "${STAGING}/Dockerfile" --tag "${IMAGE}" "${STAGING}"
docker image inspect --format '{{.Id}} {{.Config.User}} {{json .Config.Entrypoint}}' "${IMAGE}"
docker run --rm --network=none --entrypoint python3 "${IMAGE}" -c 'from chimera_agent_baseline.knowledge_access import load_manifest; load_manifest("/opt/app/resources/knowledge/manifest.json",require_alignment=True); print("KNOWLEDGE_IDENTITY_PASS")'

# 同一镜像实际入口：每任务一个GT盲合法夹具；先stub验证，再按已冻结预算用真实模型
# 输出目录先由执行者创建并授予镜像非root用户写权限；输入/模型均只读
docker run --rm --network=none --gpus device=0 --mount "type=bind,src=${CASE_DIR},dst=/input,readonly" --mount "type=bind,src=${MODEL_DIR},dst=/opt/ml/model,readonly" --mount "type=bind,src=${OUT_DIR},dst=/output" "${IMAGE}"

# 导出到独立版本路径并重新load验收，不覆盖根目录历史包
docker save --output "${STAGING}/image.tar" "${IMAGE}"
gzip -k "${STAGING}/image.tar"
sha256sum "${STAGING}/image.tar.gz"
docker load --input "${STAGING}/image.tar"
```

`/opt/app/resources/knowledge/manifest.json` 是建议的容器合同路径，尚未建成；若冻结另一布局，命令和 manifest 必须一同更新。GPU device=0 只是命令模板，不能据此占用本服务器正在服务的 GPU。原 v4 manifest 的 CPU依赖精确检查必须在镜像内 PASS；服务 readiness 还须在容器内验证 `/props`、`/tokenize`、`/v1/models`，以及一例模型请求/工具调用，避免宿主 endpoint 假通过。

完成标准是：明确 profile；新资源全量SHA与旧件保全；实际镜像和导出包同 digest；三任务合法 socket；C02 至少一项正向执行并被消费、正常不触发与未知可区分；身份错/越权/服务缺失/tokenizer失败按合同停止；实际离线非root输入/输出权限通过；聚合机制日志能在临时目录清理后留存为测试证据，且不额外定义平台 socket。无模型 stub 与真实模型、宿主与容器、镜像上传与正式提交分账。任一入口仍未接通、任何宿主路径残留、服务启动缺失、T2/T3 profile未冻结或没实际构建，均为 NOT_READY。

## 必须等 Mac 新踩坑记录核对的决定

`/Users/lancheq/Desktop/项目汇总/Chimera-agent/docs/CHIMERA-agent_踩坑总账_20261117.md` 在本服务器不可用，本审查**未读**。下列事项不能从9月旧注释决定：最新成功 Dockerfile/entrypoint/llama构建版本和指令集补丁、Mac Docker/远程构建环境及可用空间/缓存、最新可复用 image/model tar 的版本和摘要、镜像与独立模型包职责、当前非root/写目录/socket schema踩坑、上次可用平台 GPU/内存/时间资源配置及第二轮阶段/配额记录。即使 Mac 总账含历史成功，也要在本次冻结镜像重验，不直接授予 PASS。

目前独立可做的只读准备已完成；没有证据表明已具备实际构建环境，更没有上传/平台回执。下一步按 profile 与工程缺口实施，不重复开展大范围文献检索或全库提分调优。
