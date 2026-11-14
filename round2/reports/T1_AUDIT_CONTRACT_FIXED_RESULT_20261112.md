# 固定提议的审核机制分析

这里检验审核契约；提议、临床原文、旧判断和预测器输出均固定。真实调用的是审核器，不是重新生成诊断。

| 提议来源 | 旧契约接纳/30 | 新契约接纳/30 | 旧版自身重跑接纳翻转 |
|---|---:|---:|---:|
| context_only_review | 14 | 22 | 8 |
| observed_evidence_review | 12 | 25 | 5 |

配对接纳矩阵：`{"context_only_review": {"True->True": 13, "False->True": 9, "False->False": 7, "True->False": 1}, "observed_evidence_review": {"False->True": 13, "False->False": 5, "True->True": 12}}`。

旧版自身重跑变化说明，不能仅拿历史拒绝和新版通过对比就归因于修复。新版接纳增加也不自动代表误拒减少；应核对具体声明类型和阻断原因。

## 固定标签组合

{
  "legacy_v1": {
    "context_only_review": {
      "correct": 62,
      "n": 91,
      "fixes": 0,
      "harms": 1
    },
    "observed_evidence_review": {
      "correct": 63,
      "n": 91,
      "fixes": 0,
      "harms": 0
    }
  },
  "typed_v2": {
    "context_only_review": {
      "correct": 63,
      "n": 91,
      "fixes": 0,
      "harms": 0
    },
    "observed_evidence_review": {
      "correct": 63,
      "n": 91,
      "fixes": 0,
      "harms": 0
    }
  }
}

这部分是旧提议经过不同审核的组合统计，不是新的诊断实验。原始证据组没有提出标签改变，因此审核修复不能单独创造该组诊断收益。

## 已发现问题的对应病例

[公开版省略逐例记录；汇总与机制结论保留。]

## 运行与边界

完成 120 个策略分支；真实审核调用 {"legacy_v1": 53, "typed_v2": 53}；请求错误 {"legacy_v1": 0, "typed_v2": 0}；冻结校验 644 项。

合成来源样例提供已知声明关系的工程检查；真实病例的审核真值仍需独立逐项裁定。同家族模型可能共同漏掉问题，引用检查也只保证可定位，不保证推论正确。

本分析不改提示、阈值或接纳条件，不触发后续实验，候选不接入生产。
