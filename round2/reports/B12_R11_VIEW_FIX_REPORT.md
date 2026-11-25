# B12-R11-VIEW-FIX · T1 模型可见工具正文

**PASS_CANDIDATE_ISOLATED_T1，待规划独立验收。** 隔离副本通过 27/27 项零生成探针。启用后，原 R11 第 2 次 SDK 请求保留合格实验室行，迟到行留在私有回执。原调用 ID、1 次准入、1 个合成信息单位和两份 Socket 指纹保持。生产源文件未改。

## 实施边界

候选默认关闭，凭证由现有 `synthetic_test_only` 正式入口注入的 `SyntheticEvidence` 显式选择。候选仅接受 T1 guarded Direct ReAct 与本次核对的两种合成标量行。工具注册表、原守卫与 SDK 计量器来自冻结基础。实际 stdio 服务使用未修改源码，字符串对照走同一 FastMCP handler 及原账本。

接线处于 Guard 完成原结果核验和结算之后、ToolNode 更新提交 Graph 之前。候选核对同运行、病例、会话、活动工具名与实参、`tool_call_id`、原文摘要及唯一预留和成功结算。已绑定的日期元数据指向同一摘要和原行位置。源报日期、结算时刻与未知可得性不能替代历史可得凭证。原 UTF-8 正文、native 内容及资格审计在提交安全 ToolMessage 前写入私有链。SDK 发送前以不可变的视图指纹和干净的消息形状复核。B 注记按保留的行并重映射位置，避免引用已扣留或移动前的模型位置。

本次只改隔离副本的 `agent/graph.py`、`agent/evidence_reconciliation.py`，并新增 `agent/model_tool_view.py`。原生形状核验、账本、入口、表单、Socket 与默认 profile 均使用原文件。适配仅支持具有原合同结算凭证的已复核输入形状。缓存回执缺少足以证明本次原正文的关联，本轮在发布前拒绝，保留原准入和实际收费记录。

[源码差分](/home/hedongze/CHIMERA-agent/output/b12_r11_view_fix_20261122/candidate.diff) · [逐案期望与实际](/home/hedongze/CHIMERA-agent/output/b12_r11_view_fix_20261122/expected_actual.jsonl)

## 原 R11 对照

| 检查项 | 默认关 | 显式启用 |
| --- | --- | --- |
| 对照输入 | 正式 T1 同一合成 R11，真实 MCP stdio 与 MockTransport；字符串对照同义 | 同左 |
| 迟到哨兵 | 第 2 次 agent SDK 请求的 `messages[3]` | 三次 SDK 请求均无 |
| 合格正文 | 原两行同时送达 | 单独保留合法原行，值和类型不变 |
| 第 2 次请求计量 | 9,445 个合成 UTF-8 字节计数单位 | 9,340 个合成 UTF-8 字节计数单位 |
| 工具与原结算 | `call-fixture-lab`，`reserved → returned`，1 次准入、1 单位 | 相同 |
| 两路 Socket | 与封存原 R11 一致 | 与封存原 R11 一致 |

第 1、3 次 SDK 请求在两臂字节等值。实际 HTTP MockTransport、有效 SDK 请求摘要、计数输入和输出预留逐次联结。上表计数属于夹具，真实模型模板 token 继续待验。原生与字符串对照在启用、关闭及整段合法三组均有相同的请求和结果指纹。

[双臂对照](/home/hedongze/CHIMERA-agent/output/b12_r11_view_fix_20261122/baseline_delta.json)

## 正反例验收

- **通过原 R11、完整正文、原顺序与类型。** 合法整段在两个传输路径均原样提交。另测缺失值和数值字符串、PSA trend 标量行、移除首行后 B 注记路径重映射。
- **合法结算、内容扣留。** 全迟到、未证可得性、混杂不可分、单位未获认证时，保留工具角色、名称和 ID，以固定无临床含义的占位继续。正向邻行在缺证或精度重叠时仍可使用。只扣留时账本照计，模型预算显示实付但无可见栏，既有表单标签及 reveal 路径没有把占位当临床证据。两路 Socket 格式有效。
- **失败与停止。** 错病例、错摘要、伪形状、多文本、重复 JSON 键、重复 ID、不可核对缓存与私有审计失败均在下一次 SDK 请求前停止。缺 cutoff 在首个模型请求前停止。原后端失败及表单无效响应没有成功 Socket。合法工具预算拒绝按原账本 status 继续，不产生费用或未经授权的后端读取。
- **私有与模型视图分离。** 已取得的原字节、日期、结算和 native artifact 可由私有回执复算。启用后，原 artifact 和测试负哨兵不在 Graph/填表历史、SDK 或 Socket。完整回放包含合法正向哨兵，覆盖没有靠整体拒绝取得。

首轮测试包装器未捕获 `RuntimeFatal`，已保留该日志。定向回读另区分表单失败的内外层原因，外层为缺预测、内层为 Guard 拒绝默认结果。最终矩阵两层均核验，候选源码没有为这两个读取问题放宽错误合同。

## 资源、交接与停点

正式验收矩阵共 62 次 SDK MockTransport、26 次合成准入尝试、24 次合成后端读取及 23 个合成单位，16 对 Socket。单 CPU 核亲和，stdio 助手串行，矩阵约 42 秒。全程 0 真实模型、GPU、患者、临床工具或真实信息单位。344 个受保护输入在本轮前后指纹一致。无后台任务。

T1 初始输入视图、恢复、其他工具形状与合法缓存、T2/T3、Adaptive、Reflection、旧 prefetch、生产接线及 B12-LIVE 仍为 HOLD 或另批范围。T3 纵向证据保持原 SKIP。本卡以隔离候选停下。

~~~bash
bash /home/hedongze/CHIMERA-agent/output/b12_r11_view_fix_20261122/check.sh --check
~~~

[交接与实测资源](/home/hedongze/CHIMERA-agent/output/b12_r11_view_fix_20261122/handoff.json)
