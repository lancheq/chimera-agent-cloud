# Phase 2 执行链验收与下一单元边界

日期：2026-11-11。分类：运行准备/链路验收。此报告不报告准确率提升，不将小样本 smoke 当完整 Phase 2 评测。

运行目录：`output/phase2_acceptance_20261111_run01/`。来源为已校验的 `phase2_train_release_20261104`，不合并到旧 `data/taskN`。选择仅使用输入：T1 的 PI-RADS 2/3/5 各一例、T2 的输入 AI grade 低/高各一例、T3 排序首例，共六例；没有依据 GT 或历史预测挑样本。`inputs.json` 只引用 prompt、clinical 和可用表征文件，不复制标签。

## 运行准备：模型资产

原提交模型目录下，embedding/reranker 只有配置和 tokenizer，两个 `model.safetensors` 均缺失。原状保留在 `submission_asset_preflight.json`（FAIL）。先用独立模型挂载引用完整 `resources/` 权重运行入口，避免将环境缺件与取证问题混淆。

随后逐一核对两个目录现有小文件与 `resources/`：无配置/模块冲突。仅补入缺失权重，不覆盖已有文件，也未修改任何导出/上传 tar 包：

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| embedding_model/model.safetensors | 1,211,486,072 | `cbf5a78393b6a033e0b8a63a57549964f7ed5c6fbeb4ba0694214f36123f2fd2` |
| reranker_model/model.safetensors | 1,112,206,140 | `ced967c45fd1902eb92716c9ceeca7c95a936770ea9db611f5a841b926e33fbd` |

源/目标 hash 一致。实际 `model/` 路径的 CPU 加载/前向测试通过：embedding 768 维且有限，reranker 输出 1×1 且有限。探针只证明加载与执行，不证明检索质量或模型实现完全符合训练时语义。

加载日志另有已观察到的兼容性警告：Sentence Transformers 5.7.0 产物运行在 5.6.1，Transformers 4.51.3 对 `use_bidirectional_attention=True` 的支持可能不足。未据警告断言 attention 一定错误，也未在正在运行的基线上升级依赖；该项保留为运行时一致性待验。完整日志见 `/tmp/phase2_asset_load_probe.log`，关键探针结果已保存到运行目录。

补齐权重后，默认开启的 reranker 能实际加载，不再因权重缺失回退。此配置完整性修复不计入提分，也不能与昨晚显式关闭 reranker 的实验直接配对比较。六例本身从开始即使用相同完整挂载和开启 reranker 的严格配置，未中途切换。

## 真实入口检查范围

调用实际 `inference.run()`，使用本地路径替代 GC 挂载路径；保留其临时输入和所有输出/trace。适配器自行加入匿名 `gc-case` 与 task，未在送给 agent 的 prompt 中补入隐藏字段。

- 使用本机现有 Qwen 35B 服务 8766，真实调用 LLM/MCP；不是缓存重放。
- `CHIMERA_STRICT_RUNTIME=1`，T1 决策层固定服务器当前 v2，不冒充未确认的 v15 配置。
- 检查 MCP 返回内容与源字段一致；检查模型摘要是否明确引用源 PI-RADS；检查 predictor 实际执行、阶段快照及最终 decision/reasoning socket 一致。
- 没有 Docker/Podman，使用的是宿主机依赖，不是实际容器验收。源 Dockerfile/config 的本地 HTTP 模型服务启动链也仍需在真正离线镜像中单独验证。

六例最终结果见 `status.json` 和 `evidence_review.json`。运行于 12:08–12:24，六次真实入口均返回 0；工具 payload 与源字段、匿名 task/case 绑定、预测器执行、阶段审计及两个 socket 的一致性检查均为 6/6。六例模型摘要均明确引用对应源报告的 PI-RADS。关键组件 fallback 为 0，六次核心源码及 v2 系数 hash 一致。

| 范围 | 样例数 | 执行链检查通过 | headline 特征契约检查通过 |
|---|---:|---:|---:|
| T1 | 3 | 3 | 0 |
| T2 | 2 | 2 | 0 |
| T3 | 1 | 1 | 1 |

执行链 smoke 通过不等于研究基线通过。最终 `--require-ready` 退出码为 1，`ready_for_mechanism_research=false`，没有把“输出了六份 JSON”作为成功结论。这里只对选定病例检查明确的 headline 输入契约，不要求所有临床特征都非缺失，也不证明 T3 全体数据已验收。

验证：现有测试与新增覆盖共 37 项通过（另有已记录的旧 enum 序列化 warning）；新增测试证明六例的选择/暂存不读取标签、不向 prompt 注入隐藏字段，并验证工具返回解析。模型资产修复前后及主机依赖探针单独留存，不伪装成 Docker 测试。

## 输入契约审计：当前研究基线仍有阻塞

`audit_phase2_feature_path.py` 只读两份发布输入和现有 `build_case_features`，不读标签、不恢复字段、不改变模型。对全部 423 例按当前装配路径检查，即使给予完整 clinical 可见性：

| 任务 | 旧版主要输入缺失 | Phase 2 当前装配结果 |
|---|---|---|
| T1，195 例 | PSA/PSAD/vol/cspca 均 0 例缺失；pirads_int 1 例缺失 | PSA/PSAD/vol/cspca/pirads_int 均 195 例缺失 |
| T2，153 例 | 上述字段及 Gleason 主/次级均 0 例缺失 | 上述字段及 Gleason 主/次级均 153 例缺失 |
| T3，75 例 | 主要 PSA/age 不缺失 | 主要 PSA/age 仍不缺失 |

一些报告派生字段（如 pirads_max、psa_trend_last、path_isup）依旧可用，所以不能说 predictor 完全没看到报告。但它的主要训练输入消失、继续由插补/其他特征完成预测，属于必须核查的输入契约漂移。真实 T1 trace 同时证明：LLM 已从工具看到关键数值，而 predictor 的 headline 特征仍缺失。这不是单纯“LLM 不会取证”的问题。

另外，当前 MRI 工具仅返回 `radiology_report`；新数据中的独立 `clinical.cspca` 并未随该工具返回。后续不能为了补齐特征而直接偷读这个未揭示字段，必须先确定并验证合法的工具证据路径。

因此，`review_phase2_acceptance.py --require-ready` 同时检查执行和选定病例的 headline 特征契约；只输出 JSON/模型成功返回不能使研究就绪门通过。缺失字段仍在时，不继续做决策层阈值搜索。

## 初步机制观测：现有决策层的作用范围

实际系数文件为 `v2_simplified`，仅使用 `margin` 与 `llm_yes`；不使用已弃用的 PSAD/ISUP 条件变量。其 logit 为：

```text
z = -1.3880261905 + 1.2618964660 × (p_yes - p_no) + 1.4771010649 × 1[LLM=yes]
final=yes 当且仅当 z > 0
```

在有效二分类概率范围内，LLM=no 时最大 z 为 −0.1261，无法改成 yes；LLM=yes 时仅在 p_yes > 0.464706 时保留 yes。它在当前系数下是一个单向否决层，而不是可双向改判的融合器。此处是代码/数学行为分析，不是效果或因果归因结论。

三例 T1 已观察到两个 `parsed/verified=yes → decision_layer=no`，另一个保持 no；没读取标签判定这些变化对错。输入特征漂移可能通过 predictor 概率影响否决，但具体净收益必须在修复/确认输入契约后，用固定证据、初始判断和决策层作配对验证。

## 下一单元：只处理一个机制

下一研究单元先收窄到 **T1 取证后的特征装配/输入契约**。固定 planner、LLM、RAG、模型权重与 v2 系数；不同时重训模型、调阈值或尝试新架构。先明确字段来源、时点、单位与未揭示/缺失的处理，核实可恢复字段与冲突；不能在工具调用前偷读隐藏资料，也不能用旧标签选择冲突数值。

工程门通过后，才使用同份已取得证据和初始 judgment 做明确标注的离线后处理对照，再决定是否需要真实 LLM 复跑、分组 OOF 验证。输入字段恢复本身不等于诊断提分；修复/破坏病例、决策 gate、证据可靠性和新引入错误都要报告。T2、决策层参数优化与检索优化暂不并行展开。
