# 发给本机 RP 审阅会话的材料收集指令

本次仅收集 CHIMERA-agent 第二轮改进所需材料，不修改 RP 内容、不编辑本地或服务器项目代码，也不做整仓同步。

已知本机路径：

- 项目：`/Users/lancheq/Desktop/项目汇总/Chimera-agent`
- 研究计划：`/Users/lancheq/Desktop/留学申请/RP`
- 服务器 SSH 别名：`hedongze-server`；目标项目 `/home/hedongze/CHIMERA-agent`

请直接执行以下工作，不只回复操作建议：

1. 读取项目根目录 AGENT.md/AGENTS.md，遵守适用约束。此次只读取来源并复制资料，不会整仓 rsync 覆盖服务器补丁。
2. 创建新的独立暂存目录。可通过 scp 从服务器取回 `tools/collect_round2_materials.py`，在 Mac 上运行，收集本地问题清单、EXPERIMENTS、双线状态、两份平台结果目录的文本产物以及 RP 目录内的 Markdown/DOCX。补入 `reports/rp_evidence_audit_20261105.html`（若存在）。所有原件保留不变。
3. 两个方向分别定位：证据/指南检索方向的最新 RP；路径审计/序贯策略方向（包括 `ResearchPlan_可信序贯诊断Agent_v1_20261017.md`、路径可审计临床Agent 相关版本）。不要把同一检索方向的新旧版本当两个方向。复制对应原文与版本说明，供服务器对照，不要重写研究计划。
4. 盘点真实 development/test 输入、标签、平台输出及其压缩包位置，记录相对路径、大小和来源。排除模型权重、虚拟环境、原始影像、密钥、.env 和无关申请材料。对于可明确识别的临床 JSON/embedding 输入包，以独立 release 目录暂存和回传，不合并到服务器现有 data/taskN。无法确认的包先记录 inventory，不擅自认定为独立患者或真实标签。
5. 使用现有 SSH，把资料目录传至服务器新的 `data/round2_materials_20261111/mac_transfer/` 子目录；有重名目标时创建新的带时间戳子目录，不能覆盖旧资料。传输前后核对 SHA256、文件数量和字节数。仅同步暂存目录，不同步整个项目。
6. 在本机会话回复服务器实际接收路径、材料清单、缺失项及校验结果。若 SSH 或权限阻断，报告实际错误，保留暂存目录。

预期至少收到：OPEN_ISSUES 全文、dev/test PROVENANCE、两个研究方向的正文、完整 RP 书目、真实数据包位置清单，以及可确认的数据输入副本。平台两份结果副本不得算作两次独立提交；跨 release 的 case_id 不能直接配对。
