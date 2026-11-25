# P01 候选镜像构建回执

2026-11-20，北京时间。记录 `P01-PROD-CANDIDATE-V1` 的本地候选镜像。**镜像实体在 Mac 的 colima 虚拟机里，不是服务器上的文件**，服务器上只有本回执和 AVX2 编译产物。

## 1. 镜像身份

| 项 | 值 |
|---|---|
| 标签 | `p01-prod-candidate-v1:staging-20261120` |
| 镜像 ID（config digest） | `sha256:2e791f690d5410dba723abda4118331b7a6b0c80a09ffae5a1ea6212ca8a0f3c` |
| manifest digest | `sha256:ee6629be6a10fb7517881b48c47e343a71371c2edaf624738feff72da0585921` |
| 压缩后大小（manifest 合计） | 4,830,439,151 B = **4.83 GB** |
| 解压后占用（`docker images` 显示） | **14.1 GB** |
| 层数 | 15 |
| 平台 | linux/amd64 |
| 用户 | 10001:10001 |
| 创建时间 | 2026-11-20T16:00:50+08:00 |

两个大小都要看。平台的上传限制针对压缩包，我们的 4.83 GB 低于 10 GiB；14.1 GB 是解压后占磁盘的量，不作为上传依据。v15 的对应数字是 6.37 GB 压缩 / 18 GB 解压，本轮更小。

## 2. 位置

镜像在 **Mac** 的 colima 默认 VM 里，端点是 `unix:///Users/lancheq/.colima/default/docker.sock`。

服务器 `/home/hedongze/` 下没有任何镜像文件。本轮没有执行 `docker save`，所以没有 tarball 可寻。这是当前"服务器定位不到镜像"的唯一原因，不是产物丢失。

查询方式（在 Mac 上）：

```bash
docker --context colima image inspect p01-prod-candidate-v1:staging-20261120 \
  --format '{{.Id}} {{.Os}}/{{.Architecture}} {{.Size}}'
```

导出成文件（需要时执行，约 10 分钟、5 GB）：

```bash
docker --context colima save p01-prod-candidate-v1:staging-20261120 | gzip -1 > \
  /Users/lancheq/WorkBuddy/2026-11-19-20-22-45/_p01_build/p01-prod-candidate-v1-staging-20261120.tar.gz
```

## 3. 基础镜像

| 项 | 值 |
|---|---|
| 引用 | `pytorch/pytorch:2.9.1-cuda12.6-cudnn9-runtime` |
| config digest | `sha256:28fb54912f51b59d80f696da9e551ac1da601ce761195f5ff8dd59dccdcd1516` |
| 平台 | linux/amd64（本地已有缓存，未重新拉取） |
| Python | 3.11.14 |
| 内置 torch | 2.9.1+cu126 |
| 内置 packaging | 25.0 |

## 4. 运行时包

| 文件 | 大小 | SHA256 |
|---|---|---|
| `llama-server` | 216,436,864 B | `90eae07a64a015f252637aa1ec99dcb077ac03c6280bfe3058a5db3408764799` |
| `libcublas.so.11` | 159,871,304 B | 取自系统 CUDA 11.5 |
| `libcublasLt.so.11` | 338,326,488 B | 取自系统 CUDA 11.5 |
| `libcudart.so.12` | 707,904 B | CUDA 12.4 redist |
| `libgomp.so.1` | 298,776 B | 取自 Ubuntu 22.04 gcc 运行时 |

`llama-server` 与服务器 `/home/hedongze/llama-build-avx2/bin/llama-server` 逐字节一致。指令集审计：`zmm` 寄存器 **0 处**、`ymm` **17,176 处**，即无 AVX512、保留 AVX2。

`libgomp.so.1` 是构建后补的：基础镜像把它精简掉了，缺它二进制直接起不来。

## 5. 依赖闭包

118 个包，194 MB，全为 wheel（两个源码包已转 wheel）。

| 项 | 值 |
|---|---|
| 锁定文件 | `requirements.lock`，118 条，全带 SHA256 |
| 锁定文件 SHA256 | `1b8e715a2cf8cdad8950c2c286d3370c52d647af7877f2cdfcb19e583592ed70` |
| 含 torch | 否（继承基础镜像） |
| 含 nvidia-* | 否 |

生成方式：在基础镜像的系统环境里做 `pip install --dry-run --report`，让 pip 按"torch 已满足"算出真实增量，避免把整套 CUDA 轮子拖进来。

## 6. 验证矩阵

| 检查 | 结果 | 证据 |
|---|---|---|
| 镜像架构 | 通过 | `linux/amd64` |
| 非 root | 通过 | `id -u` = 10001 |
| 压缩体积 | 通过 | 4.83 GB < 10 GiB |
| Python 依赖导入 | 通过 | chromadb / langchain / sentence-transformers / sklearn / langgraph / mcp 全部 `IMPORTS_OK` |
| 容器内二进制指纹 | 通过 | `90eae07a…`，与审计版一致 |
| CUDA 库解析 | 通过 | cudart / cublas / cublasLt 均从 `/opt/p01/lib` 命中 |
| 入口脚本可执行 | 通过 | 报错在 `/input` 未挂载，属预期 |
| `libcuda.so.1` | **未验证** | 驱动库由运行时注入，Mac 无 NVIDIA GPU |
| 真实模型加载 | **未验证** | 需 GPU 环境 |
| 三任务 Socket 形状 | 未在本镜像内跑 | 沿用 11-19 的宿主证据（三任务各两 Socket 通过） |

## 7. 与上一轮的差异

| 项 | 11-19 staging | 11-20 实际镜像 |
|---|---|---|
| 镜像 | 未产出 | `p01-prod-candidate-v1:staging-20261120` |
| llama-server | `5e8d6905…`，7126 处 AVX512 | `90eae07a…`，0 处 |
| 链接方式 | 未确认 | 静态单文件，216 MB |
| 依赖 | 未准备 | 118 包 / 194 MB，离线可装 |
| venv | 隔离 | `--system-site-packages`，继承 torch |
| 入口 | 预检脚本 | 预检脚本（未变，仍缺 phase adapter） |

## 8. 缺口

1. **`libcuda.so.1` 无本地证据。** 该库必须由 NVIDIA 容器运行时注入，Mac 上不存在。模型能否真正加载，只能在有 GPU 的 Linux 上验。
2. **入口仍是预检脚本。** `ENTRYPOINT` 指向 `offline_preflight.py`，只做环境自检。接病例推理需要 phase adapter，受信注册表仍为空。
3. **未导出 tarball。** 目前只有 docker 镜像对象，没有 `docker save` 产物。需要上传时再导。
4. **目标 phase 未知。** 运行时限、实例内存规格、模型上传入口三项仍待平台侧确认。

## 9. 复现

```bash
cd /Users/lancheq/WorkBuddy/2026-11-19-20-22-45/_p01_build
docker --context colima build --platform linux/amd64 \
  --build-arg P01_BASE=pytorch/pytorch:2.9.1-cuda12.6-cudnn9-runtime \
  -t p01-prod-candidate-v1:staging-20261120 \
  -f context/Dockerfile context/
```

上下文 925 MB（源码 48 MB + wheelhouse 194 MB + runtime 683 MB）。缓存命中时增量重建 7 秒，全量 3 分 56 秒。
