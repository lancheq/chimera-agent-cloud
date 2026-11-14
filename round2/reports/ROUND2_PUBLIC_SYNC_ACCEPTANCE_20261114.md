# 公开Git归档与Chat阅读入口：验收

2026-11-14。用户授权使用现有公开仓库并上传过程Markdown。此项为资料发布／版本归档，不是新模型实验。

## 已实现

- 仓库：lancheq/chimera-agent-cloud，main。旧根目录发布代码保留；第二轮代码、配置、测试、计划与公开报告放round2/。
- discussion/提供CURRENT、ROADMAP、EXPERIMENT_INDEX、LATEST和更新流程。读者可按阶段与对应Git提交讨论，不把旧报告当实时结果。
- tools/public_checkpoint.py按固定文本允许清单生成公开快照，在独立本地Git裸仓库commit和时间标签归档，保留原工作区分支与暂存状态。
- tools/public_checkpoint_publish.js在functions.exec中使用GitHub连接发布；沿远端head创建提交，expected_sha检查，不强推。核对全部输出文件Git blob指纹及LATEST回读后写published_receipt，成功才清pending。
- AGENTS.md要求执行会话每完成可复核步骤更新入口并走此流程；不依赖讨论会话常驻，不要求用户每次单独确认上传。

## 实际验证

4项针对性测试通过：原始数据与output不导出、报告逐例记录省略、凭证阻断、外部symlink阻断、原仓库暂存／HEAD保持（边界检查分布在4个测试中）。

首次154文件公开提交a33ea1bf96de26b4eda01a1d693a6f8b4a0f63cf已回读CURRENT、LATEST与首轮S4结果报告。发布器集成演练提交798d5b21502c4cb876be6d8a81efabd97d83990a，155个文件指纹全部核验，LATEST内容一致，receipt可解析且pending已清；原仓库暂存仍为空。后续本验收文档与实施状态更新另形成检查点，以最新receipt／远端提交为准。

## 范围与局限

公开的是经筛选的文本快照，不是包含病历／模型的大型全仓备份。原始患者资料、GT、逐例trace、临床审计台账、权重与向量不上传；公开报告保留汇总与机制解释、逐例记录省略，服务器原报告未改。源码并未被自动改写为其他诊断算法。

这是一套执行会话在阶段结束时调用的自动发布流程，没有安装常驻监视器。GitHub连接需要在执行会话可用；不可用或远端head变动会失败并留pending，不可宣称完成。Chat需要主动读取仓库或提交链接，不保证已经打开的其他对话自动刷新。

当前公开状态记录v2完整诊断对照已获准启动，尚未有v2完整准确率结论；本发布工作不改变或重启那一批任务。
