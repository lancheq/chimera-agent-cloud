# P3：旧冲突候选的来源、日期与量纲复核

2026-11-13。属于S1运行准备，覆盖三来源T1旧筛查候选；不读取GT，不选择真值，不改原始输入或生产解析器，不测准确率。

## 结果

复核171条候选，涉及156个病例版本记录、60个保守风险组。跨版本副本不计为独立病例。最终产物在 `output/round2_conflict_review_20261113_v2/`；首版 `output/round2_conflict_review_20261113/` 保留，后续补齐明确历史／当前比较的处理。

| 解释分类 | 条目 | 处理 |
|---|---:|---|
| 旧程序把PSA density识别为血清PSA | 99 | 关闭该筛查误报，保留原文和两种量，不宣称整例无冲突 |
| 旧浮点计算在5%筛查边界触发 | 3 | 使用十进制重核阈值，不将5%当临床等价标准 |
| 原文明确分别为历史值及now当前值 | 3 | 保留两时点，不当作同次测量冲突 |
| PSA／体积与密度算术残差，测量／精度未对齐 | 58 | 保留待查，不反算替换任一字段 |
| PSA来源／时点未完全对齐 | 4 | 保留各来源及未知日期，部分已知不同日期不替其余缺失日期作推断 |
| 卡片与MRI密度来源不同 | 2 | 保留待查，不按某侧更接近GT选值 |
| 空活检史与原文既往活检记载不符 | 2 | 标为需核对历史，不根据“提到biopsy”自动认定结果 |

合计105条筛查标记得到解释，66条保留未裁定，分布在29个保守风险组。**105是候选标记数，不是纠正了105个患者；66也不等于确认66处医学错误。**

## 可复查实例

[公开版省略逐例记录；汇总与机制结论保留。]

## 交付

- [conflict_review_ledger.jsonl](../output/round2_conflict_review_20261113_v2/conflict_review_ledger.jsonl)：逐候选原标记、原文／路径、日期精度、单位状态、来源SHA256、审查分类和限制。
- [质量状态表](../output/round2_conflict_review_20261113_v2/annotations/data_quality_annotations.json)：全部1275条记录的范围／状态；未触发旧筛查不等于已认证干净，T2/T3标为本轮范围外。
- [未裁定队列](../output/round2_conflict_review_20261113_v2/annotations/unresolved_review_queue.json)：按跨任务、组规模与来源证据排序，未使用GT或预测对错筛选；不自动删病例。
- [汇总](../output/round2_conflict_review_20261113_v2/summary.json)、[交接摘要](../output/round2_conflict_review_20261113_v2/annotations/summary.json)、target_read_guard.json及source_hashes.json：数量、参考内容读取保护与复现依据。

**台账含审计时查看的隐藏临床原文，属于离线质量审查层，不能作为planner、LLM、predictor或few-shot的免费输入。** 审计看过某字段不授予agent访问权限；后续仍需按S2合法取证。未生成清洗版训练／测试输入，也没有改变当前运行时解析行为。

## 验证与复现

22项相关测试通过，覆盖量纲隔离、明确历史／当前关系、月与日精度、报告／就诊日期不冒充采样日期、未来／否定活检描述、缺失单位、算术残差不自动改值，以及既有数据分组与目标文件隔离。输入3834份前后哈希未变；GT／reasoning／metrics文件内容读取尝试0；模型调用0。

两个可复用入口（目标目录均须不存在）：

```bash
PYTHONPATH=src:. /home/hedongze/venvs/tabpfn/bin/python tools/review_round2_conflicts.py \
  --output-dir output/p3_new_review
/home/hedongze/venvs/tabpfn/bin/python tools/annotate_round2_conflict_review.py \
  --pool output/round2_data_pool_20261113_v2 --review output/p3_new_review \
  --output-dir output/p3_new_review/annotations
```

范围限于已冻结T1旧候选，不能声称完整查尽全部T1/T2/T3矛盾或获得临床真值。进入正式性能研究前仍需冻结版本、分组、共享冲突处理与隔离约定；未裁定项不能被按答案筛掉。

P3第一轮可复查来源审查已交付，不等待所有临床争议解决才开始基础设施准备。下一项P4进入S2：先核查对应已有方法／官方工具实现，定义工具字段权限、费用种类、成功／失败／重复／缓存规则，再实现共同执行边界。两组须使用相同原始版本及共同约定，不把审查发现当隐性额外信息。当前没有新后台任务，无需等待。
