"""字段级数值归一化的正负对照。零生成。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chimera_agent_baseline.judgment_norm import normalize_numeric_fields  # noqa: E402


def norm(v, task=3, field="months_to_recurrence"):
    obj, rec = normalize_numeric_fields(task, {field: v})
    return obj[field], rec


@pytest.mark.parametrize("given,expect", [(12, 12.0), (60, 60.0)])
def test_int_becomes_float_with_record(given, expect):
    value, rec = norm(given)
    assert isinstance(value, float) and value == expect
    assert rec and rec[0]["rule"] == "int_to_float_lossless"
    assert rec[0]["from"] == given


@pytest.mark.parametrize("given", [12.0, 12.5])
def test_float_untouched(given):
    value, rec = norm(given)
    assert value == given and rec == []


@pytest.mark.parametrize("given", ["12", True, None, -5, float("nan"), float("inf")])
def test_illegal_values_not_normalised(given):
    """不猜测、不强转：原样留给下游契约拒绝。"""
    value, rec = norm(given)
    assert value is given or value == given
    assert rec == []


def test_other_fields_untouched():
    obj, rec = normalize_numeric_fields(3, {"event": 1, "months_to_recurrence": 12})
    assert obj["event"] == 1 and isinstance(obj["event"], int)
    assert isinstance(obj["months_to_recurrence"], float)


def test_task_scoped():
    """T1/T2 没有该字段白名单，不做任何转换。"""
    obj, rec = normalize_numeric_fields(2, {"months_to_recurrence": 12})
    assert obj["months_to_recurrence"] == 12 and rec == []


def test_input_not_mutated():
    src = {"months_to_recurrence": 12}
    normalize_numeric_fields(3, src)
    assert src["months_to_recurrence"] == 12
