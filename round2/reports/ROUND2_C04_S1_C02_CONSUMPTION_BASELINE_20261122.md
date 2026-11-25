# C04-S1 · 六条冻结查询的 C02 消费基线

2026-11-22，北京时间 21:13–21:15 · 只读研究候选，执行交付待规划验收

**六条查询下既有 C02 返回 0 条参考。** 48 个原始位置在控制臂全部送达模型可见请求，启用 C02 后一条也不进模型可见载荷；3 条已登记的完整同场景推荐（Q02 第 5 位、Q03 第 2 位、Q04 第 4 位）在控制臂逐字可核，在 C02 臂全部丢失。丢失发生在来源绑定的覆盖层：这 48 个位置里没有任何一条属于 overlay 的 `conditional_reference`。

## 对象与方法

输入全部复用，未重跑检索。六条原查询取自 `output/planning_C01_retrieval_20261118/query_stages_private.json`，48 个有序返回位置取自 `output/planning_C01_frozen_retrieval_replay_20261118/Q0*_stages_private.json`。切片正文从冻结知识快照 `guidelines_db` 按 record_id 取出，QA 正文按 `rag.py` 的 `_qa_search` 格式重建；48/48 条文本的 SHA256 与冻结记录逐一相符，因此这批文本可视为原返回的字节等价物。

代码用 `output/evidence_package_candidate_20261117/frozen/`（overlay 2f61e423…、`evidence_package.py` 97974ac0…、`information_access.py` 93d1ced1…）。每条查询跑两臂：控制臂走 `packager=None` 的原返回（`{'query','results'}`），C02 臂走 `EvidencePackager.package()` 后 `access.execute('search_guidelines',…)`。两臂各用独立账本，避免知识缓存串臂。

患者事实只从两处取：`data/task1/agent_input/<case>/structured-prompt.json` 顶层字段，以及该次请求之前已取得的 `get_mri_report` 原返回（从历史 trace 原样取出，经 `InformationAccess.execute` 授予一次，花 1 个单位，预算 3 单位）。未读 GT、未读未授权字段、未用模型参数补可见性。

模型可见性验收到请求序列化：用生产同款 `langchain_openai.ChatOpenAI` 客户端指向 127.0.0.1 环回捕获服务器，取实际 POST 请求体字节。响应由本地桩返回固定字符串，无生成、无 GPU、无 35B 调用。

开销：4 线程，脚本自计墙钟 10.83–11.27 秒（三次独立运行），含解释器启动约 13.8 秒，峰值 RSS 595–596 MB，检索重跑 0 次，临床工具新增读取 0 次。

## 结果

| 查询 | 病例 | 臂 | 控制臂送达 | C02 参考 | C02 状态 | 完整目标位次 | 控制臂可见 | C02 臂可见 |
|---|---|---|---:|---:|---|---:|---|---|
| Q01 | 562159dc2cc6 | fixed | 8/8 | 0 | no_verified_reference | 无登记 | — | — |
| Q02 | 562159dc2cc6 | adaptive | 8/8 | 0 | no_verified_reference | 5 | 是 | 否 |
| Q03 | 4f33b0253633 | fixed | 8/8 | 0 | no_verified_reference | 2 | 是 | 否 |
| Q04 | 4f33b0253633 | adaptive | 8/8 | 0 | no_verified_reference | 4 | 是 | 否 |
| Q05 | c39e94a13920 | fixed | 8/8 | 0 | no_verified_reference | 无登记 | — | — |
| Q06 | c39e94a13920 | adaptive | 8/8 | 0 | no_verified_reference | 无登记 | — | — |

48 个位置的 C02 处置分布：`unknown_source_binding` 38、`directory` 5、`unverified_task_adaptation` 5、`conditional_reference` 0。overlay 共 6 条命中，其中 2 条是 `conditional_reference`，这 2 条都不在六条查询的返回窗内。

| 读数 | 控制臂 | C02 臂 | 差 |
|---|---:|---:|---:|
| 六次请求体合计 | 98,442 字节 | 61,940 字节 | −36,502 |
| 工具载荷合计 | 36,976 字节 | 2,235 字节 | −34,741（−94%） |
| 单次工具载荷 | 5,521–6,725 字节 | 366–383 字节 | — |
| 消息内容 token 合计 | 28,283 | 17,076 | −39.6% |
| 模型可见位置 | 48 | 0 | −48 |
| `automatic_action_authorized` | 不出现 | 恒 false | — |

事实层在四例上退化为未知。三例 MRI 返回都是散文本，C02 只接受规范三行 IMPRESSION 或无额外修饰的结构值，因此 `pi_rads`、`psa_density` 六条查询全部 `unknown`；`family_history_prostate_cancer` 同样六条全 `unknown`；`biopsy_context` 按 `bx` 分为三类：Q01/Q02 `unknown`（`bx=None`，不当初次）、Q03/Q04 `prior_negative`、Q05/Q06 `known_cancer`。

## 判读

三个正例的丢失都在 C02 之前。Q02 第 5 位的 EAU 2025 §5.5.7 阴性 MRI 条款、Q03 第 2 位的 §5.7.1、Q04 第 4 位的 §5.7.1，在控制臂里都是逐字可达的模型可见内容，在 C02 臂里连 disposition 都只记为 `unknown_source_binding`。这不是判别错误，是覆盖缺失。

目录与派生摘要的降级在真实返回上成立。5 个目录、5 个任务摘要全部没有升格为行动依据，`automatic_action_authorized` 恒为 false，位置顺序与原始 8 位一致。A01 提出的那类风险在本样本里被挡住了。

C02 把工具载荷压到原来的 6%，但 note 只写 `no_verified_reference` 加一个计数，没有逐位置说明哪条被拒、为什么。被拒理由留在私有审计 `evidence_package_audit.jsonl` 里，模型看不到。模型无法区分「本来就没有证据」和「有证据但未核实」，这两种情况在本轮六条查询里全部被判成前者。这是本轮最该记下来的可用性缺陷。

错误作用域分支没有被检验。Q05/Q06 是 `known_cancer`，`scope_mismatch_known_cancer` 的排除逻辑本该在条件参考上触发，但这两个窗口里没有 `conditional_reference` 命中，所以本轮无法对这条规则给出证据，只能记 `UNKNOWN`。

存储层面还有一个限制：历史 trace 把检索工具内容截到 8000 字符（末尾停在半个词上），请求体原始字节没有保存。控制臂是从同一批冻结位置重建的，绝对历史请求字节因此记 `UNKNOWN`；能证明的是这 48 个位置的内容与顺序，不是当年那次 POST 的完整字节。

## 分支判定

接续卡给了两条后继分支，本轮的失败不属于其中任何一条：

- 不是「证据在候选池内、被重排或返回窗丢掉」。Q02/Q03/Q04 的目标位次 5/2/4 都落在最终窗口里，重排和窗口没有丢它们。
- 不是「已送达且既有判断误判支持性或适用性」。C02 一条都没有输出，没有可以判错的判断。
- 是第三个分支：**来源绑定覆盖缺失**。按接续卡「材料在池外时归回召回/索引」，这条归索引层，但对象是 overlay 的条目覆盖，不是向量库召回。

据此不提交固定候选池重排方案，也不登记轻量 NLI 调查理由。下一轮只做一件事：把这三条已送达的完整原文补进 overlay，再重测消费。详见 `next_candidate.md`。

## 未做与未知

- 未跑官方容器，未接 `127.0.0.1:8765` 的 `/props` 与 `/tokenize`。配置里带 tokenizer 桩会让 `validate_delivery` 走 `tokenizer_unavailable`，本轮按未配置处理，token 用 GGUF 仅词表的 CPU 分词器单独计数，与字节分列。
- 未重跑任何检索、未改配置、未动生产入口、未操作平台。
- 3 条正例的「完整」沿用 C01 的登记口径，本轮不重新裁定医学完整性。
- 91 例里调用过 `search_guidelines` 的实际比例仍是 `UNKNOWN`，本基线只覆盖已暴露的 6 条查询。
- 若 C02 覆盖扩展后仍有正例被拒，才轮到判别类研究。

## 交付

`output/c04_s1_c02_baseline_20261122/`：`frozen_inputs.json`（48 位置与哈希、三例输入指纹、overlay 与知识快照绑定）、`query_consumption_matrix.jsonl`（逐查询六段证据链）、`wire_readback.json`（12 次实际请求的字节、SHA、token 与可见位置）、`resources.json`、`handoff.json`、`ledgers/`（12 个访问账本与证据包审计）、`next_candidate.md`、`replay_c04.py`。

复跑：`cd output/c04_s1_c02_baseline_20261122 && OMP_NUM_THREADS=4 /home/hedongze/miniconda3/envs/chimera/bin/python replay_c04.py`。已独立复跑两次：12 个请求体的 SHA256 三次全部相同，结果可复现。审计日志每次运行前清空，只保留本轮 12 条事件。不一致时以 `wire_readback.json` 的 SHA 为界，先查资源指纹再查代码。
