# S3/S4 控制输出修复 v2：工程验收

日期：2026-11-14。状态：**修复完成，有限工程验收通过，v2候选已冻结；尚未进行新临床准确率对照。** 用户指令为“完成修复”。先核查的论文、作者实现、llama.cpp文档与实际服务源码，以及修复契约见[设计与来源](../docs/ROUND2_CONTROL_REPAIR_DESIGN_20261114.md)。

## 已修复的接口

| 首轮失败／风险 | v2实现 | 边界 |
|---|---|---|
| execute但计划为空，或停止与剩余计划矛盾 | wire只保留remaining_plan和reason；空计划明确停止，非空执行第一项，内部仍记录标准Decision | 不宽松接纳旧版矛盾JSON，不借GT推断模型本意 |
| 缺purpose／检索query、返回未知工具、额外case_id字段 | 按实际工具白名单构建JSON Schema，向llama.cpp发送response_format；临床query固定null，指南query非空，purpose必填，禁止额外字段 | 返回后仍校验权限和动作语义，空白query仍拒绝 |
| reason超长、输出非JSON或截断 | 后端限制reason最多512字符、动作数不超剩余额度；客户端拒绝截断控制响应；最多两次格式尝试，第二次用同证据、禁用thinking的最终输出模式 | 后端不支持schema时显式失败，不回退成无约束生成；仍可能耗尽有限尝试 |
| 共同诊断正文为空或请求额外工具 | 两组共同使用最多两次诊断输出尝试；首次保留原推理设置，必要时以同一证据禁用thinking重试一次 | 不获取新信息，不拿reasoning正文补诊断，不生成默认标签；仍空则终止失败 |
| 重试成本与恢复不清楚 | Schema、模板／推理参数纳入请求digest和日志；精确上下文检查使用实际重试模板；已记录返回复用 | SDK传输重试保持0，费用不隐藏；信息账本不重置 |

代码：`src/chimera_agent_baseline/control_schema.py`、`shared_plan.py`、`model_context.py`；显式overlay：`configs/round2_shared_plan_repair_v2.yaml`。v1 overlay保持原样，默认生产入口没有切换为v2。v2的run_id、配置与源码绑定独立，不能套用旧账本。

每病例对客户端模型请求上界从原26变为28，增加量为两个组别各至多一次诊断输出恢复；没有增加工具权限、临床信息单位、工具尝试数或策略轮数。`tools/run_shared_plan_candidate.py`的上界统计已相应纳入。

## 验收结果，三类证据分开

| 类型 | 数量／结果 | 说明 |
|---|---|---|
| 针对性单元与权限／状态机／统计回归 | **97 passed**，75.05秒 | 覆盖非法工具、缺字段、query越权、长度、空计划、截断、后端拒绝、有限恢复、重放无新增调用、上下文模板、旧版行为和信息边界；不是诊断准确率 |
| 两个真实35B端点的合成接口检查 | **12/12通过** | 各端点规划、指南query、未知工具、停止／执行、reason长度及最终正文模式；0临床病例 |
| 后端约束生效检查 | **2/2通过** | 提示词要求PROMPT_VALUE，schema强制SCHEMA_ENFORCED，两端点都返回后者；确认后端确实应用约束 |
| 真实MCP＋脚本模型完整集成 | **4个合成场景、8个组别全部完成** | T1两例，T2／T3各一例；包括指南检索、共同第一观察、填表与保存回执 |
| 故障注入恢复 | **5次控制JSON错误、8次空诊断均在有限次数内恢复** | 发生在上行8个组别中，不是另13例患者或真实模型故障的准确率验证 |
| 完成后重入 | **8个组别产物指纹不变，无新增模型调用** | 全部回执、账本、日志、checkpoint及预测文件保持一致 |

首次51项测试与最终97项有重叠，不能相加为148项。完整MCP验收使用38次脚本模型响应，实际诊断LLM调用0；实际35B生成仅发生于12＋2项合成接口检查，共14请求／14返回，输入2934、输出4190 token，均记录usage。没有训练任何组件。

完整链中，故意错误后的诊断恢复没有改变可见信息或预算；policy理由与未获准质量标记不进入共同诊断。真实MCP结果保留在验收目录，无伪装成医学判断改善的任意fixture标签。

## 原实验保全与新候选

根目录：`output/s4_control_repair_20261114/`。

- `acceptance_manifest.json`及`acceptance_digest.json`：本次工程阶段、版本、参数、调用数、环境和源码指纹。
- `source_snapshot/`：278份源码／配置／测试等文件，逐文件校验与当前接受版本一致；不是新91例临床协议。
- `candidate.yaml`：已解析绝对资源路径的完整v2配置，SHA256 `1ad41cf444e58c4b04210da0ddf212be8177855fbdd854a5467263e6ba8b90f3`。
- `regression.log`、`backend/`、`backend_canary/`、`integration/`：各类独立证据；实际调用与脚本调用分开。
- `source_and_backend_review.json`：论文／作者实现链接、实际llama.cpp源码SHA、端点身份与模板指纹。
- `v1_preservation.json`：原冻结273份源文件、168份成功预测／trace回执及原统计文件SHA仍一致。原结果准确率没有重算或拼接“修复后”失败例。

验收命令使用`/home/hedongze/venvs/chimera_access/bin/python`，`PYTHONPATH=src:.:tests`，OMP／OpenBLAS各4线程，知识资源离线且reranker开启。工具为`tools/validate_control_repair.py --mode backend|canary|integration --output <全新目录>`。backend／canary会实际生成合成文本，integration使用脚本模型和真实MCP；重复验收必须使用新目录。

## 下一阶段

当前完成的是输出接口的工程修复。有限合成验收不保证所有未来模型输出合法，也没有证明临床正确率提高。下一次正式对照应以这个冻结版本重新登记完整比较，分别报告完成率、LLM判断和信息／计算代价；不得把只重跑原失败例后的结果拼回原主要分母，也不能将这一组合修复的效果归因于某一个子项。S5策略学习、S6 RAG专项、predictor扩维仍后置。

本次验收任务均已结束，尚未部署新临床批次，无需等待后台运行。
