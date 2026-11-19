# A-F06-RECHECK · 预算停止接线后的真实单例

**预算停止接线在真实 35B 上按设计生效：第 5 个取证动作在派发前被封存，诊断与填表随后各发一次，两路正式 Socket 落地。** 本轮唯一一次真实单例已耗用，工程链路贯通；填表把 `months_to_recurrence` 写成 60 的语义问题留待规划裁定。全程使用 F06 冻结合成夹具，无患者、无参考结局。 [上一轮阻断复核](ROUND2_A_F06_BUDGET_RECHECK_20261119.md) · [预算停止验收](ROUND2_A_BUDGET_CLOSE_20261119.md) · [原 F06 失败](ROUND2_A_F06_REAL_RECHECK_20261119.md)

## 宿主准入：阻断在执行环境，不在服务器

前两轮把 `[Errno 1] Operation not permitted` 和 `nvidia-smi` exit 9 记成宿主不合格。同一份探针换到桌面端直连 宿主 后，结论反转：

| 检查项 | 派发沙箱 | 真实宿主（本轮） |
|---|---|---|
| 回环 socket | create 阶段 EPERM，errno 1 | `local_ephemeral_bind_ok` |
| `nvidia-smi` | exit 9，无法通信驱动 | exit 0，两块 3090 |
| `/dev/nvidia*` | 仅 `nvidia-caps` | nvidia0/1/ctl/uvm 全在 |
| `ss -ltnp` | netlink `Operation not permitted` | 正常，8765/8766 均空 |
| CPU 亲和 | 40 核可见 | 40 核，取前 8 |

派发默认后端把执行会话关在无网络命名空间、无设备节点的沙箱里，宿主权限本身完好。本轮改为桌面端会话经 SSH 直接执行，未改动主机安全配置、设备权限、服务范围或协议。0 号卡当时 13 MiB 占用、24246 MiB 空闲；1 号卡由 既有 27B 服务占 22336 MiB，全程未触碰。

## 指纹链与资产

运行源码 72 项逐项对上，链路 `A-G2 F06 批准 → A-T3-INPUT 修复 → A-BUDGET-CLOSE` 三跳完整。本轮真正改变的只有两处：`shared_plan.py`（9c7c359d→1c85e28a）与 `information_access.py`（93d1ced1→c8af6389），即停止接线的落点；`tests/test_adaptive_budget_close.py` 为新增测试。`llama-server` 与 `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` 的 SHA-256 与登记值一致。

接续卡在 17:46 被上一轮追加「本次接续记录」段，指纹从 `13c24520…` 变为 `bd14c957…`。内容只多这一节，未改授权范围；两个指纹都写入 `handoff.gate`，便于规划核对。

## 七次真实发送与停止位置

| # | 阶段 | 后端输入 | 输出上限 | 预留 | 后端输出 |
|---|---|---:|---:|---:|---:|
| 1 | planner | 1,733 | 1,024 | 2,757 | 730 |
| 2 | policy | 1,815 | 1,024 | 2,839 | 762 |
| 3 | policy | 1,857 | 1,024 | 2,881 | 686 |
| 4 | policy | 1,871 | 1,024 | 2,895 | 676 |
| 5 | policy | 1,896 | 1,024 | 2,920 | 581 |
| 6 | diagnosis | 3,086 | 4,096 | 7,182 | 2,399 |
| 7 | form_fill | 1,475 | 4,096 | 5,571 | 86 |

合计输入 13,733、输出 5,920、预留 27,045 token，上限 195,072。单次 `输入 + 输出上限` 最大 7,182，低于 min(16,256, 16,384−128)。7 次 SDK 载荷、HTTP 实发与后端用量逐次同 digest，隐式重试 0。8 次非生成预检正好用满配额（`/props` 1 次、原生计数 5 次、模板与分词各 1 次）。

四个工具动作全部受理：

| 动作 | 返回 | 信息单位 | 账本受理 |
|---|---|---:|---:|
| `get_previous_notes` | `previous_notes: []` | 1 | 1 |
| `get_surgical_pathology_report` | 器官局限，无复发标签 | 1 | 2 |
| `get_pathology_report` | 无该栏数据 | 0 | 3 |
| `get_mri_report` | PI-RADS 2，PSA density 0.08 | 1 | 4 |
| `get_family_history` | **未发送** | — | — |

第 4 次策略后余额 3/3、已受理 4 次，模型仍保留家族史动作。控制器计算该动作需要 1 单位、余额为 0，在派发前写下 `information_budget_before_dispatch`，账本无该动作的请求、受理或结果；`get_family_history` 在 SQLite 事件表里零命中。策略轮次 4、工具申请 4，均在 5 与 6 的配额内。三次 `policy_decision` 标记 `plan_replaced=true`（仅改写目的说明），第四次为 false。

## 两路输出与事实边界

决策 Socket 48 字节：

```json
{"event": 0, "months_to_recurrence": 60.0}
```

理由 Socket 162 字节：

> Low-risk features: PI-RADS 2, organ-confined pathology, and low PSA density (0.08). No post-treatment PSA rise documented. Predict no recurrence within 5 years.

事实来源逐项可追：PI-RADS 2 与 PSA density 0.08 来自 MRI 回执原文，器官局限来自手术病理回执，低基线 PSA 来自准入后的 `psa=0.03`。`reveal_sequence` 只记录三次真实揭示（既往记录、手术病理、MRI），活检病理与家族史没有出现在观察序列里。

诊断阶段对缺失数据的处理是干净的：原文明写 `Post-treatment PSA & Follow-up Duration: Not provided in the record`、`Months to recurrence: Not available (last follow-up duration not documented)`、`Confidence: Uncertain`。没有编造随访时长或复发时间。

需要规划裁定的有两处，都属于填表阶段：

1. **`months_to_recurrence = 60.0` 没有观测依据。** 诊断阶段判为「不可得」，填表阶段该字段必填，模型用「5 年内无复发」的预测窗口填了 60。它表示预测时间窗，不表示随访到 60 个月。平台消费方若按「末次随访时长」读取，会把预测当成已观察病程。
2. **`No post-treatment PSA rise documented` 属阴性陈述，措辞强度偏高。** 句法指向「没有记录到」，与 `previous_notes` 为空列表一致，未出现具体随访数值，也未声称观察到 PSA 变化。判定为边界表述，不构成「无依据宣称已观测随访」，但比诊断阶段的 `Not provided` 更肯定。

`repeat_test: Serial PSA monitoring every 6 months` 是随访建议，不是已发生的检查。

## 资源、时间与收尾

北京时间 17:58:19 开始、18:01:22 结束，总墙时 3 分 3 秒；病例本身 108.3 秒（上轮 96.3 秒）。一次启动、单 slot、16384 上下文、8 计算线程、8 CPU 亲和。GPU 0 峰值采样 21,696 MiB，服务峰值 RSS 21,835,380 KiB，退出前 3,958,192 KiB。只终止本卡自启的 进程，退出码 0；8765/8766 事后为空，GPU 0 回到 13 MiB，GPU 1 的 27b 进程未受影响。无后台任务。

与上一轮的差别集中在停止之后：上轮第 5 个动作实际发出、由 Guard 返回 `information_budget_exhausted`，诊断、填表与双 Socket 全部未到达；本轮同一点在发送前封存，随后共同诊断与填表各一次，两路输出完整。合成重放预期的路径在真实模型上复现。

## 交付与待办


规划侧需要裁定：`months_to_recurrence` 的语义标注方式（是否在 Socket 投影里加预测标记）、`free_text` 措辞是否可接受，以及是否据此放行 G2-CASE-PILOT 首批。本轮未改生产源码、未提交平台作业、未上传任何内容。
