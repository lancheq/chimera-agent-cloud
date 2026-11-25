# Fact Check 机制说明（含一处旧矛盾的解答）

`agent/fact_check.py`，713 行。它是 Pro 四阶段图的 **Stage D 填充前护栏**。

## 一、三步机制

```
decompose → verify → flag
```

1. **Decompose**：把 `free_text` / `reasoning` 拆成原子声明，每句一个可临床检验的断言
2. **Verify**：每条声明对两类依据核对
   - a. 证据转录（agent 是否真的检索到过这个值）
   - b. RAG 检索到的指南段落（阈值是否正确）
3. **Flag**：标记 `TRUE` / `FALSE` / `UNVERIFIED`
4. **Report**：若存在 `FALSE`，通知调用方可选择重新触发 self-refine

## 二、旧报告那处矛盾的解释

此前两份材料一处说"Fact Check 完全不调用 LLM"，另一处说"会调用 LLM 分解原子事实"。**两种说法各描述了一条路径，都不算错，但都不完整。**

代码事实：

| 位置 | 内容 |
|---|---|
| `fact_check.py:29` | `from langchain_core.language_models import BaseChatModel` |
| `fact_check.py:193` | 函数签名 `model: BaseChatModel` |
| `fact_check.py:208` | `resp = model.invoke(...)` ← **确实调用模型** |
| `fact_check.py:249` | `def decompose_claims(..., model: BaseChatModel \| None = None)` ← **model 可选** |
| `fact_check.py:548` | 同上有 `model: BaseChatModel \| None = None` |

也就是说：

- **传入 model** → 走 LLM 辅助分解，能处理自由文本里非模板化的表述
- **不传 model（None）** → 走纯程序化检查（正则 + 规则匹配），速度快、确定性高，但只能识别模板化写法

模块 docstring 自己也写了："combines programmatic checks (fast, deterministic) with optional RAG-based verification"。

**所以正确的问法不是"调不调 LLM"，而是"这次运行有没有传 model 进来"。** 这一点要看调用点的配置，不是看 fact_check.py 本身。

## 三、数据结构

```python
@dataclass
class Claim:
    ...
    status: str = "UNVERIFIED"   # "TRUE" | "FALSE" | "UNVERIFIED"
    issue: str = ""              # FALSE 时的原因描述

@dataclass
class FactCheckResult:
    ...
    n_true / n_false / n_unverified
    # __str__ 输出形如 "3 TRUE, 1 FALSE, 0 UNVERIFIED"
```

`Claim.status` 用三值而不是布尔，是为了把"证据不足"与"证据矛盾"分开——这两者在临床决策里后果完全不同。

## 四、与 Reflection 的关系

调用链上，fact_check 在 self-refine **之后**、form_fill **之前**：

```
self_refine → fact_check(Stage D) → form_fill → reflection(内层) → postprocess
```

它发现 `FALSE` 时可以要求重新 self-refine，但这是"建议"不是"强制"——是否重跑由调用方决定。

## 五、运行证据的实际情况

**没有找到 fact_check 的独立合成测试产物目录。** 服务器上 `output/` 下没有 `*fact*` 命名的实验目录，`p01_g1r_reflection_20261118` 只覆盖 reflection 契约。

现有可用材料只有两类：

1. **源码**：`round2/src/chimera_agent_baseline/agent/fact_check.py`（713 行，已同步）
2. **测试引用**：`round2/tests/test_b_evidence_reconciliation_build.py:214` 有 `cfg.agent.form_fill.atomic_check = False`，说明测试里会显式关掉这条护栏（即默认可能是开的）

要拿到真实输入输出，需要一次 `atomic_check: true` 的运行。A 线 91 例的配置里它是 `false`，所以现有 182 份事件账本里没有 fact_check 节点。

## 六、待补

| 项 | 需要什么 |
|---|---|
| 原子事实的输入输出样例 | 一段 reasoning → 若干 Claim 的实际映射 |
| LLM 路径与程序路径的差异 | 同一段 reasoning 分别用 model=None / 传 model 的结果对比 |
| 调用点配置 | 生产里到底传不传 model（看 graph.py 的调用） |
