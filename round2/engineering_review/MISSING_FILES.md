# MISSING_FILES — 未同步文件及原因

本文件记录**本轮明确没有上传**的内容，以及每一项的处理方式。分四类：合规拦截、体量拦截、待许可、未找到。

## 一、合规拦截（含患者数据或指南正文）

| 文件 | 位置 | 体量 | 拦截原因 | 可替代材料 |
|---|---|---:|---|---|
| `model_calls.jsonl` | `s4_shared_plan_performance_v2r1/worker*/{arm}/task1/<case>/` | 17 KB/例 | 含 upfront context 与工具返回全文，即患者临床值 | `traces/` 已保留同一轨迹的结构骨架（`stage`/`input_tokens`/`usage`），去掉正文 |
| `candidate_checkpoint.json` | 同上 | 21 KB/例 | 节点状态快照，含已取栏目内容 | `candidate_events.jsonl` 骨架保留了 `budget_before/after` 与事件序列 |
| `information_access_snapshot.json` | 同上 | 6 KB/例 | 已取栏目的原文 | `information_access_receipt.json` 可作替代（仅记录取用事实，不含内容），待审 |
| `records.jsonl` / `records_v2.jsonl` / `records_r1.jsonl` | `guideline_dataset/rechunk_*/` | 2.5 MB / 3.2 MB | 指南正文分片 | `kb_manifests/` 已给出条数、类型分布、字符长度分布 |
| `vectors*.npy` / `vec/shard_*.npy` | 同上 | 3.2 MB + 21 shard | 向量可由 records 重建；且与正文一一对应 | `merge_summary.json` 给出 `shape=[996,768]`，`index_summary.json` 给出模型路径与维度 |
| `db/chroma.sqlite3` | `rechunk_20261124/db/` | — | 完整检索库 | `index_summary.json` |
| `guideline_qa.jsonl` / `.embeddings.npy` | `resources/` | 328 KB / 1.3 MB | 指南 QA 正文 | 未提供 |
| EAU 指南原始 PDF | `guideline_dataset/raw/` | — | 版权限制，不得公开再分发 | `stats.json` 已记录页数、目录页、参考文献页分布 |
| `ledger.json` | `a_attribution_20261122/` | 291 KB | **91 例逐例决策明细**（逐例判对错、逐阶段调用数） | `attribution_summary.json` + `accounting.json` 已给全部聚合口径 |
| `attribution_30.json` | 同上 | 42 KB | 30 例逐例归因 | 同上 |
| `avoidable_policy_calls.json` | 同上 | 40 KB | 逐例可避免调用判定 | 同上 |
| `b_evidence_reconciliation_build_20261118/` 的 1348 个 json + 222 md | — | 880 MB | 逐例证据包 | `recon_build/` 脚本 + `evidence/` 4 份汇总 |
| `b12_boundary/string_replay/case/private/` | — | — | 目录名即标识为 private | 未提供 |

## 二、体量拦截（技术上可传，但无必要）

| 内容 | 体量 | 处理 |
|---|---:|---|
| `resources/embedding_model/` | 1.2 GB | 只给指纹与路径，见下 |
| `resources/reranker_model/` | 3.2 GB | 同上 |
| `model/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` | ~20 GB | 同上 |
| `resources/guidelines_db/` + `.bak` | 45 MB + 48 MB | 只给 manifest |
| `b12_regression_20261122/` 的 1987 个 json | 58 MB | 只取脚本与代码快照 |

### 模型与资源身份（不上传权重，只记录指纹）

来自 `host_smoke.py` 常量：

```
BINARY      = /home/hedongze/llama-build/bin/llama-server
BINARY_SHA  = 5e8d69056250cb5f263d721fae79bbc7b0b3246b038696493c50ee8155a3ad9a
MODEL_SHA   = ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61
TEMPLATE_SHA= 55d4931433fe502b794226ee7f4d206a6bdd436ac9f80eb7d8ebb4c639f9ea0c
KNOWLEDGE_SHA=13fbd478a3c4f2f3fa2f2fe60a9b48c5c838df097f15eab83077a36fc41bb4da
```

embedding 模型路径 `resources/embedding_model`，维度 768；reranker 路径 `resources/reranker_model`。三者的具体版本号与量化方式**未在仓库任何文件中出现**，需要服务器补一份 `RESOURCE_MANIFEST.md`。

## 三、待许可

| 内容 | 位置 | 阻塞点 |
|---|---|---|
| `rag_eval_sample_en.jsonl`（120 条查询 + 金标节） | `rag_eval_20261025/` | 含指南节号与查询原文，需确认发布权限 |
| `rag_eval_t1_en_full.jsonl` / `rag_eval_sample_zh.jsonl` | 同上 | 同上 |
| `cases_evidence/` 中的 B01 冲突病例证据包 | 仓库已有 | 未复核内容，建议单独审 |

## 四、未找到

| 报告里提到的路径 | 实际情况 |
|---|---|
| `02_extract_eau_pdf.py` / `03_merge_normalize.py` / `04_extract_qa.py` | **找到了**，在 `guideline_dataset/scripts/`（01~05 共 5 个）。本轮只同步了 rechunk 系列，旧的 5 个抽取脚本尚未推——因它们产出的是旧库，价值次之 |
| `rechunk_eau.py` | **服务器上不存在**，实际在本地 `chimera-c/` 工作目录，已同步 |
| `configs/experiment/` 之外的其他实验预设 | `p01_prod_candidate_v1.lock.json` 已推；其他 arm 专属 config 未逐一排查 |
| 38 个候选病例筛选程序 | 未定位到独立脚本；疑似由 `prepare_shared_plan_study.py` 与 `build_eligibility.py` 内含逻辑完成 |
| 66 标记 / 29 风险组的处理代码 | 未定位到独立脚本，疑似在 `b_data_applicability_*` 系列的 `08_semantic_fix.py` 等脚本内（`semantic_fix.diff` 可见部分） |

## 五、已知代码里的环境常量

以下不是秘密，但会影响复现：

- 所有脚本硬编码 `/home/hedongze/CHIMERA-agent` 为根目录
- `host_smoke.py:14` 硬编码 `llama-server` 绝对路径
- 服务端点：本地 `127.0.0.1:8765`（llama.cpp）、`127.0.0.1:11435`（Ollama）、外部 `llmapi.blsc.cn`（第三方 API，仅存 env 变量名）

要迁移到别的机器复现时，这些都需要改。
