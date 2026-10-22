"""pyweb._speedups: the same results with and without the optional ``speed`` extra (orjson)."""

import dataclasses
import datetime
import json

import pytest

from pyweb import _speedups


@pytest.fixture(params=["orjson", "stdlib"])
def impl(request, monkeypatch):
    if request.param == "orjson":
        if _speedups._orjson is None:
            pytest.skip("orjson isn't installed")
    else:
        monkeypatch.setattr(_speedups, "_orjson", None)
    return request.param


@dataclasses.dataclass
class Point:
    x: int


VALUES = [
    {"b": 1, "a": [True, None, 1.5, "é <x>"], 3: "int key"},
    [{"id": i, "when": datetime.date(2026, 1, i + 1)} for i in range(3)],
    {"big": 2 ** 70},                            # orjson can't: the standard library takes over
    {"p": Point(1), "t": datetime.datetime(2026, 1, 2, 3, 4, 5)},
]


@pytest.mark.parametrize("value", VALUES)
def test_same_json_text_both_ways(impl, value):
    expect = json.dumps(value, separators=(",", ":"), sort_keys=False, default=str, ensure_ascii=False)
    assert json.loads(_speedups.json_dumps(value, default=str)) == json.loads(expect)
    assert _speedups.json_dumps(value, default=str) == expect
    assert _speedups.json_bytes(value, default=str) == expect.encode()


def test_sorted_keys(impl):
    assert _speedups.json_dumps({"b": 1, "a": {"d": 1, "c": 2}}, sort_keys=True) == '{"a":{"c":2,"d":1},"b":1}'


def test_unencodable_values_raise_type_error(impl):
    with pytest.raises(TypeError):
        _speedups.json_dumps({"x": object()})


def test_live_versions_agree_with_or_without_orjson(monkeypatch):
    from pyweb import livedata
    rows = [{"id": i, "title": f"row {i}", "at": datetime.date(2026, 1, 1)} for i in range(50)]
    first = livedata._version(rows)
    monkeypatch.setattr(_speedups, "_orjson", None)
    assert livedata._version(rows) == first
