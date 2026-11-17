# P01-GIT：公开 Git 交付只读审查

日期：2026-11-17，北京时间约17:00完成。Task ID：P01-GIT；阶段：P01／S0、S2提交工程准备。判定：**公开可见与阶段源码核查 PASS；独立可构建／C02提交复现 NOT_READY**。本项未发布或更改远端；已有公开仓库无需重复“开放”。

## 范围与版本证据

按 AGENTS.md、discussion/SYNC_WORKFLOW.md、docs/ROUND2_SUBMISSION_SMOKE_FIRST_PLAN_20261117.md、P01及C02任务卡执行只读审查。唯一改变为新增本报告与 output/p01_public_release_audit_20261117/ 元信息证据；没有修改生产代码、默认配置、共享CURRENT／INDEX或旧冻结快照，没有运行checkpoint，没有读写 public_sync/pending。

- 已连接GitHub GET回读仓库元信息：private=false、visibility=public、archived=false、disabled=false、default_branch=main。
- 当前main回读：`1cb14f6ea4f39683fb9864a56749576be61e25f0`，与父会话提供版本一致。
- 固定commit递归tree未截断：全仓库275个blob，其中round2为171个。父会话的187文件是当次导出／回读范围，不能当作整个远端文件总数。
- 最新公开checkpoint：`20261117-164221-submission-smoke-first`；其manifest登记170个工作区来源文件，source_git_head为`7301642d0eedda2f143ba44b93d42f948249d813`。工作区脏，不能只用本地HEAD标识第二轮版本。
- 抽核17个当前工程路径，其中14个已公开round2文件的Git blob逐字节相同；另3个路径未在round2公开（.dockerignore、do_build.sh、tools/check_submission_imports.py）。包含Dockerfile、requirements、pyproject、inference、C02模块／配置／构建与验收工具的匹配。
- 对当前src的绝对内部import作AST路径检查，未发现对应round2模块缺失；这仅证明静态路径闭合，不是Python导入、安装、模型或镜像验收。
- 独立无鉴权API GET得到403 rate limit exceeded；故“匿名终端此时可正常克隆／网页访问”未验证。已连接GitHub可读和仓库公开属性已验证，403不被解释为仓库私有。若外部评审访问受限，应由另一普通浏览器核查，而非重复改visibility。
- 未收到或读取尚在补传的Mac踩坑总账；不推断Mac是否存在可复用新镜像／recipe。

远端证据：[仓库](https://github.com/lancheq/chimera-agent-cloud)、[已回读commit](https://github.com/lancheq/chimera-agent-cloud/commit/1cb14f6ea4f39683fb9864a56749576be61e25f0)、[round2目录说明](https://github.com/lancheq/chimera-agent-cloud/blob/1cb14f6ea4f39683fb9864a56749576be61e25f0/round2/README.md)、[最新manifest](https://github.com/lancheq/chimera-agent-cloud/blob/1cb14f6ea4f39683fb9864a56749576be61e25f0/discussion/checkpoints/20261117-164221-submission-smoke-first/manifest.json)。

机器证据：
- `output/p01_public_release_audit_20261117/remote_metadata.json`：公开属性、commit及远端路径／blob清单，无仓库权限字段或认证信息。
- `output/p01_public_release_audit_20261117/source_public_comparison.json`：17文件SHA256／公开blob比较、AST静态检查结论。
- `output/p01_public_release_audit_20261117/access_and_resource_metadata.json`：匿名访问限制、docker缺失、现KB包版本与资产大小元信息；未读取权重字节。

## 当前公开物分别能做什么

| 层次 | 已具备 | 明确边界 |
|---|---|---|
| 公开过程快照 | discussion过程、阶段报告、checkpoint指纹、round2源码／配置／部分测试 | 可复核研究顺序、声明、接口和静态实现；报告为公开衍生版，不能当逐例原始证据 |
| 可阅读源码 | C02、信息权限、固定检索、runner、inference与输出模块均有源码；抽核版本对应 | 公布源码不等于完整可构建上下文，也不证明C02在平台入口已启用 |
| 独立构建源码 | round2有Dockerfile、requirements和pyproject | 缺提交profile、资产获取／校验／安装流程、构建入口／忽略规则／启动链与实际镜像验收 |
| 运行资产 | 根目录保留上一轮部分guideline DB／QA、vendor及旧启动脚本 | round2无resources／model；旧根目录资产与C02固定KB的身份未证明相同，不能混入补齐 |
| 模型／资源公开元信息 | 根THIRD_PARTY有历史组件说明，C02源码内有部分来源URL和版本 | 未构成当前C02精确模型revision／量化转换／资源文件SHA／运行依赖／获取方式清单 |
| 医学复现 | 可见工程验收声明与限制 | 无患者、GT、逐例trace，无法由公开包重现91例临床结果或证明医学有效性；C02医学NO_GO保持 |

## 阻断独立构建与C02复现的具体缺口

1. **构建上下文不闭合。** round2/Dockerfile_Baseline会COPY resources/及model/，而round2没有这两目录，单独以round2为context会缺输入。根目录与round2是不同版本；在根context使用round2/Dockerfile也会COPY旧根源码，不能这样混拼。公开round2缺 .dockerignore、do_build.sh、docker/start_server.sh、tools/check_submission_imports.py；公共同步白名单只导出指定文本，不包含这些新提交工程资产（tools/public_checkpoint.py:20、26）。后续专用构建交付不能仅靠当前白名单自然覆盖。

2. **入口不加载C02。** inference.py:371从固定config.yaml读取；当前information_access.enabled=false，且不合并round2_evidence_package_candidate.yaml。该overlay本身还要求先合并shared-plan v2配置，不是独立的可提交profile。T1支持及T2/T3走哪条合法路径必须显式冻结；不能仅把C02 YAML放进镜像就声称触发。

3. **模型服务启动缺口。** config默认openai兼容localhost:8765，Docker入口直接执行inference.py，没有启动该35B服务的步骤。现Docker主要安装vLLM，pyproject可选vLLM又是另一版本；公开旧docker/start_server.sh不是现round2启动证明。应交付真正使用的后端、server二进制／版本、启动参数、模型挂载、健康检查、超时、失败退出和tokenizer身份校验，不依据历史注释认为已经具备。

4. **C02资源不可由现公开包直接重建。** 候选YAML指向服务器output绝对路径，Docker上下文排除output。build_round2_evidence_package.py:15固定依赖A01规则台账、已下载原始PDF／HTML及抽取文本、两个guideline_dataset二次摘要、固定KB QA与DB。公开round2没有这些输入；C02测试test_evidence_package.py:17、35也读取output资源。不能宣传“clone后运行已公开C02测试即可完整复现”。应先交付无患者的最小合成fixture与独立测试目标，完整工程集成测试另明确需要私有本地资产。

5. **路径迁移存在多重哈希依赖。** knowledge_access.py:39要求corpus／encoder／reranker绝对路径、逐文件身份、精确包版本，并绑定alignment报告路径及SHA；overlay再绑定整个知识manifest的SHA，配置再绑定overlay的SHA。因此需要生成新的容器路径manifest、兼容性报告和派生overlay／配置，保存旧→新对应与差异，保持原KB内容和检索规则；不能替换字符串后沿用旧SHA，也不自动重建向量或改检索。

6. **依赖不匹配是明确验收阻断。** 现知识manifest SHA256为`13fbd478a3c4f2f3fa2f2fe60a9b48c5c838df097f15eab83077a36fc41bb4da`，它要求如下版本。现requirements不同，Docker还会重装vLLM依赖。knowledge_access.load_manifest逐包相等比较，若沿用此manifest，差异将触发Knowledge dependency versions differ。这里只根据源码和manifest确定，不声称已实际构建失败。

| 包 | 固定KB manifest | 当前公开构建声明 |
|---|---|---|
| sentence-transformers | 5.7.0 | requirements=5.6.0 |
| transformers | 4.57.6 | requirements=5.12.1 |
| torch | 2.6.0+cpu | Docker基础镜像／vLLM CUDA安装；未锁最终解析值 |
| numpy | 2.4.6 | 未见requirements显式pin；未验最终解析值 |
| chromadb | 1.5.9 | requirements=1.5.9 |
| rank-bm25 | 0.2.2 | requirements=0.2.2 |

现Docker的基础镜像用tag而非digest，apt以及pip／wheel／setuptools／uv升级未锁，缺最终依赖解析／wheel哈希清单。因此即使补齐文件，也不能仅凭requirements中“fully pinned”注释宣称位级可复现。不要在本项擅自选择改依赖或解除严格检查；由构建任务选定实际运行环境，再做原向量兼容性验收并登记新版本。

7. **现许可证／来源说明不足以描述C02交付。** 公共根LICENSE为Apache-2.0，THIRD_PARTY记录历史LLM、embedding与指南，但没有当前C02四来源的精确版本／检索日期／SHA、encoder／reranker确切revision、摘要派生关系、量化来源与重建方式。C02原文／抽取全文／overlay内引文不是自动获准公开Git的文本。先公开来源与指纹元信息和获取／校验说明；是否再分发全文或模型资源按各自条款单独核查，不能用仓库Apache标识覆盖第三方。

8. **缺同一提交产物的运行证据。** 本服务器docker命令为空；本项未安装服务、构建镜像、下载或推理。旧verify_submission.sh固定历史image ID／MD5、外部挂载与逐例回归资源，不能直接充当本次不含患者材料的公开验收命令。需新的合成／工程专用命令，并在实际待提交镜像上验证非root、断外网、输入只读、输出可写、T1/T2/T3 sockets和机制正向／拒绝／失败消费路径。

## 最小公开交付清单

这是下一项工程交付清单，不是在本审查中上传。

1. **单一源码身份与根说明**：新提交目录或版本tag、源码commit／逐文件manifest、从哪个context构建的唯一命令、同round2过程快照的关系。保留旧根与冻结目录，不将二者混称同版。
2. **闭合的构建文本**：实际Dockerfile及base digest、.dockerignore、构建／导出脚本、真实服务启动／健康检查脚本、安装依赖的锁定与最终包清单、无模型导入检查。将这些路径显式纳入公开导出范围。
3. **明确提交profile**：T1/T2/T3配置与机制表、C02 opt-in／默认关闭两路径、共同权限／成本／停止契约、容器固定路径、模型／tokenizer端口和身份。未验收机制写禁用或未知，不能“全开”代替验收。
4. **仅元信息的资产manifest**：LLM仓库revision、量化文件名／来源／SHA／大小／许可证与获取说明；encoder／reranker及KB各文件SHA／大小、依赖、对齐报告身份；四份原始来源URL／版本／页定位／下载及抽取方法；C02 overlay／KB manifest／来源的依赖图和重绑定记录。外部资产不进入本轮Git包。
5. **独立无患者合成验收**：一个不依赖output、原指南全文、私有KB或真实模型的最小fixture／stub测试目标，覆盖正向、正常不触发、未知、拒绝、错误与最终消费。另提供需本地真实资产的集成／容器验收命令，准确写前提。
6. **产物及验收回执**：实际image ID／归档SHA、模型包ID／SHA、源代码／profile／资源manifest对应、构建及断网运行聚合日志、资源上限、已知限制。公开回执不含患者内容、GT、逐例trace或账号秘密；平台镜像／model上传与公开Git同步分列。
7. **来源与复现边界说明**：更新当前提交对应的第三方组件表、来源／获取与权限前提，明确工程可复现不等于临床效果复现。C02只支持T1、两个原文载体、有限格式，医学未验；保留这些边界。

## 排除项与发布控制

公开Git继续排除患者原文、隐藏病历、GT、逐例输入／预测／trace／临床日志、患者ID／质量标签、训练／few-shot病例、模型权重、凭据、服务环境文件、旧私有output整目录、含未审原始指南全文的候选资源包。这里不复制或再次扫描这些内容；不能把“未读取”写成“已全量安全审查”。

当前根目录已存在的公开DB／QA／vendor属于历史发布；本项仅检查路径与元信息，未审其完整内容或分发权，未删除或再次发布。若后续新包需要这些资产，须依据新manifest决定是否引用，不能自动认定旧根公开资产等于本次受控资源。

后续由父会话统一更新CURRENT／INDEX、运行public_checkpoint并回读；本子任务不触碰pending、不执行git add、不创建仓库、不更改visibility、不推送。不得把本报告写成“新提交已上传”或“平台已经ready”。

## 已验证／未知与下一最小动作

已验证：仓库公开属性与连接可读；准确远端commit；14个抽核公开源码／配置字节身份；round2缺失的构建上下文；C02默认不开及宿主路径依赖；KB精确依赖与公开构建声明不匹配；本机无docker。

未知／未执行：匿名浏览器／git clone实测、Mac最新recipe与资产、新镜像构建、最终依赖解析、35B服务实际启动、真实容器T1/T2/T3、C02平台触发、平台账号／额度／提交状态及医学效果。静态import检查不替代这些验收。

下一最小动作：父会话先冻结机制profile及资产身份；在独立staging交付上述构建文本、路径／哈希重绑定方案和无患者合成目标；合入补传Mac踩坑后，选择已有可用构建宿主并验算实际下载／磁盘／运行预算，再构建一次新产物。本项不提出新临床批次或新研究方向。

资源：本审查0临床LLM调用、0训练、0GPU、0模型／原始临床／GT／trace读取、无大下载；仅CPU静态解析、少量公开GitHub GET、少量本地源文件／manifest读取。知识manifest登记的corpus约48.6MB、encoder约1.264GB、reranker约1.134GB，合计约2.447GB；这是既有元信息，未读取权重，未含35B、容器层、缓存或临时解包，不能用它充当总构建下载量或峰值磁盘预算。后续最低需要Linux/amd64容器构建环境、已选离线后端／资产及一次有限容器验收的资源估算；当前没有后台任务，无需等待。
