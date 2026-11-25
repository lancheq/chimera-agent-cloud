# Stage 2 · 消融A 报告 (atomic_check=true, self_refine=false)

- 时间: 2026-08-29 10:16:16
- 配置: agent.form_fill.self_refine=false, agent.form_fill.atomic_check=true, decision_override_tasks=[]
- 输出: test/output.ablation_A_atomic_20260829 (30 case: T1:12+T2:10+T3:8)
- server: 复用 Stage1 的 35B llama_cpp.server (8765)

## 运行统计
- 写入: T1=11 (1 failed), T2=9 (1 failed), T3=8
- 失败 case:
  - PT-pseudo_4bfd4ec864d8 (T1): Task1Output._fact_check_flags extra_forbidden (atomic_check schema 不兼容)
  - T2-007 (T2): form_fill 3 次解析失败 (DynamicTask2Judgment.variable_weights missing, LLM 输出空)
- 失败率: 2/30 = 6.7% (<15%, 继续)

## atomic_check / fact_check 触发
- fact_check 日志行数: 59 (每个 case 均有 decompose_claims + 判定)
- 典型输出: "decomposed N claims ... X TRUE, Y FALSE, Z UNVERIFIED"
- 注意: 多数 case 出现 "LLM decompose_claims failed: Extra data" WARNING (JSON 尾随字符), 但 fallback 解析成功

## 结论
- atomic_check 生效, fact_check 每 case 均执行
- 发现 1 个 schema 兼容性 bug (fact_check_flags extra input), 需 L2 验证修复 (deferred)
