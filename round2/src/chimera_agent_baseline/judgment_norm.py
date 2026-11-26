"""J0 输入边界的字段级数值归一化。

与「修复 JSON」「放宽 canonical 校验」都不同：只把**声明为 float 的字段**上的
无损 int 字面量改成 float 字面量，语义完全不变，并把每次转换记录下来。

不做的事：
- 不改字符串 "12"、布尔 true、null —— 这些交给下游按契约拒绝
- 不改负数、NaN、inf —— 同样交给下游拒绝
- 不改任何非白名单字段
- 不修 JSON 结构（那是解析层的事，失败即拒绝）
"""
from __future__ import annotations

import math

# 仅这些字段允许 int -> float；task 3 的 months_to_recurrence 在 schema 中声明为 float
FLOAT_FIELDS: dict[int, tuple[str, ...]] = {
    3: ("months_to_recurrence",),
}


def normalize_numeric_fields(task: int, obj):
    """返回 (规范化后的对象, 转换记录)。不修改入参。"""
    if not isinstance(obj, dict):
        return obj, []
    out = dict(obj)
    records: list[dict] = []
    for name in FLOAT_FIELDS.get(task, ()):
        if name not in out:
            continue
        value = out[name]
        if isinstance(value, bool) or isinstance(value, str) or value is None:
            continue                      # 交给下游拒绝，不做任何猜测
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                continue                  # 非法数值，交给下游拒绝
            continue                      # 已是 float，原样保留
        if isinstance(value, int):
            if value < 0:
                continue                  # 负数不归一化，交给下游拒绝
            out[name] = float(value)
            records.append({"field": name, "from": value, "to": float(value),
                            "rule": "int_to_float_lossless"})
    return out, records
