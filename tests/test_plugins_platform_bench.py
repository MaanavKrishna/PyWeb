"""Plugins, platform branches, benchmarks."""


import pytest

from pyweb import bench as _bench
from pyweb import platform as _platform
from pyweb import plugins as _p


def test_plugin_rpc_and_routes_registered():
    plug = _p.Plugin("stripe", version="1.0.0")

    @plug.server
    def checkout(cart_id: str) -> str:
        return cart_id

    @plug.page("/pay")
    def Pay():
        pass

    @plug.model
    class SKU:
        pass

    assert checkout.__pyweb_plugin__ == "stripe"
    assert plug.describe()["rpc"] == ["checkout"]
    assert plug.describe()["routes"] == ["/pay"]
    assert plug.describe()["models"] == ["SKU"]


def test_plugin_config_validation_and_secret_redaction():
    plug = _p.Plugin("mail")
    plug.config_schema = {"api_key": {"required": True}}
    with pytest.raises(ValueError):
        plug.configure(sender="x@y.z")
    plug.configure(api_key="sk-live", sender="x@y.z")
    assert plug.describe()["config"]["api_key"] == "***"
    assert plug.describe()["config"]["sender"] == "x@y.z"


def test_plugin_registry_duplicates_and_build_steps():
    reg = _p.Registry()
    plug = _p.Plugin("x")
    reg.add(plug)
    with pytest.raises(ValueError):
        reg.add(_p.Plugin("x"))
    calls = []
    plug.on_build(lambda manifest, out: calls.append(out))
    reg.run_build_steps({}, "dist/")
    assert calls == ["dist/"]
    assert reg.get("x") is plug


def test_platform_flags_and_prune():
    assert _platform.web and _platform.server
    assert _platform.target() in _platform.TARGETS
    src = ("import platform\n"
           "if platform.web:\n    a = 1\n"
           "if platform.mobile:\n    b = 2\n"
           "c = 3\n")
    web_src = _platform.prune(src, "web")
    assert "a = 1" in web_src and "b = 2" not in web_src and "c = 3" in web_src
    mobile_src = _platform.prune(src, "ios")
    assert "b = 2" in mobile_src and "a = 1" not in mobile_src


def test_bench_reports_shapes():
    results = _bench.run()
    assert set(results) == {"counter", "todo", "blog"}
    for name, row in results.items():
        assert row["html_bytes"] > 0 and row["ssr_ms"] > 0
        assert row["source_lines"] >= 4 and row["compile_ms"] >= 0
    assert results["blog"]["js_bytes"] == 0          # static page ships no JS at all
    assert results["todo"]["runtime_js_gzip"] < 15360
    assert results["todo"]["js_bytes"] == results["todo"]["page_js_bytes"] + results["todo"]["runtime_js_bytes"]
    counter = results["counter"]
    assert counter["signals"] >= 1  # count detected as reactive


def test_bench_budgets_fail_the_run():
    results = {"x": {"app": "x", "ssr_ms": 5.0, "compile_ms": 1.0}}
    assert _bench.over_budget(results, max_ssr_ms=10) == []
    assert _bench.over_budget(results, max_ssr_ms=2, max_compile_ms=0.5) == [
        "x: server render 5.0 ms > 2 ms", "x: compile 1.0 ms > 0.5 ms"]
    assert _bench.main(["--json", "--max-ssr-ms", "1000"]) == 0
