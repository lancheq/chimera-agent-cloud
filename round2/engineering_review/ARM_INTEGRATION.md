# 三仓关系与 v2 检索接入方式

`archive/armA_current/` 与 `archive/armC_current/` 是 2026-11-25 从服务器取回的当前快照（只含 `src` / `configs` / `scripts` / `inference.py` / `pyproject.toml`，不含 data、output、model、bench）。三者都不是 git 仓库，**因此这是快照，不是历史版本**。

## 一、三仓是什么关系

| 仓库 | py 数 | 与主仓的关系 |
|---|---:|---|
| `CHIMERA-agent`（主仓） | 56 | 最新工程实现，B 线模块齐全 |
| `CHIMERA-armA` | 25 | **固定对照臂**：缺 B 线全部新模块 |
| `CHIMERA-armC` | 135 | 主仓 + v2 适配器 + benchmark 工具 |

### armA 缺什么

对比 `src/chimera_agent_baseline/`，主仓有而 armA 没有：

```
agent/assertion_card.py      agent/evidence_reconciliation.py
agent/b12_evidence_state.py  agent/evidence_review.py
agent/decision_gate.py       agent/fact_check.py
agent/decision_question.py   agent/form_parsing.py
agent/evidence_features.py
```

外加 `agent/form_fill.py` 两版不同。说明 armA 停在 B 线（时间序列、证据协调、fact_check）合入之前，作为"不改进"的基线使用。

### armC 多了什么

```
src/chimera_agent_baseline/v2_search_adapter.py        ← 适配器进了核心包
src/chimera_agent_baseline/agent/graph.py.pre_longitudinal  ← 44 KB，11-18 备份
src/chimera_agent_baseline/bench_cost.py
src/chimera_agent_baseline/benchmark_entry.py
```

不同文件：`entry_profile.py`、`mcp_server.py`、`models/__init__.py`。

`rag.py` **与主仓完全相同**——armC 没有改检索算法本体，只是加了一条可选分支。

## 二、v2 到底怎么接进去的

armC 的 `mcp_server.py` 比主仓多这一段（`diff` 定位在 150-170 行）：

```python
# --- v2 适配器（默认关闭；原 chroma 路径保持不变） ---
if os.environ.get("CHIMERA_KB_V2", "").strip().lower() in ("1", "true", "on", "yes"):
    from chimera_agent_baseline.v2_search_adapter import V2GuidelineSearch
    v2_dir = os.environ.get("CHIMERA_KB_V2_DIR",
                            ".../guideline_dataset/rechunk_20261124_v2")
    p1_on = os.environ.get("CHIMERA_KB_P1", "1").strip().lower() not in ("0","false","off","no")
    search = V2GuidelineSearch(v2_dir, resource_dir, reranker_dir, p1=p1_on)
    ...
    return search
# 否则走原 GuidelinesSearch
```

三个要点：

1. **默认关闭**。不设 `CHIMERA_KB_V2` 就走旧 ChromaDB，代码注释自己也写了"默认关闭；原 chroma 路径保持不变"。
2. **guarded knowledge access 路径没有这条分支**。armC 的 `knowledge_access.py` 与主仓一致（`selected = ('guidelines_db', ...)` + `VerifiedSearch(GuidelinesSearch)`），只认旧库。所以即使开了 `CHIMERA_KB_V2`，走权限校验的检索仍用旧库——**两条路径会分裂**。
3. **主仓连这段代码都没有**。`round2/src/.../mcp_server.py` 里 grep 不到 `CHIMERA_KB_V2`。

## 三、一个没法从代码确定的事

armC 的 `configs/` 与 `scripts/` 里**都没有设置 `CHIMERA_KB_V2`**（已 grep 全目录）。所以：

- 无法从仓库判断 armC 的某次实验是否真的开了 v2
- 也无法判断跑实验时用的是 `rechunk_20261124_v2` 还是 `v2r1`
- 如果当时是手工 `export`，那就没有任何记录

要确认只能去翻当时的 shell 历史或启动日志，当前材料不足以定论。这一点在 `C_LINE_RETRIEVAL_DIVERGENCE.md` 里也列为待核实项。

## 四、`graph.py.pre_longitudinal` 的价值

44 KB，时间戳 2026-11-18 20:42，位于 armC 的 `agent/` 下。这是**把 longitudinal（PSA 时间序列）合入 graph 之前的版本**，可用它对照出：

- 时间序列节点是怎么插进状态图的
- 插入后哪些边被改接
- 新增节点前后的 state 字段差异

这是本轮能找到的少数"改之前"快照之一。同类的还有 `b_line/archive/before_recon_build/`。

## 五、不能做的事

- **不能把 armA / armC 的当前快照当历史版本引用**。它们没有 commit，改动时间不可考，只能说是"2026-11-25 这一刻的样子"。
- **不能假设生产的检索行为**。生产（主仓）没有 v2 代码，只有旧库；C 线实验的结论要到 `scripts/c_line/` 那套适配器里找，两套参数不同（1.0/1.0 vs 0.8/0.2）。

## 六、要让版本可追溯，最小动作

```bash
cd ~/CHIMERA-armA && git init && git add src configs scripts inference.py pyproject.toml \
  && git commit -m "snapshot 20261125"
cd ~/CHIMERA-armC && git init && git add src configs scripts inference.py pyproject.toml \
  && git commit -m "snapshot 20261125"
```

只加这五类路径，不要 `git add -A`（会带进 data / output / model / bench）。三分钟的事，之后每个改动都有 diff。
