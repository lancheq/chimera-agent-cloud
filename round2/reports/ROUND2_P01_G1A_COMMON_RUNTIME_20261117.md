# P01-G1A 共用入口与消费证据基础修复

2026-11-18。本地有限交付完成，状态 **G1A_DELIVERED_AWAIT_PLANNING_REVIEW**；机制总体仍 **NOT_READY**。0真实模型生成、0模型端点调用、0 GPU、0训练/下载/部署/构建/上传/平台提交。没有派发其他会话、子Agent或自动进入G1R/G2。

## 接续与修改边界

接续HEAD为`7301642d0eedda2f143ba44b93d42f948249d813`，基于已有dirty工作树；未提交Git、未改staged内容。启动保全197份相关源码/配置/测试/G0/A02文件身份、生产源码原件及dirty/staged diff；不是重做G0的47/89核查或PACK/GIT全审计。本轮8份既有生产文件修改、2份新模块及1份专项测试；既有197份中189份未变，G0/A02文件、旧配置和旧测试无变化。精确边界见[保全核对](../output/p01_g1a_common_runtime_20261117/preservation/check.json)、[本轮源码diff](../output/p01_g1a_common_runtime_20261117/manifest/source_diff.patch)和[实际版本](../output/p01_g1a_common_runtime_20261117/manifest/actual_version.json)。

G0验收和A02契约直接复用；不重复患者/GT或大范围文献调查。没有修改graph、postprocess、predictor、医学Prompt/阈值、默认config、frozen、共享KB/原数据、B01/B02、CURRENT/INDEX或public pending；未对PACK/GIT/C02作重复验收。请求模型为gpt-6.1-sol/high；实际执行模型身份未独立核验，不虚称已验证。

## 完成的工程基础

- 可信入口：代码所有的profile注册表、配置真实路径/SHA、effective config、源码和已导入模块路径、环境与实际加载开关可核对，身份不符在服务/生成之前拒绝，并在每次计量请求前重核。正式提交注册表为空；测试注册表只能经Python注入，目的为`synthetic_test_only`。该上下文不能调用常规load_model或启动embedding服务。无profile保持旧默认行为，但旧默认不是本轮批准的提交profile。
- 全请求接口：OpenAI SDK同步/异步create、bind_tools派生对象和vLLM offline chat共用原子账本。SDK重试必须为0，prefill回退、解析重试、内层反思与atomic模型调用逐次预留input+最大output；失败不退预算。预算、身份、计数器和私有审计失败通过RuntimeFatal越过宽泛临床fallback，不能制造默认成功。
- 模板边界：原纯文本精确计数接口保留；工具role、assistant tool_calls/额外元数据、schema及未验证模板选项明确拒绝，不删内容凑长度。Streaming、Responses和root parse等未验证SDK路径也在发送前拒绝。本轮工具payload完整保留的证明来自fake计数器和MockTransport，**不是live工具模板/tokenizer验收**。
- 私有消费证据：同一run/case/task/stage的完整输入输出digest、字段before/after、请求和响应、填表初始判断、内层反思/atomic、逐层后处理、prediction、align及两个最终socket有序关联。请求在预算核查前先保留完整preflight，因此被拒绝的请求也不丢工具观察结果。最终字段附投影映射、阶段digest和覆盖历史，既有align权威明确标为D05未审定。
- 清理生命周期：私有run目录0700、事件/导出文件0600、事件逐条flush/fsync；成功和失败均在临时目录清理前导出，不跟随symlink。最终两个socket写出并读回核SHA才标finalized，随后导出完成才有run_completed。没有新增平台socket或改变其schema/原有语义。

SDK边界的`wire_attempt`是**保守收费尝试**，可能包括尚未传输即失败的提交；不是服务器收到或真实生成的回执。本轮MockTransport收到的请求数与计量数一致。token预留不等于实际usage、平台工具使用分、患者取证成本或GPU费用。旧shared_plan研究journal保留；未迁控制架构，其JSON-schema路径进入当前适配器会明确拒绝，不能把该限制说成候选已经接通。

完整接口及状态含义见[共用契约v1](../output/p01_g1a_common_runtime_20261117/handoff/runtime_contract.md)和[请求覆盖表](../output/p01_g1a_common_runtime_20261117/handoff/request_coverage.json)。

## 验证及资源

最终 **81 passed，7 warnings**：40项G1A专项，41项相关历史回归。包括真实LangGraph节点调用配合fake响应、plan/agent/form_fill三请求三阶段、sync/async与工具绑定、fake vLLM、prefill失败和解析重试三次独立收费、真实旧self-refine/atomic消费者fatal退出、并发不超额、未知profile在服务前拒绝、身份失配、工具模板拒绝、私有写入失败，以及T1/T2/T3入口stub清理后证据/最终字段一致性。入口stub使用真实填表解析/后处理/align；runner替换为fake，未声称真实临床端到端通过。

[复现入口](../output/p01_g1a_common_runtime_20261117/tests/run_checks.py)、[日志](../output/p01_g1a_common_runtime_20261117/tests/final.log)、[JUnit](../output/p01_g1a_common_runtime_20261117/tests/junit.xml)、[资源及依赖版本](../output/p01_g1a_common_runtime_20261117/tests/metrics.json)。验证进程设置4GiB地址空间上限，最终峰值RSS661104KiB，耗时16.74秒，10ms采样峰值4线程，结束2线程；该测量只覆盖最终验证worker，不代表全会话资源/账单，采样不能排除更短峰值。最多一验证worker加一个短子进程，无临床批次。

过程偏差如实保留：初次pytest被自动插件的socket监听权限阻断；关闭第三方插件自动加载后执行。沙箱下async线程唤醒/收尾挂起通过测试专用timer解决，未改生产事件循环。一次无关旧MCP stdio环境测试挂起后已中止，未计入最终81通过。早期stub少量合成trace曾落默认output/trace/gc-case，文件创建时间确认属本轮新建，已移至G1A tests并修正隔离路径。资源采样曾见5线程；诊断定位为并发预留测试加数值库后台线程，改为主线程+单worker竞争，保留[超限诊断](../output/p01_g1a_common_runtime_20261117/tests/resource_thread_diagnostic.json)，最终复验峰值4。7个warning来自既有Pydantic枚举默认值序列化，未改医学schema消除warning。

[审计示例](../output/p01_g1a_common_runtime_20261117/handoff/audit_example)只含合成软件夹具，可检查两个socket SHA、覆盖历史、事件链以及清理后保留的prediction/processing证据；不包含真实患者。所有材料只留本地，未公开同步或称已回读。实际源码SHA和依赖版本纳入manifest；没有构建发布产物。

## 未满足门槛与下一阶段

外部reflect写refined_transcript、填表仍读messages的缺口**已确认且未修复**。本轮分别保留实际填表输入与原始反思artifact，显式`proposal=null / verdict=not_requested / acceptance=not_implemented_G1R / consumed=false`；记录提议不等于接纳。内层反思仅观察原有行为，不新增审核权威、不启用生产Reflection、不删节点。

G1R依赖：G1A接口经规划验收+A02最小契约经规划审查之后，另发单写有限Goal，实施完整候选、绑定审核、唯一选择/实际消费回执；先覆盖无患者合成接纳、拒绝、无变化、异常/回退及最终字段一致性，仍0真实生成。本轮不实施选择器或授权token，不把记录artifact当消费链修复。

D01控制器、D02两套反思/atomic/审核、D03 predictor/影像/few-shot权限、D04 C02/知识、D05后处理/align最终字段权威仍未冻结；C02/控制器/predictor完整接通、真实自然触发、临床正确性与效果均未证明。D06仅交接口与fatal语义，D07仅工程私有证据保全。G2的60次上限/总token仍为草案，真实生成授权0。停止供规划审查，不自动续跑其他G1或G2。

交付结束无后台任务，无需等待，也不会自动唤醒或通知。
