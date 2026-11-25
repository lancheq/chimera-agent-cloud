# C 线检索分歧核查

本文件回答三件事：新旧检索算法的实际差异、新库是否已切入生产、C 线实验结论的适用范围。

**结论先说：新库目前完全没有切流，生产仍走旧库 + 0.8/0.2 权重；C 线 A/B/C/D 对照实验用的是实验适配器（1.0/1.0 + 新库）。两者的检索参数不同，所以"新库更好"不能直接外推为"生产切库会变好"。**

## 一、RRF 权重确实变了

| 参数 | 旧 `src/chimera_agent_baseline/rag.py` | 新 `scripts/c_line/v2_search_adapter.py` |
|---|---|---|
| Dense 召回 | 20 | 20 |
| BM25 召回 | 20 | 20 |
| **Dense 融合权重** | **0.8**（`DENSE_WEIGHT = 0.8`，L221） | **1.0**（`1.0 / (RRF_K + rank + 1)`，L192/L197） |
| **BM25 融合权重** | **0.2**（`BM25_WEIGHT = 0.2`，L222） | **1.0**（同上） |
| RRF k | 60（L214） | 60（L33） |
| 重排候选 | 30 | 30 |

旧：`s = 0.8/(60+r_d) + 0.2/(60+r_b)`
新：`s = 1.0/(60+r_d) + 1.0/(60+r_b)`

即新版把 dense 与 BM25 改成了**等权融合**。

### 一处必须指出的矛盾

`v2_search_adapter.py` 的 docstring 第一行写：

> `不改变任何检索算法——dense 20 / BM25 20 / RRF 30 / 重排 max_len 512 / 正文 5`

但代码实际把权重从 0.8/0.2 改成了 1.0/1.0。**这句声明与实现不符**，属于文档级错误，需要作者确认是有意改的还是实现时顺手写死。

这不必然是 Bug——等权融合在某些评测里确实更好——但它意味着 C 线四组对照（A 原混合库 / B 新2025+原2026 / C 纯2025旧 / D 纯2025新）是**在新权重下比较的**，而生产旧库用的是旧权重。两组分数不能直接对比。

## 二、新库没有切流（代码证据）

两条检索路径都只认旧库：

| 位置 | 代码 | 指向 |
|---|---|---|
| `mcp_server.py:151` | `from chimera_agent_baseline.rag import GuidelinesSearch`<br>`search = GuidelinesSearch(resource_dir, reranker_dir=reranker_dir)` | `resources/guidelines_db`（旧库，2889 分片） |
| `knowledge_access.py:111` | `from chimera_agent_baseline.rag import GuidelinesSearch`<br>`class VerifiedSearch(GuidelinesSearch)` | 同上，加了一层权限校验 |

`knowledge_access.py:75` 的白名单只有：

```python
selected = ('guidelines_db', 'guideline_qa.jsonl', 'guideline_qa.embeddings.npy')
```

没有 `rechunk_20261124*` 任何一代新库。

**所以：**
- 真实进程加载的是旧库
- 普通 MCP 路径（`search_guidelines`）与 guarded knowledge access 路径都不支持 v2
- `V2GuidelineSearch` 只活在 `scripts/c_line/` 里，是实验工具，不是组件

## 三、旧库的实际构成（脚本 + README + 实测）

`guideline_dataset/scripts/` 五个脚本的实际链路：

| 脚本 | 输入 | 输出 | 实测条数 |
|---|---|---|---|
| `01_normalize_eau.py` | `raw/eau_official_chunks.jsonl` | `normalized/eau_normalized.jsonl` | 1501 → 1501 |
| `02_extract_eau_pdf.py` | `raw/eau_2025_prostate.pdf` | `raw/eau_pdf_chapters.jsonl` | 478 章节 |
| `03_merge_normalize.py` | 多来源 | `normalized/guidelines_unified.jsonl` | **376**（README 写 491） |
| `04_extract_qa.py` | 章节 + QA | `qa/guideline_qa.jsonl` | **20**（另有 `guideline_qa_api.jsonl` 140） |
| `05_cleanup.py` | 上述产物 | 清理后 | — |

### 两处 README 与实际不符

`guideline_dataset/README.md` 生成于 2026-08-20，之后数据被替换过但文档没更新：

| 项 | README 声明 | 实际 |
|---|---|---|
| `normalized/guidelines_unified.jsonl` | 491 条 | **376** 行 |
| `qa/guideline_qa.jsonl` | 20 条带 grounding 的 QA | 20 行（但**生产用的是 `resources/guideline_qa.jsonl` = 428 行**） |

**428 条这个数字不是这五个脚本产出的。** `resources/guideline_qa.jsonl` 的字段是：

```
id, task, question, answer, decision, confidence, evidence_level,
grounding, support_quote, _primary_grounding
```

比 `qa/guideline_qa.jsonl` 多了 `task / decision / confidence / evidence_level / _primary_grounding`。说明 428 条是后来另一条（未入库的）流程生成的，**生成它的代码目前不在 `guideline_dataset/scripts/` 里**。这是 C 线旧库链条上唯一还没找到源码的环节。

## 四、待核实（需要作者确认，不是我能推断的）

1. 1.0/1.0 是 C 线实验的有意设计，还是实现时未同步旧代码的权重常量？
2. 新库是否计划切流？如果计划，`knowledge_access.py` 的白名单与 `VerifiedSearch` 需要同步改造。
3. 428 条 QA 的生成脚本在哪？是否合规可公开？
4. `guidelines_unified` 376 vs 491，是删了不合规条目还是换了来源？

## 五、对学习的影响

读 RAG 模块时要注意：**`round2/src/.../rag.py` 是生产实现，`scripts/c_line/v2_search_adapter.py` 是实验实现**。两者不是同一套参数，代码结构也不同（前者基于 ChromaDB collection，后者基于 `records.jsonl` + `vectors.npy` 自建检索）。先读 `rag.py` 理解生产链路，再读适配器理解 C 线在试什么，不要把两者的行为混为一谈。
