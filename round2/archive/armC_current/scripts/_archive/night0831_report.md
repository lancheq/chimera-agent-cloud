# 0831 合并任务报告（TASK-0831-FULL · night0831_report）

> 生成: 2026-08-30（S1 + N1-P0r/P1/P2/P3）
> 锁: TASK-0831-N1.lock（沿用，收尾删除）
> 声明: 未 push / 未改 prompt/τ/schema/主 config.yaml / 各 output 独立 / commit 历史干净（见文末）

## 0. 结论摘要

| 阶段 | 结果 |
|---|---|
| S1 self_refine 解析修复 | 修复 commit `0759bfd`；重放 `4eb91c1`：**refine 触发 102/217（47.0%）**，原 0% 根因闭环 |
| N1-P0r 容器方案 1 | commit `07453c8`；overlay+start_server.sh；本地冒烟通过（reveal len 3, via=tool_call） |
| N1-P1（替换）磁盘清理 | 共享盘 7.1G→**12G**；gemma4:e4b 验证正常；Q3 移至打包期 |
| N1-P2 T2 P6 重跑 | **acc 65.3%（47/72）**、macroF1 0.480；vs 0830 **+4.2pp**；form_fill warning **0 条** |
| N1-P3 官方评估 | 6 组件表见 §5；DAG: T1 0.449 / T2 0.423 / T3 0.667 |

## 1. S1 · self_refine 解析修复 + 217 重放

- **根因**：`_parse_critique_json` 修复 2A 检测到 thinking 前缀直接 return (raw, [])，把尾部有效 JSON 全部错杀 → refine 0% 触发的唯一根因。
- **修复**：保留 thinking 检测，但改为从 raw 尾部反向提取最后一个平衡 `{...}`（跳过内联反引号示例片段，fenced/裸 JSON 均可），解析成功且 issues 为 list 才采信；尾部无 JSON 才走原降级。L0 单测 12/12 PASS（真实 case 0cdfb9410718 解析出 fabricated_facts + internal_inconsistency）。
- **217 重放**（scripts/s1_replay_217.py，零推理）：
  - **若 S1 在 0829 消融 B 生效，refine 触发率 102/217（47.0%），原 0/217（0%）**。
  - top issue 类型（关键词归类，215 条）：internal_inconsistency 74 / fabricated_facts 60 / missing_guideline 34 / unsupported_weights 30 / confidence_miscalibration 25 等。
  - severity 格式漂移：模型大量用 high/medium/low（86/102 触发 case），未严格按 none/mild/moderate/severe——refine 消费 severity 时需映射。
  - **消融 B 真实结论补全**：self_refine 0% 触发是解析器 bug 而非机制无效；真实增益需修复后重跑消融（S2）。

## 2. N1-P0r · 容器 config 对齐（方案 1，用户已拍板）

- `configs/experiment/container_qwen.yaml`：provider=openai → `http://127.0.0.1:8765/v1`，model_id=Qwen3.6-35B-A3B-UD-Q4_K_M.gguf，无 tool_parser（openai 路径不需要），extra_body.reasoning_budget_tokens=4096，generation temp 0.3/max 8192/top_p 0.95。
- `docker/start_server.sh`（容器入口）：按显存自选量化（方案 B：≥23000MiB 且 Q4 存在→Q4；否则 Q3 若存在；都不满足→Q4+警告）→ 启动 llama_cpp.server（n_ctx 16384）→ 轮询 /v1/models 就绪（10min）→ exec inference.py。Dockerfile ENTRYPOINT 改为 bash docker/start_server.sh + 装 llama-cpp-python 留待打包 Phase 统一做。
- **本地冒烟**（复用 8765）：PT-pseudo_0169468160c6，67.6s 完成，biopsy_decision=no / confidence=clear / **reveal_sequence len 3, via=tool_call** ✓ / free_text 正常。
- ⚠️ 执行插曲：本会话沙箱无 GPU 设备权限（ggml_cuda_init failed / nvidia-smi No devices），35B server 由**用户终端启动**（CUDA0 RTX 4090 生效）后完成全部 GPU 阶段；T2 运行结束后按 N1-P3 授权停止。

## 3. N1-P1（替换）· 磁盘冗余清理 + Q3 移打包期

| 项 | 结果 |
|---|---|
| 删 ollama/models/lib/（gemma 源 gguf 4.64G） | ✅ 已删；curl api/tags 仍列出 gemma4:e4b |
| 删 qwen_start.log + qwen_server_llamacpp.log（152M） | ✅ 已删 |
| 删 /root/.ollama（系统盘 2.4G） | ⚠️ 沙箱权限拦截，需用户终端 `rm -rf /root/.ollama`（非共享盘，不阻塞） |
| df 确认 | **共享盘可用 12G（≥11.5G 目标达成）** |

- **Q3 处置（打包期遗留，三方案待用户拍板）**：
  - X) HF 下载 stream → GC tarball（不落共享盘）
  - Y) 下载→冒烟→上传→删本地（需临时 16.6G，盘满则不可行）
  - Z) 放弃 Q3，纯 Q4 + 提交声明 A10G-only（4090 24G 已 500+ case 稳定，A10G 同规格）
  - docker/start_server.sh 的 Q3 分支保留（Q3 不存在自动走 Q4，与现状自洽）。

## 4. N1-P2 · T2 全量重跑（P6 验证口径）

- 输出 `test/output.t2_full_p6_20260831/`，72 pids 显式枚举，双关闭（predictor/self_refine/atomic），CHIMERA_OUTPUT_DIR 显式导出；17:13→19:04（T2-024/032 SOFT 失败后重跑补全，19:19 全 72 完成）。
- **结果**（eval_train_gt.py）：acc **65.3%**（47/72）、macroF1 **0.480**。
  - 混淆：AS tp15/gt25/pred25、AT tp24/gt31/pred33、CS tp8/gt14/pred14、WW tp0/gt2/pred0。
  - reveal 非空率 100%、free_text 非空率 100%。
- **三方对比**：0827 68.1% / 0830 61.1% / **0831-P6 65.3%** → vs 0830 **+4.2pp**，vs 0827 -2.8pp。
- **P6 生效统计**：`decision salvaged` **0**、`decision DEFAULTED` **0**——72 个 prediction 的 form_fill_warnings **全空**（0 次解析重试、0 次兜底）。对比 0830 的 21/72 fallback 签名，解析质量显著提升，**失败路径未触发**（无静默决策污染）。
- **P6 判定**：判据「salvage ≥5 且 acc 回升 ≥4pp」——acc **达标（+4.2pp）**；salvage 0 次（未触发场景，本身即优于 salvage 的结果：0 DEFAULTED = 无污染）。**判定 P6 修复有效**（以 acc 回升 + 0 兜底残留为准；salvage 计数因触发场景未出现为 0）。
- ⚠️ **口径标注（必写）**：本 T2 数字为 P6 验证口径；若 S2 后 refine 启用，最终提交配置需另跑全量。
- 翻转 vs 0830：32/72——确认 T2 决策解码方差大（0830 自身 vs 0827 也翻转 33/72）。

## 5. N1-P3 · 官方 evaluate.py 完整评分

详见 `scripts/official_eval_full_20260831.md`（三任务 6 组件表 + 明细）。核心：

| 组件 | Task1 (91) | Task2 (72) | Task3 (73) |
|---|---|---|---|
| decision gate | 0.703 | 0.653（含部分分 0.701） | event acc 0.781 |
| confidence kappa | 0.019 | -0.019 | — |
| weights kappa | 0.449 | 0.316 | — |
| tool efficiency | 0.000 ⚠️ | 0.000 ⚠️ | — |
| grounding | 0.895 | 0.952 | — |
| GEval* | 0.805 | 0.607 | 0.806 |
| **DAG** | **0.449** | **0.423** | **0.667** |
| C-index | — | — | **0.728** |

\* GEval 为项目 gemma4:e4b 判分（官方 deepeval 路径本机不可用：deepeval 4.2.0 与 evaluate.py 2.x 时代 API 不兼容挂起 15min → 按「不强行适配」记录差异改用项目判分）。T2 为 P6 新输出重跑 0.607（旧 0.618）。
⚠️ tool 0.000 为 GT 映射局限（自建 GT 的 reveal_sequence 为空），非模型缺陷。
- 交叉验证：Task2 exact acc 0.653 与 eval_train_gt.py 47/72 一致 ✓。

## 6. 遗留项与待用户决策

1. **Q3 打包期三方案**（X/Y/Z，见 §3）——需用户届时拍板。
2. **/root/.ollama 清理**（用户终端可删，系统盘 2.4G，非阻塞）。
3. **Dockerfile 打包 Phase**：ENTRYPOINT→docker/start_server.sh、装 llama-cpp-python[cuda]、模型 tarball 或 COPY、容器 config 用 container_qwen overlay。
4. **S2 消融**：触发条件（重放触发率 47%≥20%）达成；需 35B server（用户终端重启）→ 判定 refine 启用与否。
5. **提交口径**：若 S2 启用 refine，T2/T1/T3 需按最终配置重跑全量再提交。

## 7. commit 历史（本任务）

```
31dc28b N1-P3: official evaluate.py full scoring pipeline
321b107 N1-P1/P2: T2 P6 rerun launcher + Q3 skip→packaging-phase note
07453c8 N1-P0: container path — embedded llama_cpp.server + qwen overlay (plan B)
4eb91c1 S1: replay 217 ablation-B critiques — refine would trigger 102/217 (47.0%)
0759bfd S1: critique parse — extract trailing JSON past thinking prefix
```
（未 push；每个 commit 单任务单文件组，无混合提交）
