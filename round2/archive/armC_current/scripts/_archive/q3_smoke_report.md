# Q3_K_M 冒烟报告（N1-P1）

> 生成: 2026-08-31（TASK-0831-FULL）
> 状态: **SKIPPED（未执行）**

## 结论

**Q3_K_M 本次标记为「未验证回退档」，容器暂只携带 Q4_K_M。**

方案 B（Q4+Q3 双量化）按用户已拍板维持，但本地冒烟被两个环境阻塞：

| 阻塞项 | 详情 |
|---|---|
| GPU 沙箱权限 | 本会话沙箱禁止访问 /dev/nvidia*（ggml_cuda_init failed / torch cuda=False / nvidia-smi No devices），Q3 冒烟需要 GPU 推理，无法在沙箱内执行；已交由用户终端启动 server 后补 |
| 磁盘空间 | Q3_K_M 需 16.6GB，共享盘仅剩 ~7.1GB（删 ollama 4.7GB 重复副本后 ~11.8GB，仍差 ~4.8GB）→ 用户已拍板跳过下载 |

## 遗留待办（GPU + 磁盘就绪后补）

1. 下载 `unsloth/Qwen3.6-35B-A3B-GGUF` 的 `Qwen3.6-35B-A3B-UD-Q3_K_M.gguf`（~16.6GB）到 model/
2. 3-case 冒烟（T1×2 + T3×1），与 Q4 已有输出对照：
   - decision 一致率 ≥2/3 → Q3 可用回退档
   - <2/3 → Q3 弃用
3. 若通过 → 容器 tarball 携带 Q3，start_server.sh 的显存自选逻辑（≥23000MiB→Q4，否则 Q3）即生效

## 影响

- 本次 Docker 提交配置：**仅 Q4_K_M + A10G 风险声明**（Q3 未验证）。
- docker/start_server.sh 已含 Q3 分支逻辑（Q3 文件存在时自动启用），未来补测通过后无需改脚本。
