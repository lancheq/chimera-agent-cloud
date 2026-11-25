# C 线脚本索引（指南库重建与检索优化）

来源：本地 C 线工作目录（原路径 `/Users/<user>/Workbuddy/2026-11-24-13-07-24/chimera-c/`）+ 服务器实验目录。
描述取自各文件 docstring 首行，未做改写。

## 说明

- 这些脚本是 **2026-11-24 那一批 C 线实验的实际执行代码**，报告里提到的 `rechunk_eau.py` 在此处（服务器上不存在同名文件）。
- 脚本内的 `/home/hedongze/...` 路径为当时真实运行环境常量，保留以便复现，不含凭据。
- **仅同步脚本，未同步任何检索结果、指南原文或 records 数据**。

## 建库与切分

| 文件 | docstring |
|---|---|
| `rechunk_eau.py` | EAU 前列腺癌指南 — 结构感知重切分（C 线方向一） |
| `rechunk_v2.py` | v2 重切分：在 v1 基础上加三件事 |
| `build_index.py` | 把结构感知重切分结果建成独立向量库（不触碰 resources/guidelines_db）。 |
| `build_title_boost.py` | 章节标题增强：给每条独立推荐前缀真实 section_title，重新编码，其余条目不变。 |
| `downgrade_table_meta.py` | 表格归属如实降级：caption 编号可追溯，但临床场景标低置信。 |
| `fix_table_section.py` | 修正表格的章节归属：用版面标题位置而不是 caption 编号。 |
| `chk_table_section.py` | 检查表格 caption 编号与所属章节是否一致。 |
| `patch_v2_meta.py` | 只补元数据，不动文本与顺序（避免与正在进行的编码冲突）。 |
| `backfill_p0_p3.py` | 父子回填 P0-P3 对照（Q01 为主，其余 5 条给通用指标）。 |
| `fix_old_ctx.py` | 修旧库 section 解析（编号+标题格式），并诊断初次诊断场景适用率异常。 |
| `chk_align.py` |  |

## 向量编码与合并

| 文件 | docstring |
|---|---|
| `encode_full.py` | 全量编码 + 建 chroma（tmux 内运行，绕开 ssh 会话时长限制）。 |
| `encode_shard.py` | 分片生成向量（避开 OOM）：每次处理一段，落盘 npy 分片。 |
| `encode_shard_v2.py` | v2 分片编码：用 retrieval_text（章节标题 + 原文）建向量。 |
| `merge_shards.py` | 合并分片向量并校验顺序。 |
| `merge_v2.py` |  |
| `export_old_vectors.py` | 导出旧库全部向量到 npz（在副本上操作，不触碰原库）。 |
| `build_e_evidence.py` | 自动生成 E 组证据：新库检索 top5 + P1 规则回填同节条件分支。 |

## 检索与对照实验

| 文件 | docstring |
|---|---|
| `retrieve_abcd.py` | A/B/C/D 四组语料对照检索（C 线方向一/三）。 |
| `retrieve_boost.py` | 章节标题增强前后对照：同一 query、同一管线，只换 recommendation 的向量。 |
| `retrieve_v2.py` | 组合方案回放：v2 库（标题入检索文本）+ F6 泛词过滤 + P1 分支回填。 |
| `rerank_variants.py` | 重排器分数对照 R1-R6：固定同一条推荐，只改上下文，看原始分数。 |
| `query_variants.py` | Q01 Query 修正对照：原始（PI-RADS 3）vs 修正（PI-RADS 2）。 |
| `diag_bm25.py` | Q05 BM25 分数来源诊断：不预设"语料缩小导致 IDF 上升"。 |
| `t1_retrieval_abcd.py` | 30 例留出病例的固定 Query 四组检索对照。 |
| `report_abcd.py` | 把 ABCD 结果整理成对照表。/tmp/abcd_result.json -> 可读 MD。 |
| `unify_stats.py` | 统一统计口径：把 top5 与 P1 新增分开，四类互斥。 |

## 新旧库适配与审计

| 文件 | docstring |
|---|---|
| `v2_search_adapter.py` | v2 运行时适配器：drop-in 替换 GuidelinesSearch，只换数据源。 |
| `accept_adapter.py` | v2 适配器验收：6 条历史 query 逐阶段检查。 |
| `accept_adapter2.py` | 补验收：阶段自洽性 + MCP 返回实际内容。 |
| `audit_old_kb.py` | 旧知识库（resources/guidelines_db）质量审计 — 只读 sqlite，不启动 chroma。 |
| `audit_t1_deep.py` | 三件事： |
| `split_mismatch.py` | 两件事： |
| `recompute_t1.py` | 四组检索对照的统计修正： |

## LLM 判分与样本

| 文件 | docstring |
|---|---|
| `llm_abcd.py` | 35B 标准证据四组实验（方向二）。 |
| `analyze_llm.py` | 对 35B 四组输出做结构化打标（关键词 + 简单规则），输出对照表。 |
| `judge_five.py` |  |
| `f_experiment.py` | F0-F3：非临床段落过滤 × Query 泛词移除。 |
| `sample_t1.py` | T1 独立验证病例抽样（第一阶段的结项实验）。 |
| `make_q01_case.py` | 生成 Q01 完整真实检索案例（原始 Query / 各阶段排名 / 目标证据去向）。 |
| `extract_case_inputs.py` | 抽取三个 C01 病例的诊断输入（方向二：标准证据四组实验的基座）。 |
| `inspect_q05_q03.py` |  |
| `show_tb.py` |  |

## 探针（自 /tmp 抢救）

| 文件 | docstring |
|---|---|
| `probes/probe_a.py` | 旧库检索质量探针 A：全量 120 条英文查询，dense/BM25/RRF 三路逐条落盘。 |
| `probes/probe_b.py` | 旧库检索探针 B：指定查询的完整链路回放（dense / BM25 / RRF / rerank）+ 原文全文。 |
| `probes/dump_sec.py` |  |

## 服务器侧验证与评估

| 文件 | docstring |
|---|---|
| `c_line/validation/check_v2_freeze.py` | v2 冻结前三项机检：向量逐条对应、表格 caption 与所属小节、覆盖率分母重算。 |
| `c_line/validation/check_v2_tables2.py` | 表格章节归属复核 v2：用 PDF 字体信息找 caption 上方最近的真标题。 |
| `c_line/validation/freeze_and_select.py` | v2 冻结件 + 独立验证病例预注册（不参考任何系统输出）。 |
| `c_line/validation/retrieve_v2_frozen.py` | v2 冻结口径回放：P1 回填最多 2 小节、按首次命中的重排顺序取、正文去重、不截断。 |
| `rag_eval/qa_channel_eval.py` | QA-channel comparison: old (183-item lib, BM25) vs new (428-item lib,  |
| `rag_eval/qa_embed_precompute.py` | Precompute dense embeddings for the QA library questions (run once per |
| `rag_eval/rag_eval.py` | Retrieval quality evaluation for the deployed hybrid RAG pipeline (202 |
| `rag_eval/ragfix_apply.py` | Apply PF1+PF2 retrieval fixes to the deployed rag.py (with .bak backup |
| `rag_eval/ragfix_offline_apply.py` | Apply PF4-hardening: inject HF offline env vars into the embedding sub |
| `rag_eval/ragfix_pf3_apply.py` | Apply PF3: QA channel dense matching + normalised loader + init-time B |
| `rag_eval/score_ab_arms.py` | Score the two A/B arms on the 91 GT-labelled T1 cases and compare. |
| `predictor/check_node_path.py` | 节点路径 vs 账本：predictor_node.revealed_sections 还原出的可见段落，和账本差多少。 |
| `predictor/diag_sensitivity.py` | 诊断：掩码改动了特征，为什么某些任务的预测一例都不翻？敏感度到底来自哪里。 |
| `predictor/eval_predictor_masked.py` | Predictor 合法证据回放测试（2026-11-24，v2）。 |