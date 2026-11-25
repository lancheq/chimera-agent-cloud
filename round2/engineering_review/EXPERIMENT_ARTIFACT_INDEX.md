# EXPERIMENT_ARTIFACT_INDEX — 实验产物索引

只列**仓库中实际存在**的路径。服务器上有但未同步的，在 `MISSING_FILES.md` 里。

## 1. s4 shared-plan 两臂对照（A 线主体，2026-11-15）

| 项 | 位置 |
|---|---|
| 实验 ID | `s4-shared-plan-internal-t1-v2-r1-20261115` |
| setup 脚本 | `a_line/scripts/prepare_shared_plan_study.py` |
| resolved config | `a_line/summary/worker0_candidate_execution_config.json`（46 例）、`worker1_candidate_execution_config.json`（45 例） |
| 事件账本 | `a_line/traces/worker{0,1}/{fixed,adaptive}/task1/<case>/candidate_events.jsonl`（91×2 = 182 份） |
| 计划 | `a_line/traces/worker*/common_plans/task1/<case>/initial_plan.json` |
| 首观测 | 同目录 `first_observation.json` |
| 索引 | `TRACE_INDEX.md` |
| 执行顺序还原 | `a_line/A_LINE_EXECUTION_ORDER.md` |

## 2. A 线归因（2026-11-22）

| 项 | 位置 |
|---|---|
| 入口 | `a_line/scripts/build_ledger.py` → `build_attribution.py` → `finalize.py` |
| 资格判定 | `a_line/scripts/build_eligibility.py` |
| 聚合产物 | `a_line/summary/accounting.json`、`attribution_summary.json`、`holdout_eligibility.json` |
| 协议冻结 | `a_line/summary/protocol_freeze_draft.json`、`handoff.json` |
| 逐例明细 | **未同步**（`ledger.json` 291 KB 等，见 MISSING_FILES） |
| 协议报告 | `reports/ROUND2_A_ATTRIBUTION_PROTOCOL_20261122.md` |

## 3. B 数据语义修复（2026-11-22）

| 项 | 位置 |
|---|---|
| 入口脚本 | `b_line/scripts/data_semantic_fix/`（4 个） |
| 前后 diff | `b_line/evidence/semantic_fix.diff` |
| 计数对比 | `b_line/evidence/before_after_counts.json` |
| 拒读探针 | `b_line/evidence/denial_probes.json`（6/6 denied，读取 0 字节） |
| 合成校验 | `b_line/evidence/synthetic_checks.json`（11/11 passed） |
| 协议报告 | `reports/ROUND2_B_DATA_SEMANTIC_FIX_20261122.md` |

## 4. 证据协调构建（2026-11-18）

| 项 | 位置 |
|---|---|
| 入口 | `b_line/scripts/recon_build/index_evidence.py`、`run_serial.py` |
| 修复前代码 | `b_line/archive/before_recon_build/` |
| 逐例证据 | **未同步**（880 MB） |
| 报告 | `reports/ROUND2_B_EVIDENCE_RECONCILIATION_BUILD_20261118.md` |

## 5. B12 边界与回归（2026-11-22）

| 项 | 位置 |
|---|---|
| 边界回放 | `b_line/scripts/b12_boundary/replay_boundary.py` |
| 回归入口 | `b_line/scripts/b12_regression/{preregister,formal,matrix,compare_formal,cases}.py` |
| 基线代码 | `b_line/archive/formal_final_baseline_src/`（71 个 py） |
| 回归矩阵报告 | `reports/B12_REGRESSION_MATRIX.md`、`B12_R11_BOUNDARY_REPORT.md` 等 8 篇 |
| 逐例数组 | **未同步**（1987 个 json） |

## 6. C 线知识库重建（2026-11-24 / 25）

| 项 | 位置 |
|---|---|
| v1 切分 | `scripts/c_line/rechunk_eau.py` |
| v2 切分 | `scripts/c_line/rechunk_v2.py` |
| v2r1 表修复 | `scripts/c_line/fix_table_section.py`、`downgrade_table_meta.py`、`patch_v2_meta.py` |
| 编码合并 | `scripts/c_line/encode_*.py`、`merge_*.py` |
| 建库 | `scripts/c_line/build_index.py`、`build_title_boost.py` |
| 三代 manifest | `kb_manifests/v1_20261124/`、`v2_20261124/`、`v2r1_20261124/` |
| 版本映射 | `kb_manifests/KB_VERSION_MAP.md` |
| 审计报告 | `c_line_analysis/C01_知识库重建审计_20261124.md`、`C01_勘误_20261125.md` |
| 记录/向量本体 | **未同步** |

## 7. C 线检索对照（A/B/C/D 四组）

| 项 | 位置 |
|---|---|
| 检索对照 | `scripts/c_line/retrieve_abcd.py`、`t1_retrieval_abcd.py` |
| 统一分母 | `scripts/c_line/unify_stats.py` |
| 2026 贡献分析 | `scripts/c_line/f_experiment.py` |
| 逐阶段损失定位 | `scripts/c_line/retrieve_boost.py`、`diag_bm25.py`、`rerank_variants.py` |
| 新旧库适配 | `scripts/c_line/v2_search_adapter.py` |
| 验收 | `scripts/c_line/accept_adapter.py`、`validation/freeze_and_select.py` |
| 结果表 | `c_line_analysis/ABCD_四组对照表.md`、`Q01_真实检索案例_旧库.md` |
| **参数差异警告** | **`c_line_analysis/C_LINE_RETRIEVAL_DIVERGENCE.md`** — 适配器用 1.0/1.0，生产 `rag.py` 用 0.8/0.2 |

## 8. 旧指南库构建（2026-08）

| 项 | 位置 |
|---|---|
| 五步脚本 | `guideline_dataset/scripts/01_normalize_eau.py` ~ `05_cleanup.py` |
| 说明 | `guideline_dataset/scripts/README.md` |
| 实际规模 | 官方 chunks 1501、PDF 章节 478、统一 schema 376、QA 20（生产用 428，来源未入库） |
| 抽查用探针 | `scripts/c_line/probes/probe_a.py`、`probe_b.py`、`dump_sec.py` |

## 9. Predictor 回放

| 项 | 位置 |
|---|---|
| 掩码评估 | `scripts/predictor/eval_predictor_masked.py` |
| 敏感性诊断 | `scripts/predictor/diag_sensitivity.py` |
| 节点路径检查 | `scripts/predictor/check_node_path.py` |
| 参数 | `resources/` 6 个 json |
| 训练 | `scripts/train_predictor.py` |
| 资源身份 | `RESOURCE_MANIFEST.md` 第四节 |

## 10. 检索评估（rag_eval，2026-10-25）

| 项 | 位置 |
|---|---|
| 评估程序 | `scripts/rag_eval/rag_eval.py`、`qa_channel_eval.py`、`score_ab_arms.py` |
| 修复脚本 | `ragfix_apply.py`、`ragfix_offline_apply.py`、`ragfix_pf3_apply.py` |
| 120 条查询 | **未同步**（待许可） |

## 缺口最快的补法

若要补「Reflection / Fact Check / Predictor 的真实运行证据」，需要找一次 `self_refine: true` 或 `predictor.enabled: true` 的运行产物。A 线那 91 例的配置里这两项都是 `false`，所以现有轨迹无法替代。
