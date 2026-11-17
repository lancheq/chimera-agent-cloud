# 两份任务规则摘要的来源与适用性审查

2026-11-17。S4后的只读诊断阶段，不是新提分实验。范围为两份摘要全部69个条目（AUA 27、PI-RADS 42），另核对6例既有分歧中的检索事实和请求上下文；不按正确性选取规则。条目包括影像评分行、重复表述和项目用途说明，**不是69条独立临床建议**。

本轮真实模型调用0、训练组件0；未改病历、Prompt、KB、向量或冻结实验。3299个受保护文件前后指纹相同，冻结语料8文件也与原manifest一致。69行均有原文行号、出处和条件键，语法覆盖检查无漏项。人工医学解释不是独立专家裁定。

## 原始来源与版本

- [AUA/SUO Part I，2023，doi:3491](https://pmc.ncbi.nlm.nih.gov/articles/PMC11060750/)和[Part II，doi:3492](https://pmc.ncbi.nlm.nih.gov/articles/PMC11321723/)，按原始statement编号核对。原官方PDF旧链接本次不可用，已保存PMC原作者稿与下载指纹；Part I页码应为46–53，项目头写45–53。没有用其他年份更新替代2023条款。
- [ACR发布的PI-RADS2.1，2019 PDF](https://edge.sitecorecloud.io/americancoldf5f-acrorgf92a-productioncb02-3650/media/ACR/Files/RADS/PI-RADS/PIRADS-2019.pdf)，由[ACR主页](https://www.acr.org/Clinical-Resources/Clinical-Tools-and-Reference/Reporting-and-Data-Systems/PI-RADS)当前链接取得。表内页码是文内印刷页，PDF查看器页码另加7。旧PDF URL已跳转到网页，未把网页当PDF。头部所列2021综述是二次来源，本审查不用其代替原标准。
- [EAU 2025年3月完整指南](https://d56bochluxqnz.cloudfront.net/documents/full-guideline/EAU-EANM-ESTRO-ESUR-ISUP-SIOG-Guidelines-on-Prostate-Cancer-2025_2025-03-24-120144_rinw.pdf)：本地PDF与官方SHA256同为`13129874ea258067735ce2021995207825d3a6123ee4bdd4ad51ddf8701c92a1`。本次严格按2025核对所谓2025关联规则；不使用当前2026网页回填历史版本。PI-RADS摘要另声称结合NCCN但未给版本/条款，因此该部分未核实。

## 结果与含义

| 人工审查状态 | 条目数 | 含义 |
|---|---:|---|
| 窄义相符 | 36 | 对应内容可定位；不认证跨人群完整适用 |
| 条件不全 | 17 | 条件、例外、可选性或继承前提省略 |
| 内容/归属偏差 | 13 | 评分含义、动作强制性或所谓直接来源不符；重复表述也分别计行 |
| 未定位/项目说明 | 3 | 2条是项目用途说明，1条混合管理建议未定位完整原始支持；不等于3条虚假医学规则 |

36条相符不能等同整份资料可信；13条偏差也不等同13个独立缺陷，更不证明有13例因此答错。

关键偏差有三个层次。AUA中两次将“可补充系统取样”写成强制联合，改变动作强度。任务摘要把整体低风险缩成PSA阈值，把某一密度阈值当通用分界，并误称直接对应EAU 2025 §5.6；实际相关决策推荐位于§5.5.7，§5.6是活检途径。PI-RADS低层评分中DWI2/3/4、TZ-T2W3/4及DCE定义也有转述偏差，见完整台账。这些不是全部应一次修的单一机制。

PI-RADS评分标准本身不是完整管理方案；本任务已有放射科评分，低层序列转述问题并不授权agent免费重判影像。AUA筛查/初次或阴性后重复取样的适用范围，也不能直接覆盖已确诊患者的确认性/监测动作。条件说明见下方共用键与原始出处。

## 轨迹对应：事实、返回内容和判断输入

6例中有3例调用过指南，共6次搜索、48个返回条目。按执行该次搜索前的合法可见信息核对：4条query中的患者事实有依据；1条还含未定义阈值的趋势形容词；1条影像分级与已取得报告明确不符。未发现显式标为待证假设的query。本范围不能推断全91例的错误率，也未声称query错误已导致判错。

48条返回中，5次是2份任务关联摘要，5次是2个不同的EAU2026目录片段。目录项的标题虽含recommendations，正文仍只是章节导航；其“2026”是片段明确页眉，不能给整个混合KB统一推定年份。部分其他片段没有完整版本绑定，未在本轮推测填齐。冻结一致性验证只证明资源未变，不证明来源级别、版本或推荐条件正确。

3组成功临床内容相同的分歧中：系统消息及采样参数均相同，但诊断请求都不同；2组有不同query/知识内容，2组固定流程另含一次取证拒绝（分组可重叠）。其中一组没有知识检索差异，仅凭轨迹仍不能归为纯随机波动。私有请求差分保留预算、观察与拒绝文本，尚未重写上下文。

6例共同初始记录可区分3例既往阳性复评、2例既往阴性复评和1例未记录既往阳性，不能用一种“癌风险→本次活检”的规则混用。参考意图或更精确的监测分层若输入不足，保持未确定；未将GT/参考补给模型。患者内容、query原文及逐例映射均留在私有output目录。

## A01交付与资源

任务状态为DELIVERED_AWAIT_PLANNING_REVIEW（执行审查已交付，待规划会话验收），不是已通过上层验收。机器交接见私有`output/evidence_applicability_audit_20261117/handoff.json`；未新建或发送其他会话任务。可核对的首次原始PDF保存至交付时间为北京时间15:29–15:40，是取证时段而非完整会话计时。使用CPU文本提取、文件哈希和公开文献下载；临床GPU新调用0，未独立计量Codex token/费用，不以空闲GPU推断实验占用。

## 单一候选选择与验收边界

提交规划复核的建议是先做**来源与适用条件约束的证据包**，不继续调自主规划Prompt。理由先于新正确率结果：任务二次规则被组织标签包装、目录返回、条件丢失均有直接内容证据；它们影响多条既有取证路径。query事实约束与诊断上下文规范化继续后置，避免一次改多个变量。

本步交付是审查与候选契约，尚未实现或部署运行时修复。后续仅为同一机制准备新资源/证据包版本：
1. 每条结果绑定原始文件指纹、版本、页/statement、原始/二次/任务适配来源级别、正文/目录类型，以及明确人群、条件和推荐强度；缺失记unknown，不猜填。
2. 目录和无原始依据的任务捷径不得充当行动推荐；保留其审计映射。可定位原文的规则连同条件呈现；匹配条件未知时明确不可直接套用。保持原始结果及排除原因可查。
3. 条件只与已合法取得的信息比较；缺少家族史等不能按阴性填，也不能读取隐藏病历或质量标签补齐。不强行输出患者诊断。
4. 出错/来源缺失显式记录，严格模式验收失败；不得静默回退到旧摘要或默认建议。保留查询、原始命中、有效证据与最终请求的绑定。
5. 先用本次所有规则、目录、缺版本、缺条件、已确诊等合成边界验收，再冻结候选。固定35B、query、计划控制、患者预算和诊断/填表；不同时更改重排器/粒度/上下文/阈值。

这项候选是证据来源与适用性修复的组合契约，不能在未来只归功于某一字段。若需区分“移除目录”与“补全条件”的独立因果效应，须另设有限消融；本轮未同时启动它们。Direct ReAct仍是后续必要基本参照，不因本审查自动部署新三组。

可证伪预测：在预登记、与GT无关的全部边界材料上，该证据包应降低把目录/二次捷径/条件未知项作为确定行动依据的比例，同时保留可核实且条件匹配的原始依据；若仅增加文本/计算而未改善此项，停止晋级。诊断正确率能否改善仍须另作有限对照，不能由来源完整性代替。

晋级门槛：内容及信息权限验收通过；冻结唯一主要改变及不依赖GT的选择规则；明确可用数据/组与历史接触，再安排有限真实验证。对照须分列取证后LLM判断、填表、信息单位和计算。若仍只有暴露91例，只能作探索性内部证据；不反复修提示直到胜出。当前没有新医学结果、后台任务或等待时间。

## 完整行级台账

行号指两份本地原摘要。A=AUA摘要，P=PI-RADS摘要。相同问题的重复行不去重，便于验证覆盖。原始精确文本及机读ledger保存在服务器`output/evidence_applicability_audit_20261117/`；本表不复制患者或整段指南。

| ID/行号 | 审查 | 原始位置 | 条件键 | 具体差异/限定 |
|---|---|---|---|---|
| A11 | 条件不全 | AUA2023 Part I statement 4 | screening | 年龄范围有出处，但原文为可提供基线筛查，并非人人必须启动 |
| A12 | 窄义相符 | AUA2023 Part I statement 5 | screening | 高风险人群范围与起始年龄对应 |
| A13 | 窄义相符 | AUA2023 Part I statement 6 | screening | 筛查间隔有出处，仍需共同决策 |
| A14 | 条件不全 | AUA2023 Part I statement 7 | screening | 尚省略个人意愿、年龄、PSA与风险等停止条件 |
| A18 | 窄义相符 | AUA2023 Part I statement 2 | screening | 适用于筛查首项 |
| A19 | 窄义相符 | AUA2023 Part I statement 3 | screening | 新升高PSA需先复测，不代表每种复诊必须重复检查 |
| A20 | 窄义相符 | AUA2023 Part I statement 9 | screening | 不得单凭PSA速度决定后续检查 |
| A21 | 条件不全 | AUA2023 Part II statement 18 | urgent | 保留紧急情形，但未列活检本身高风险、后续仍可能需要组织学及共享决策 |
| A27 | 窄义相符 | AUA2023 Part II statements 13,14 | initial | 可用MRI及标准化报告有出处 |
| A28 | 内容/归属偏差 | AUA2023 Part II statements 15,32 | initial_repeat | 将可加系统取样写成必须联合；丢失初次/重复适用条件 |
| A29 | 窄义相符 | AUA2023 Part II statement 16 | initial | MRI阴性仍须结合整体GG2+风险 |
| A30 | 窄义相符 | AUA2023 Part I statement 11 | low_risk | 暂缓须基于完整风险及共同决策，不等于PSA单阈值 |
| A31 | 窄义相符 | AUA2023 Part II statement 26 | prior_pathology | 局灶HGPIN不立即重做不等于永不再评估 |
| A32 | 窄义相符 | AUA2023 Part II statement 27 | prior_pathology | 额外风险评估并非自动重复活检 |
| A33 | 窄义相符 | AUA2023 Part II statements 28,29 | prior_pathology | 额外检测有出处，不是两个病理概念等价 |
| A34 | 窄义相符 | AUA2023 Part II statement 30 | repeat_no_mri | 已存在重复活检适应证且此前无MRI |
| A35 | 条件不全 | AUA2023 Part II statements 19,20,22,23 | repeat | 不应仅因阴性活检停筛查，不是任何情况都不能停止 |
| A39 | 条件不全 | AUA2023 Part II statement 13 | initial | 把可提高GG2+检出简写为普遍提高检出，弱化条件性 |
| A40 | 窄义相符 | AUA2023 Part II statement 14 | mri_reporting | 标准报告有出处，不能代替行动目标判断 |
| A41 | 内容/归属偏差 | AUA2023 Part II statements 15,32 | initial_repeat | 同A28：系统取样为可选而非强制 |
| A42 | 窄义相符 | AUA2023 Part II statement 16 | initial | 同A29，风险升高是必要限定 |
| A43 | 窄义相符 | AUA2023 Part I statement 8 | screening | 可联合评估，不以DRE替代PSA筛查 |
| A47 | 未定位/项目说明 | Project task adaptation; not a primary recommendation | task | 说明项目用途，不构成原指南原文 |
| A48 | 内容/归属偏差 | EAU2025 section 5.5.7 p41; AUA2023 II statement 15 | initial | 未定位到PSA>10这一额外门槛或跨指南统一强推荐；不证明此充分条件下动作必错 |
| A49 | 条件不全 | EAU2025 section 5.4.2.2 table 5.5 p37; section 5.5.7 p41 | initial | 0.15不是PI-RADS3唯一决策边界；0.10及其他风险条件也相关 |
| A50 | 内容/归属偏差 | AUA2023 I statement 11; II statement 16; EAU2025 5.5.7 p41 | low_risk | PSA<10不能替代完整低风险/PSAD/DRE/家族史条件 |
| A51 | 内容/归属偏差 | AUA2023 II statements 21,23,30 | repeat_no_mri | 原文是无既往MRI且需重复活检时先做MRI；不支持所有新升高PSA均重复MRI |
| P9 | 条件不全 | PI-RADS2.1 Introduction p3; section III p10 | image_scope | 五级风险定位可核对，摘要未说明治疗前适用范围及监测限制 |
| P10 | 窄义相符 | PI-RADS2.1 section III p10 | image_scope | 五级类别名对应，不产生独立管理指令 |
| P11 | 窄义相符 | PI-RADS2.1 section III p10 | image_scope | 五级类别名对应，不产生独立管理指令 |
| P12 | 窄义相符 | PI-RADS2.1 section III p10 | image_scope | 五级类别名对应，不产生独立管理指令 |
| P13 | 窄义相符 | PI-RADS2.1 section III p10 | image_scope | 五级类别名对应，不产生独立管理指令 |
| P14 | 窄义相符 | PI-RADS2.1 section III p10 | image_scope | 五级类别名对应，不产生独立管理指令 |
| P16 | 条件不全 | PI-RADS2.1 section III p10-11 | image_quality | 主导/辅助概述可用，但所有序列质控及缺失序列例外未呈现 |
| P17 | 窄义相符 | PI-RADS2.1 section III p10-11 | image_quality | PZ主导DWI，DCE仅特定中间分支升级 |
| P18 | 窄义相符 | PI-RADS2.1 section III p11,13-14 | image_quality | TZ主导T2W，DWI辅助 |
| P24 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P25 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P26 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P27 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P28 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P29 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P35 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P36 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P37 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P38 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P39 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P40 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P41 | 窄义相符 | PI-RADS2.1 section III combined assessment table p11 | image_quality | 该组合行与原表一致；以可诊断质量序列为前提 |
| P47 | 窄义相符 | PI-RADS2.1 section IV.B.2 p20 | image_quality | 无异常应同时针对ADC与高b值图像 |
| P48 | 内容/归属偏差 | PI-RADS2.1 section IV.B.2 p20 | image_quality | DWI2原文强调线状/楔形，局限性加不够3不能替代形态标准 |
| P49 | 内容/归属偏差 | PI-RADS2.1 section IV.B.2 p20 | image_quality | DWI3被改为边界不清；遗漏两序列可择一及不能同时显著的限定 |
| P50 | 内容/归属偏差 | PI-RADS2.1 section IV.B.2 p20 | image_quality | DWI4核心是ADC与高b值均显著异常；边界清楚不能替代 |
| P51 | 条件不全 | PI-RADS2.1 section IV.B.2 p20 | image_quality | 大小/侵犯门槛相符，但继承P50不完整的前提 |
| P57 | 条件不全 | PI-RADS2.1 section IV.A.2 p18 | image_quality | 典型包裹结节有出处，尚遗漏正常TZ这一评分1情形 |
| P58 | 条件不全 | PI-RADS2.1 section IV.A.2 p18 | image_quality | 列出的结节可对应2，但遗漏结节间均匀轻度低信号 |
| P59 | 内容/归属偏差 | PI-RADS2.1 section IV.A.2 p18 | image_quality | 将T2W3异质信号改写为均匀轻度低信号，可能混入评分2条件 |
| P60 | 内容/归属偏差 | PI-RADS2.1 section IV.A.2 p18 | image_quality | lenticular不等于半月形，non-circumscribed不等于仅无包膜 |
| P61 | 条件不全 | PI-RADS2.1 section IV.A.2 p18 | image_quality | 大小/侵犯门槛相符，但继承P60翻译偏差 |
| P65 | 条件不全 | PI-RADS2.1 section IV.C.2 p22 | image_quality | 漏同期增强及与T2W/DWI可疑灶一致的共同条件 |
| P66 | 条件不全 | PI-RADS2.1 section IV.C.2 p22 | image_quality | 弥漫增强需结合有无相应局灶；亦漏BPH例外 |
| P72 | 条件不全 | PI-RADS2.1 section III p10; EAU2025 5.5.7 p41 | low_risk | 并非单凭低PI-RADS决定不活检；NCCN版本及条款未给出 |
| P73 | 未定位/项目说明 | PI-RADS2.1 section III p10; EAU2025 5.5.7 p41 | initial_repeat | 个体化有依据，但短期MRI/系统活检二选措辞未定位到所称EAU/NCCN版本 |
| P74 | 条件不全 | PI-RADS2.1 section III p10; EAU2025 5.5.7 p41 | initial | 总体考虑活检方向可对应，不能归为PI-RADS5独立强推荐；漏周边取样与临床情境 |
| P78 | 未定位/项目说明 | Project task adaptation; not a primary recommendation | task | 项目输入用途说明，不能替代来源或临床适用条件 |
| P79 | 内容/归属偏差 | EAU2025 section 5.5.7 p41 | initial | 同A48，未定位到PSA>10附加门槛或强推荐 |
| P80 | 条件不全 | EAU2025 section 5.4.2.2 table 5.5 p37; section 5.5.7 p41 | initial | 同A49，不能视0.15为唯一决策边界 |
| P81 | 内容/归属偏差 | EAU2025 section 5.5.7 p41 | low_risk | 同A50，以PSA<10代替低临床怀疑完整条件 |
| P82 | 内容/归属偏差 | EAU2025 sections 5.5.7 p41 and 5.6 p41-45 | task | 章节号与内容均不构成三条捷径直接对应；5.6主要讲活检途径 |

## 条件键

这是审查定位说明，不是独立临床指南；推荐强度已在机读台账逐项保存。

- `screening`：筛查人群/早期发现；共享决策；年龄、风险与寿命；不能套用为已确诊患者本次复诊动作。
- `initial`：初次取样/疑似局限病变相关；其他风险及检查条件依具体条款，不把癌风险等同本次活检效用。
- `initial_repeat`：先区分初次取样和重复取样，靶向与可选系统取样的推荐强度不同。
- `urgent`：PSA极高须排除感染/器械操作等解释；仅紧急治疗或取样高风险的特定情形，共享决策且不排除后续活检。
- `low_risk`：暂缓依整体临床风险并有随访安排；EAU2025阴性MRI需PSAD<0.20、DRE阴性且无家族史；PI-RADS3低疑虑阈值为PSAD<0.10及相应条件。
- `prior_pathology`：须有对应病理结果；额外风险评估/检测不自动等于立即重复活检；不能把HGPIN/ASAP/AIP当阳性癌。
- `repeat`：阴性取样保护作用须计入整体风险；不能仅按单次PSA或一次阴性结果决定。
- `repeat_no_mri`：已存在重复活检适应证且未做过MRI；不能误读为不论既往影像如何均再做MRI。
- `mri_reporting`：标准化影像评分及当地解读质量；评分不是完整管理指南。
- `image_scope`：可疑癌、未经治疗腺体的影像风险分级；PI-RADS2.1非完整诊断/监测进展管理指南。
- `image_quality`：可诊断质量及对应区域的序列；原文另有序列不足例外；本任务已有放射科评分，不能据文字简表免费重新判读影像。
- `task`：项目二次适配文本；不是官方指南条目，不应以组织名标签冒充原文权威。

复核材料：下载manifest、行级ledger、查询前可见性、目标范围及请求差分均保存；包含患者映射的文件不发布。既有S4两组64/91、10.33%信息节省和约2.22倍组内token结论保持。此审查没有解决原数据全部矛盾，也未证明任何资源修订能提高正确率。
