# 每阶段提交与公开存档

执行会话每完成一个可复核步骤（实现和验收、有限批次完整分析、明确的失败诊断），必须更新 discussion/CURRENT.md 与 EXPERIMENT_INDEX.md，再保存检查点和推送。不要求用户再次说“上传”。这不是每次保存文件都上传的定时任务，也不运行后台无限循环。

## 生成本地归档

运行 `python tools/public_checkpoint.py --step <英文步骤名> --summary '<本步结果与限制>'`。

工具使用独立的本地裸仓库归档并打时间标签，不触碰执行仓库的暂存区或分支。`output/public_sync/pending.json` 指向待上传bundle；原始数据／模型／日志不在允许清单。报告生成公开衍生副本，省略逐例记录，源文件不改。

## 使用已连接 GitHub 完成推送

本服务器未配置命令行GitHub写入凭证，发布由执行会话使用GitHub连接工具完成。不要索取或把API密钥写到仓库。

可直接读取 `tools/public_checkpoint_publish.js` 的源码并作为 `functions.exec` 调用执行：该脚本会完成以下全部步骤，并核对每个公开文件的 Git blob 指纹后写回执。不是在 Node 或 shell 中直接运行。

1. 从pending读取bundle；先检查清单、排除项和报告公开版本。若代码含患者特定内容被排除，先解决范围，不假装完整代码已发布。
2. GitHub fetch读取 `lancheq/chimera-agent-cloud` 的 `branches/main`，记录head和tree。
3. create_tree以该tree为base，加入bundle.files中的UTF-8文本；不删除原有路径。新增代码位于round2/，旧根目录代码保持。
4. create_commit以刚读取的head为parent，再update_ref更新main；使用expected_sha，force=false。head变化就重读并构建新提交，不强推。
5. 再fetch核对远端head和discussion/LATEST.md。将远端commit、URL、本地archive commit和checkpoint写入 `output/public_sync/<checkpoint>/published_receipt.json`，仅成功后清除对应pending。上传失败保留pending，不报告成功。

首次发布由详解会话执行。后续由主会话在阶段结束时沿用；不依赖详解会话常驻。Chat需主动读取GitHub内容，不能保证其他对话即时自动刷新。

## 快照含义

本地标签归档的是经筛选的公开文本快照；服务器原始临床证据和模型不在Git存档内，仍按实验冻结目录保存。公开提交中的过程报告、源码和指纹相互对应，但不证明该工作区最新代码已跑完全部历史实验。每批按其冻结源码核对。
