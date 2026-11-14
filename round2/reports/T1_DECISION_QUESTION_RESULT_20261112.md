# 决策问题分解：真实配对生成结果

问题分解未得到净提分；停止该候选的自动扩跑，不追加提示或阈值搜索。

| 策略 | 正确数/91 | 修正 | 损坏 |
|---|---:|---:|---:|
| keep_original | 63 | 0 | 0 |
| usual_review | 63 | 0 | 0 |
| decision_question_review | 63 | 0 | 0 |

## 审核前提议

| 策略 | 正确数/91 | 修正 | 损坏 |
|---|---:|---:|---:|
| usual_review | 63 | 0 | 0 |
| decision_question_review | 63 | 0 | 0 |

## 审核与成本

{
  "policy": {
    "usual_review": {
      "reviewed": 30,
      "accepted": 20,
      "revised": 0,
      "quote_rejections": 10
    },
    "decision_question_review": {
      "reviewed": 30,
      "accepted": 21,
      "revised": 0,
      "quote_rejections": 9
    }
  },
  "blocks": {
    "usual_review": {},
    "decision_question_review": {}
  },
  "actual_calls": {
    "usual_review": {
      "review": 30,
      "request_errors": 0,
      "audit": 21
    },
    "decision_question_review": {
      "review": 30,
      "request_errors": 0,
      "audit": 21
    }
  }
}

30例各进行两组真实新诊断生成，其他61例不变；这不是固定提议的审核回放。原文、旧判断、目标语义、模型、种子、系统指令及审核相同，只有实验组额外的问题分解提示不同。
同91例已暴露历史病例；设计前查看了12个错误病例及参考解释，属于内部机制验证，不能认定独立患者或平台提分。参考解释没有进入模型输入，缺失的病灶比较/活检间隔没有被补写。
冻结哈希通过 811 项，既有批次未改写；原生产图不变。本批完成后停止。
