# S1：数据来源登记与保守分组

2026-11-13。运行准备／数据治理阶段，未调用模型、训练、修改生产行为或清洗输入值。

## 交付与范围

最终目录为 `output/round2_data_pool_20261113_v2/`。首版保留在 `output/round2_data_pool_20261113/`；最终版补齐逐版本配对表与官方样例用途标记，未覆盖源输入或旧审计。

| 文件 | 作用 |
|---|---|
| [data_pool_manifest.json](../output/round2_data_pool_20261113_v2/data_pool_manifest.json) | 来源、原路径、资产角色、哈希依据、接触史、用途与可复跑状态 |
| [patient_version_map.json](../output/round2_data_pool_20261113_v2/patient_version_map.json) | 保守同源风险组、成员及跨任务关联证据，不是确诊患者身份 |
| [version_pairs.json](../output/round2_data_pool_20261113_v2/version_pairs.json) | 两release逐任务／ID配对，只比较输入，不读取标签、不合并值 |
| [archive_inputs.json](../output/round2_data_pool_20261113_v2/archive_inputs.json) | 原ZIP成员及白名单输入字节核对 |
| [summary.json](../output/round2_data_pool_20261113_v2/summary.json) | 数量、验证和限制汇总 |
| input_hashes.json、target_read_guard.json | 原输入前后校验与本次禁止读取参考内容的保护记录 |

三来源仍为1275条记录：服务器旧目录429、旧训练release423、Phase2训练release423。登记3834个输入文件资产（包括别名和样例副本）、712个决策标签文件、489个reasoning文件和8个平台返回文件。文件／记录数都不是独立患者数。

标签、reasoning、平台返回仅登记存在性、大小以及既有传输清单中的来源／声明哈希。**声明哈希不是本轮读取内容验证**，这类元数据不得作为LLM、predictor或相似病例查询的特征。清单也不授予运行时读取隐藏临床字段的权限；S2执行边界仍需落实。

两份test返回按既有传输证明记为同次返回的副本。已接收范围尚未核实可独立复跑的dev/test完整临床输入原包；不声称未挂载Mac或其他主机一定没有原包。评分返回不能变成临床输入。当前`replay_status`仅为输入包存在、运行适配未认证，并非新的容器／MCP端到端通过。

## 版本与副本核对

- 两release有423组同任务／ID配对。419组非空表征指纹相同，prompt和clinical指纹相同均为0；保留版本，不覆盖或拼成新增患者。
- 已传入的 `train_release_chimera_phase2.zip` 内，423份prompt及对应clinical／表征共1265个白名单输入成员，与Phase2解压目录全部字节相同。标签和reasoning成员未被读取。这确认ZIP与现有解压输入的关系，没有增加病例。
[公开版省略逐例记录；汇总与机制结论保留。]

## 风险分组及限制

由病例键、非空临床／表征指纹候选做传递关联，得到415个保守风险组，6组跨任务，最大组10条记录。**415不是已确认患者数**：相同指纹可能来自复制或模板，同ID可能跨版本语义不同；保守分组可能过度合并，未匹配也不能证明独立。

后续内部划分不得将同一风险组拆到训练与评估两侧。文件没有自动分配分区或认证跨组患者独立性；6个跨任务组及最大组优先复核来源，真实患者映射仍未关闭。全部来源保持历史已接触属性，无新holdout声明。

## 验证与下一步

12项相关测试通过：包括跨任务传递关联、prompt相同不认定身份、缺失表征不当作相等、ZIP目标不解码／路径越界，以及旧标签盲审边界。全量运行额外用文件读取守卫禁止打开标签／reasoning／metrics内容，尝试读取数0。3834份原始输入前后哈希一致，ZIP输入不一致0。

复现命令（输出目录须不存在）：

```bash
PYTHONPATH=src:. /home/hedongze/venvs/tabpfn/bin/python tools/build_round2_data_pool.py \
  --sources configs/round2_sources_20261113.json \
  --output-dir output/round2_data_pool_new_audit
```

读取守卫记录属于本次额外保护，不冒充所有未来运行入口均已受控。

P1登记已交付；P2交付风险关联与版本表，患者确认仍未完成。下一项P3按来源、日期、单位核对冲突候选，裁不清保留两来源；再进入S2权限／成本契约。本轮无新后台任务，无需等待。
