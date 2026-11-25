# P01 打包交付规划验收

2026-11-20，北京时间。本机规划核验。**Mac Colima 的预检镜像构建交付通过书面验收。正式推理入口、同配对 GPU 离线验收和平台就绪维持 HOLD。** 候选镜像为 Mac Colima 中可引用的 Docker 对象，服务器保存已核验的交付报告和机器回执。官网字段由用户转交现有 Agent。[官网核对任务卡](../discussion/tasks/20261120_P01_OFFICIAL_PHASE_EVIDENCE.md)

## 已验收的构建交付

- 报告和机器回执均为 0600。报告 SHA-256 `2ee24d537efd07a1c6525f4eba795784c44183cf732b760c401b7a84d8439013` 与交付值吻合。两份回执和用户确认的镜像标识一致。实际 Colima 对象未由本会话直接读取。 [镜像构建报告](ROUND2_P01_IMAGE_BUILD_20261120.md) · [机器回执](../output/p01_image_build_20261120/handoff.json)
- 镜像标签 `p01-prod-candidate-v1:staging-20261120`，配置 digest `sha256:2e791f690d5410dba723abda4118331b7a6b0c80a09ffae5a1ea6212ca8a0f3c`，manifest digest `sha256:ee6629be6a10fb7517881b48c47e343a71371c2edaf624738feff72da0585921`。回执记录 `linux/amd64`、UID 10001、118 项 wheel 锁、依赖导入和容器内 CUDA 用户态库解析。入口进程确实启动，停在预检所需的 `/input` 缺挂载。以上为构建侧证据。

| 验收项 | 本地回读 | 判定 |
|---|---|---|
| 构建上下文 | 重算服务器 staging 的 66 项文件与聚合 SHA-256，49 项运行源码与候选锁吻合。JUnit 记录 13 项宿主检查通过。Mac 有效 Dockerfile、最终镜像应用层、wheel 锁全文仍待小型回执对齐。 | staging 通过，同镜像复现证据待验 |
| AVX2 后端产物 | `/home/hedongze/llama-build-avx2/bin/llama-server`，216,436,864 字节，SHA-256 `90eae07a64a015f252637aa1ec99dcb077ac03c6280bfe3058a5db3408764799`。构建日志达 100%，x86-64 ELF；CMake 显示 AVX2 开、AVX512 和 native 关，CUDA 架构 86。 | 编译产物身份通过，镜像内可移植性待验 |
| 依赖与配对 | 与镜像回执中同 SHA 的服务器 ELF 依赖 `libgomp.so.1`、`libcudart.so.12`、`libcublas.so.11`、`libcuda.so.1` 和系统库。记录中的「静态单文件」限内部组件链接，系统和 CUDA 仍为动态依赖。镜像报告覆盖用户态库，驱动注入及实际 35B 冷启动无回执。 | 编译与用户态依赖有据，目标 GPU 配对待验 |
| 镜像大小 | 机器回执记录压缩层合计 4,830,439,151 B（4.83 GB）及本地大小 15,139,717,120 B（约 14.1 GiB）。实际 `docker save | gzip` 归档尚无，准确导出大小和目标 phase 限制待核。 | 已知镜像元数据，上传体积合规待验 |
| 入口与配对实测 | `ENTRYPOINT` 为 `offline_preflight.py`，正式 phase adapter 和受信 profile 待绑定。还需同镜像绑定模型 SHA、挂载位置，实测 Linux NVIDIA 驱动、官方 MCP、T1/T2/T3 双 Socket、预算与失败清理。历史宿主 Socket 证据随阶段登记。 | 正式候选 HOLD |
| 目标 phase 和账号 | 运行模式、资源和时限、模型挂载、容器归档限制及可见额度待当前官网证据。 | 人工转交，未运行官网核对 |

[原 staging 报告](ROUND2_P01_OFFLINE_STAGING_20261119.md) · [原配对清单](../output/p01_offline_staging_20261119/pairing_manifest.json) · [AVX2 编译日志](/tmp/p01_static.log)

## 待回的小型证据

Mac 执行者可提供按标签定位、带配置 digest 的 `docker image inspect` 回执、manifest digest 的原始来源、最终 Dockerfile 与 `requirements.lock`、镜像内应用与配置的指纹，以及容器检查原始短日志。将镜像内库的指纹与各资产来源一并绑定。此阶段直接引用现有 Colima 镜像，节省一次约 5 GB 的导出。达到等效 GPU 主机搬运或平台上传阶段后，按实际使用的不可变配对只导出一次，并记录归档大小和 SHA-256。若正式 phase adapter 改变了镜像，给新镜像独立 digest 并对新配对重新验收。B、T3 数值和平台动作继续按既有裁定。
