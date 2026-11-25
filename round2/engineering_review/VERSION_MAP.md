# VERSION_MAP — 实验与代码/配置/资源版本对应

## 一、一个必须先说清的事实

**当前无法为历史实验建立精确的代码版本映射**，原因是版本管理本身不完整：

| 仓库 | 状态 | 问题 |
|---|---|---|
| `/home/hedongze/CHIMERA-agent` | git `master` @ `606b6df` | **213 个未提交变更**；`git remote` 为空（不连 GitHub） |
| `/home/hedongze/CHIMERA-armA` | **非 git 仓库** | 无版本历史，无法回溯任何时点 |
| `/home/hedongze/CHIMERA-armC` | **非 git 仓库** | 同上 |

后果：11 月 15 日跑的 s4 实验、11 月 22 日跑的 B12 回归，都无法用 commit 唯一定位其代码状态。可行替代是按**产物内嵌的指纹**对齐（见下）。

## 二、产物内嵌指纹（可用来对齐版本）

`initial_plan.json` 的 `binding` 字段自带四类指纹，比 commit 更可靠：

```json
"diagnostic_system_sha256": "6d01477e...",
"upfront_context_sha256":  "208484d1...",
"clinical_snapshot_id":    "77c99f3c...",
"knowledge_snapshot_id":   "13fbd478..."
```

`knowledge_snapshot_id = 13fbd478...` 与 `host_smoke.py` 的 `KNOWLEDGE_SHA` 一致，说明该实验使用的知识库就是 `kb_manifests/` 里记录的那一份。

`candidate_execution_config.json` 另有 `runtime_cohort_sha256: 68cd2aff...`，可用于判定两次运行是否用了同一批病例。

## 三、GitHub 与本地的对应

| 层 | 位置 | 标识 |
|---|---|---|
| Phase 1 发布版 | GitHub `main` 根目录（`src/` `configs/` `scripts/` …） | `6c2cc54` 之前的历史 |
| Round 2 研究版 | GitHub `main` / `round2/` | 本轮推送后 `adae746` |
| 服务器主仓 | `/home/hedongze/CHIMERA-agent` | `master` @ `606b6df`（dirty 213） |
| B12 回归基线 | `b12_regression_20261122/formal_final_baseline/src/` | 已归档至 `b_line/archive/formal_final_baseline_src/`（71 个 py） |
| 证据协调修复前 | `b_evidence_reconciliation_build_20261118/before/` | 已归档至 `b_line/archive/before_recon_build/` |

## 四、各实验的配置与代码入口

| 实验 | 配置 | 代码入口 | 产物 |
|---|---|---|---|
| s4 shared-plan 两臂（11-15） | `configs/experiment/round2_react.yaml` + `candidate_execution_config.json`（含完整 resolved cfg） | `run.py` + `shared_plan.py` + `guarded_runtime.py`；setup 为 `tools/prepare_shared_plan_study.py` | `a_line/traces/`、`a_line/summary/` |
| A 线归因（11-22） | 无独立 config，读 s4 产物 | `a_line/scripts/build_ledger.py` → `build_attribution.py` → `finalize.py` | `a_line/summary/` |
| B 数据语义修复（11-22） | — | `b_line/scripts/data_semantic_fix/`（含 `semantic_fix.diff` 记录 before→after） | `b_line/evidence/` |
| B12 回归（11-22） | `b_line/scripts/b12_regression/env.sh`, `check.sh` | `matrix.py` / `preregister.py` / `formal.py` | `b12_regression_20261122/`（仅代码，数据未推） |
| C 库重建（11-24） | 无 Hydra config，脚本内常量 | `round2/scripts/c_line/rechunk_eau.py` 等 43 个 | `kb_manifests/`、`c_line_analysis/` |
| C 检索对照 | 同上 | `retrieve_abcd.py` + `unify_stats.py` | `c_line_analysis/ABCD_四组对照表.md` |

## 五、模型与资源

| 项 | 值 | 来源 |
|---|---|---|
| 推理后端 | llama.cpp `llama-server`，SHA `5e8d6905...` | `host_smoke.py` |
| 主模型 | `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`，SHA `ac0e2c11...` | 同上 |
| tool parser | `gemma4` | `candidate_execution_config.json` |
| 上下文 | `context_limit: 16384`，`context_reserve: 128` | `initial_plan.json` |
| 采样 | `temperature: 0.3`, `top_p: 0.95`, `max_new_tokens: 8192`（C 线）/ `planner_max_tokens: 1024`（A 线） | configs |
| reasoning 预算 | `reasoning_budget_tokens: 4096`（A 线）/ `planner_reasoning_tokens: 512` | 同上 |
| embedding | `resources/embedding_model`，768 维 | `index_summary.json` |
| reranker | `resources/reranker_model` | `accept_adapter.py` 常量 |

**缺口**：embedding / reranker 的确切模型名与版本号在仓库与服务器配置中都没写清，只有路径。这是复现时最先会卡住的点。

## 六、若要补齐版本映射，最小动作

1. 主仓 `git add -A && git commit`（或至少 `git stash create` 留一个树对象），把 213 个变更加以固化；
2. armA / armC 执行 `git init && git add -A && git commit -m "snapshot 20261125"`——只需一次，之后就能按 commit 回溯；
3. 在 `configs/` 里为 embedding / reranker 补一个 `resource_versions.json`（模型名、HF repo、commit hash、下载日期）。

这三步不改任何实验逻辑，只补可追溯性。
