# 本机材料收集完成与服务器验收

日期：2026-11-11。经用户明确授权，向现有本机 RP 审阅会话发送收集指令，由 Mac 执行读取、独立暂存及 SSH 回传。解决了当前服务器会话无法直接访问 Mac 磁盘的问题；不再依赖未运行的旧 git_auto watcher。

## 接收与验证

接收目录：`data/round2_materials_20261111/mac_transfer/20261111_101222_bn72g8xr/`。

- `payload/`：原始材料和来源清单；不合并到服务器现有 `data/taskN`。
- `verification.json`：本机执行的两端传输校验，PASS。
- `server_independent_verification.json`：本服务器独立复核，PASS。
- `derived_text/`：收到的 DOCX 提取文本与来源 hash；原始 payload 不变。

服务器独立逐文件验证：3,431 个清单条目的文件大小/SHA256 全部一致，加上 transfer_manifest 本身共 3,432 个文件、58,133,528 字节。缺失 0、额外文件 0、重复清单路径 0、校验错误 0。来源清单记录本机绝对路径、原文件 hash 和原件未变验证。

## 已取得

1. `project/docs/OPEN_ISSUES_未解决问题清单.md`、本地完整 EXPERIMENTS、双线状态与待验证记录。
2. Test PROVENANCE、同次 Test 的两份返回副本及其关系说明、Development 历史结果快照、RP 证据审计材料。
3. 两个研究方向及书目/版本说明共 47 份材料：证据/指南检索；路径审计/序贯诊断策略。目录映射见 `payload/rp_directions.json`。包含 10-17 可信序贯诊断 Agent 原稿、11-02/03 路径可审计稿，以及 11-10 检索方向修订版；不是把检索稿新旧版当两个方向。
4. 两个独立存放的训练发布目录：`releases/local_train_release_v1/` 和 `releases/phase2_train_release_20261104/`，各有 423 个 structured prompt、419 个预计算表征文件，以及临床 JSON 和随包标签。独立存放不代表独立患者，后续必须比较输入和发布关系。
5. `data_inventory.json/csv`、`source_manifest.json`、`transfer_manifest.json`、缺失和范围限制说明。

## 尚未证实

本次检查的项目目录及 Downloads 候选范围内，未找到可独立确认的真实 Development/Test 临床输入包。平台返回包含评分/参考字段，不等于完整临床输入；官方样例/mock 位于 `samples_unverified/`，不能当额外真实队列。数据归属、跨 release 患者身份、原下载来源仍需后续审计。

本次只完成资料取得、完整性验证和文本提取，尚未完成所有正文的科学审查或数据独立性证明。未改写本机 RP、未覆盖项目代码、未合并标签，也未进行平台提交。

旧 `git_auto_pending` 收集请求已取消并移至 `data/round2_materials_20261111/cancelled_legacy_bridge_request/`，避免后续延迟重复执行。此前从本机会话记录恢复的部分材料保留在 `recovered_from_local_chat/`，后续以本次验证过的原文件为核对依据。
