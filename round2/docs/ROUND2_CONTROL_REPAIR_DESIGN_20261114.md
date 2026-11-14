# 自主取证控制接口修复 v2：原始来源与适配

范围为S3/S4已有候选的控制输出可靠性，不新增策略学习、RAG算法、患者字段或预算。2026-11-14用户要求“完成修复”；授权实现、故障重现与工程验收，不将新临床对照混入既有91例结果。

## 先核查的原始来源

- Willard & Louf, *Efficient Guided Generation for Large Language Models*：[论文](https://arxiv.org/abs/2307.09702)，[作者实现Outlines](https://github.com/dottxt-ai/outlines)。采用生成时约束结构的思路，不新增Outlines依赖或声称结构正确保证诊断正确。
- [llama.cpp grammar文档](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)及[server文档](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)：`response_format`可传JSON Schema；仅支持Schema子集，schema还需在prompt中说明；保留客户端语义校验，不能仅凭HTTP200认定全部约束有效。
- 实际服务源码位于`/home/hedongze/llama.cpp-master`，由`/home/hedongze/llama-build/CMakeCache.txt`确认。`tools/server/server-common.cpp`实际解析`response_format`和`chat_template_kwargs`；`examples/json_schema_to_grammar.py`实现union、数组上下限、字符串长度。对当前两个端点做合成生成验收，避免把最新版网站支持当作本地可用保证。

## 修复契约

v1仍可复现，原冻结快照、配置、91例产物不写入。v2由新overlay显式选择，具有不同run_id及源码绑定；旧账本和输出不能直接接续为v2。

1. 规划和policy按当时可用工具生成受限Schema：工具名固定、临床query只能null、指南query必须非空、purpose必填、禁止额外字段、数组不超过剩余动作数，reason限512字符。模型看到同一Schema，返回后仍执行Pydantic及工具语义校验。
2. v2 policy只输出`remaining_plan`和`reason`。空列表表示停止，非空表示执行第一项；内部转换成既有Decision供日志和状态机使用。不是把旧版非法execute空计划宽松放行，也不按GT修复动作。理由和计划仍不进入医学证据。
3. 控制格式最多两次尝试；第二次使用同证据、错误说明和关闭thinking的输出模式，将有限输出空间留给JSON。Schema不被后端支持时直接报错，不降级到无约束生成。截断响应不当作完整控制对象。
4. 共同诊断第一次维持既有推理设置；若正文为空或请求工具，最多一次同证据最终正文重试（禁用thinking，不能取新信息）。仍不合法就终止失败，不补默认诊断。两个组别使用同一恢复规则，所有请求、参数、失败、响应及用量落盘；已记录返回在恢复时复用。
5. 精确上下文检查对重试使用的模板参数一并计数。传输错误、权限漂移、不明工具返回不走输出修补；不重置预算，不重取信息，不隐藏模型重试成本。模型调用上界纳入共同诊断恢复。

## 验收与结论边界

先用单元／状态机测试重现非法工具、缺query／purpose、超长reason、空计划停止、截断、后端错误、空诊断恢复、恢复不重复调用与预算约束；再以合成材料运行本地35B端点的有限Schema／最终正文检查，并做真实MCP＋脚本模型的候选集成验收。工程结果与新增真实模型调用数分开记录，均不估计患者准确率。通过后冻结新的工程候选；下一次临床比较须另行登记新版本，不能只重跑旧失败例拼接主结果。

## 最终交付

上述方案已实现并通过验收，详见[2026-11-14验收报告](../reports/ROUND2_CONTROL_REPAIR_ACCEPTANCE_20261114.md)。冻结候选及机器验收清单位于`output/s4_control_repair_20261114/`。本次工程阶段结束，临床效果另验。
