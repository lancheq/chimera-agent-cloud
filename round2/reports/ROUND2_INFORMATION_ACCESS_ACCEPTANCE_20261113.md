# S2：信息权限与成本执行边界验收

2026-11-13。完成P4契约，以及P5中的首个临床MCP执行边界候选。属于运行准备，没有诊断生成、LLM训练或真实患者性能评测。

## 方案与实现

先核对成本敏感特征获取论文§3、MedAgentBench作者仓库部署结构、MCP工具规范和SQLite事务，再形成[本项目契约](../docs/ROUND2_TOOL_ACCESS_COST_CONTRACT_20261113.md)。文献链接、采用部分和未复现部分见契约；不移植其他论文的检查价格或收益。

- `information_access.py`：病例／任务／输入快照绑定，SQLite跨进程账本，执行前原子预留、返回后结算，可见字段集合、缓存和失败记录。
- `mcp_server.py`：新增显式`--access-session`。受控模式只读取绑定病例，取数时检查字节哈希和声明字段，预算拒绝以MCP工具错误返回。不开该参数则沿用旧路径，生产runner默认没有开启新行为。
- [机器契约快照](../configs/tool_access_cost_v1.json)：工具与字段、信息单位及禁止路径；测试与实际注册表保持一致。信息单位是揭示资料栏的研究代理，真实货币价格为空，尚未选定S4实验预算。

重复缓存不重复收信息单位，仍记调用与耗时；失败不记成功揭示、释放预留但消耗获准调用；不存在的资料不加入可见集合；显式空资料栏也算取得信息。崩溃遗留预留不自动回收。已有标量不等于整个检验／报告栏都已免费可见，初始完整栏必须有实际prompt及hash证明。

## 验证

25项相关单元／回归测试通过，另增加1项机器契约一致性测试通过，共26项不同测试。原有合成枚举序列化警告1项。测试覆盖并发、跨病例、失败／空资料／缓存、来源和预算绑定、旧运行边界以及真实stdio重连。

独立复现脚本又进行了5次真实MCP调用，每次新开服务进程，病例完全合成：

| 请求 | 结果 |
|---|---|
| 获取A的MRI | 成功，新增信息支出1单位 |
| 重连后获取同一MRI | 缓存返回，支出仍为1，获准调用累计2 |
| 再重连获取化验 | 超预算，`isError=true`，未返回化验内容 |
| 请求其他病例B | 越权拒绝，不暴露其他病例信息 |
| 查未验收版本的指南 | 显式拒绝，不调用检索后端 |

最终预留0，可见集合只有radiology_report；5个请求有各自请求ID，重启未重置预算。

产物在 `output/s2_information_access_20261113/`：[summary.json](../output/s2_information_access_20261113/summary.json)、[transport_calls.json](../output/s2_information_access_20261113/transport_calls.json)、ledger_snapshot.json、ledger.sqlite、可信会话配置和测试记录。缓存及病例均为合成资料。

```bash
PYTHONPATH=src:. /home/hedongze/venvs/tabpfn/bin/python tools/validate_information_access.py \
  --output-dir output/s2_new_acceptance
```

输出目录必须不存在。此命令没有LLM调用，不构成容器或诊断准确率验收。

## P5剩余门槛

1. runner逐病例创建／恢复可信会话，接入合法余额、动作目的和事件；不能把一个病例配置套给全部病例。
2. planner预执行、few-shot按ID补特征、predictor和影像分支使用同一合法可见集合；本轮未修复这些旁路。
3. 指南资源manifest／版本和影像来源授权。账本核心支持已声明知识快照的0患者信息费／有调用上限测试，但受控MCP当前拒绝尚未验证的RAG／影像，不声称完整接通。
4. Phase2初始可见项对应的工具说明、模型token成本、共同停止／错误处理、整图与实际容器验收。

因此已证明临床工具边界有实际约束，尚未证明全agent权限闭环或自主取证优势。下一步继续P5接线／工程验收，不部署S4性能批次。当前无后台任务，无需等待。
