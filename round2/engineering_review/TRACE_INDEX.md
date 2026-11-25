# TRACE_INDEX — 可公开执行轨迹索引

共 182 个轨迹文件（91 病例 × 2 臂），位于 `a_line/traces/`。
所有 `model_request/return` 的 messages 与 response 已剥离，临床数值已替换为 `<PSA>` / `PI-RADS <n>` / `<age>` 占位符。

## 一、两臂事件数分布

| 臂 | 最小/中位/最大事件数 | 含 policy_decision 的例数 |
|---|---|---|
| fixed | 10 / 14 / 16 | 0/91 |
| adaptive | 11 / 21 / 31 | 91/91 |

## 二、按实验结论分组的代表病例

分组来自 `a_line/summary/accounting.json`。

### 2.1 Adaptive 独赢（3 例）

| case | fixed 事件 | adaptive 事件 | adaptive 工具序列 |
|---|---:|---|---|
| `PT-pseudo_0169468160c6` | 14 | 21 | get_psa_trend → get_lab_results → get_mri_report |
| `PT-pseudo_562159dc2cc6` | 16 | 26 | get_psa_trend → get_mri_report → get_lab_results → search_guidelines |
| `PT-pseudo_d38917f87b35` | 14 | 16 | get_psa_trend → get_mri_report |

### 2.2 Fixed 独赢（3 例）

| case | fixed 事件 | adaptive 事件 | fixed 工具序列 |
|---|---:|---|---|
| `PT-pseudo_1dc32184cab6` | 14 | 21 | get_psa_trend → get_mri_report → get_lab_results → get_previous_notes |
| `PT-pseudo_4f33b0253633` | 16 | 31 | get_psa_trend → get_mri_report → get_lab_results → get_previous_notes → search_guidelines |
| `PT-pseudo_c39e94a13920` | 16 | 31 | get_psa_trend → get_mri_report → get_previous_notes → get_lab_results → search_guidelines |

### 2.3 策略轮次最多的 adaptive 轨迹（Top 5）

| case | policy 决策数 | 事件总数 | 停止原因 |
|---|---:|---:|---|
| `PT-pseudo_4c6b2b2bbf94` | 5 | 31 | policy_stop |
| `PT-pseudo_41ca49bed13d` | 5 | 31 | policy_stop |
| `PT-pseudo_c39e94a13920` | 5 | 31 | policy_stop |
| `PT-pseudo_2e0346bce3b3` | 5 | 31 | policy_stop |
| `PT-pseudo_4f33b0253633` | 5 | 31 | policy_stop |

## 三、读取示例

```bash
# 打印某病例固定臂的事件序列
cat a_line/traces/worker0/fixed/task1/<case_id>/candidate_events.jsonl \
  | python3 -c 'import sys,json;[print(json.loads(l)["seq"], json.loads(l)["kind"], json.loads(l).get("tool","")) for l in sys.stdin if l.strip()]'
```

## 四、轨迹中不包含

- 模型实际收到的 prompt 正文（已剥离）
- 工具返回的正文（仅保留 `budget_after`）
- 模型 reasoning_content 与最终 JSON 输出
- task2 / task3 轨迹（s4 实验只覆盖 task1）

需要这些内容时，需在有读权限的机器上用 `a_line/scripts/build_ledger.py` 从原始产物重新提取。