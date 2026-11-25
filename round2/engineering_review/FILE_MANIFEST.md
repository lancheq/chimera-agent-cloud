# FILE_MANIFEST — round2 源码同步清单

同步时间：2026-11-25  
服务器仓库：`/home/hedongze/CHIMERA-agent` @ `master` / `606b6df1aee39a5563a8c374a1c96a1300852c48`  
GitHub 基线：`main` @ `6c2cc540c51fda549dd6be24f4f364c74f653ecd`  
同步范围：`src/chimera_agent_baseline/` 全部 `.py/.json/.yaml/.j2`（56 个文件，与服务器逐文件 SHA256 一致）

## 校验结论

- 服务器 56 个文件 ↔ GitHub 同步后 56 个文件，**SHA256 全部一致**。
- 同步前 GitHub 为 44 个：新增 12 个，内容更新 12 个，其余 32 个本已一致。

## 新增文件（12）

| 文件 | 作用 | SHA256(前12) |
|---|---|---|
| `agent/b12_evidence_state.py` | B12 实验证据状态表示（缺失/未知/冲突） | `b7cec70a391c` |
| `agent/evidence_reconciliation.py` | 证据冲突识别与协调 | `c7e4485f5390` |
| `agent/longitudinal/__init__.py` | 包导出 | `82a078f6e3eb` |
| `agent/longitudinal/estimate.py` | 趋势估计 | `2fbe5996e601` |
| `agent/longitudinal/extract.py` | PSA 事件抽取 | `131bf647bc78` |
| `agent/longitudinal/inject.py` | 注入模型输入 | `cd34da7c42a2` |
| `agent/longitudinal/isolate.py` | 来源隔离 | `14920136e465` |
| `agent/longitudinal/reconcile.py` | 跨源协调 | `939b5081fbd0` |
| `agent/longitudinal/render.py` | 证据文本渲染 | `c68ba5ff3bc4` |
| `agent/longitudinal/schema.py` | 数据结构定义 | `ba2e5fb9e4d6` |
| `case_pilot.py` | 病例试点/筛选 | `b824a99d0b21` |
| `host_smoke.py` | 单机真实环境工程验证（含模型/权重哈希指纹） | `7391ece388d1` |

## 内容更新文件（12）

| 文件 | GitHub 旧 SHA256(前12) | 服务器新 SHA256(前12) |
|---|---|---|
| `agent/form_fill.py` | `ca240dd46a40` | `a8fc998ba8c0` |
| `agent/graph.py` | `dfed6d6cbbe6` | `dcbe03351954` |
| `entry_profile.py` | `5a062df6b8f3` | `c4715941ca1b` |
| `guarded_runtime.py` | `123159ef56f3` | `19b456831cec` |
| `information_access.py` | `93d1ced1d39e` | `ccf849c0d338` |
| `mcp_server.py` | `f2356f5f1f5a` | `8d9e20f1bbd4` |
| `models/__init__.py` | `0968a6ace364` | `70ac2af2d0ff` |
| `models/openai_compat.py` | `251c17eff4fa` | `bddce54d7a7a` |
| `models/request_meter.py` | `408d26f9f798` | `233110ce3e1a` |
| `run.py` | `1ff321452e5f` | `953edf26a870` |
| `runtime_audit.py` | `33644de90c20` | `cc6057c5038c` |
| `shared_plan.py` | `71d153d14685` | `1c85e28ac7c0` |

## 安全审查记录

推送前对 24 个文件做敏感模式扫描，结果：
- 密钥/令牌/私钥：**0 处**
- 内网地址/内部端点：**0 处**
- 真实病例数据/真值内容：**0 处**（`run.py` 中 `_GT_FILES` 仅为官方数据集文件名常量，不含路径或数据）
- 已知注意项：`host_smoke.py:14` 硬编码本机路径 `/home/hedongze/...`，属构建环境事实而非凭据，保持原样以便环境可复现。


## 配置与根级文件同步（第二批）

| 文件 | 状态 | 说明 |
|---|---|---|
| `configs/experiment/round2_react.yaml` | NEW | 第二轮 ReAct 主实验预设 |
| `configs/experiment/qwen_local.yaml` | NEW | 本地 llama.cpp Q4_K_M 推理预设（127.0.0.1:8765） |
| `configs/experiment/qwen_api.yaml` | NEW | 第三方聚合平台 Qwen（`api_key_env: LLMAPI_KEY`，**key 不入库**） |
| `configs/experiment/container_qwen.yaml` | NEW | 容器内 Qwen 预设 |
| `configs/experiment/school_ollama.yaml` | NEW | 校内 Ollama 预设 |
| `configs/experiment/school_ollama_smoke.yaml` | NEW | 校内 Ollama 冒烟预设 |
| `configs/experiment/{t1,t2,t3}_override.yaml` | NEW | 三任务参数覆盖 |
| `configs/p01_prod_candidate_v1.lock.json` | NEW | P01 生产候选锁（含源码哈希、预算与晋升门） |
| `Makefile` | NEW | Round 2 缺，补入 |
| `.dockerignore` | NEW | Round 2 缺，补入 |
| `inference.py` | UPDATED | 与服务器一致化 |
| `AGENTS.md` | UPDATED | 实验执行约定最新版 |

校验：上述 10 个配置与 4 个根级文件已与服务器 SHA256 逐一比对通过。

### 配置侧安全结论

- 9 个 experiment 预设中 8 个指向 `127.0.0.1`（本地/容器），1 个（`qwen_api.yaml`）指向外部聚合平台，仅存 `base_url` 与**环境变量名**，无凭据。
- `p01_prod_candidate_v1.lock.json` 为纯元数据（id/purpose/status/mechanisms/fields/assets/budget/promotion_gates），无绝对路径。

## 尚未同步

见 `MISSING_FILES.md`（C 线建库脚本、A 线历史版本与运行轨迹按批次推进）。
