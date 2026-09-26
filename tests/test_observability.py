"""Distributed traces, error taxonomy, time-travel log, inspect CLI."""

import io
import json
import subprocess
import sys

import pytest

from pyweb import observability as _o


def test_trace_roundtrip_headers():
    tid = _o.new_trace()
    headers = _o.inject_headers({})
    assert headers["X-Request-Id"] == tid
    assert headers["traceparent"].startswith("00-")
    joined = _o.context_from_headers(
        {"traceparent": f"00-{tid * 2}-{'a' * 16}-01"})
    assert joined == tid  # 32-hex traceparent folds to 16-char trace
    assert _o.context_from_headers({})  # fresh trace when absent


def test_spans_share_trace():
    tracer = _o.Tracer()
    tid = _o.new_trace()
    with tracer.span("rpc", op="create_user"):
        with tracer.span("db.query", table="users"):
            pass
    assert len(tracer.spans) == 2
    assert {s["trace"] for s in tracer.spans} == {tid}
    assert all(s["dur_ms"] >= 0 for s in tracer.spans)
    assert tracer.spans[1]["table"] == "users"


def test_legacy_trace_ctx_still_works():
    tracer = _o.Tracer()
    with tracer.trace("old-style"):
        pass
    assert tracer.spans[0]["end"] is not None


def test_error_taxonomy_codes():
    from pyweb import auth as _a
    assert _o.error_code(ValueError("x")) == "bad-request"
    assert _o.error_code(KeyError("x")) == "not-found"
    assert _o.error_code(PermissionError("x")) == "forbidden"
    assert _o.error_code(RuntimeError("x")) == "internal"
    assert _o.error_code(_a.AuthError("no")) == "unauthenticated"
    assert _o.error_code(_a.Forbidden("no")) == "forbidden"


def test_error_report_hides_detail_by_default():
    try:
        raise ValueError("secret-ish internals")
    except ValueError as exc:
        safe = _o.error_report(exc)
        dev = _o.error_report(exc, safe_detail=False)
    assert safe["code"] == "bad-request" and safe["detail"] is None
    assert "ValueError" in safe["message"] and safe["trace"] is not None
    assert dev["detail"] is not None and "secret-ish" in dev["detail"]


def test_event_log_record_replay():
    log = _o.EventLog(capacity=3)
    log.record("signal", name="count", old=0, new=1)
    log.record("signal", name="count", old=1, new=2)
    log.record("rpc", name="save")
    log.record("signal", name="count", old=2, new=3)  # evicts first
    ns = {}
    last = log.replay(ns)
    assert ns == {"count": 3} and last == 4  # replay skips rpc events
    assert log.replay({}, up_to=3) == 3
    assert len(log.since(0)) == 3  # capacity bound
    assert len(log.since(0, kind="rpc")) == 1
    assert json.loads(log.to_json())[0]["seq"] == 2


def test_inspect_cli_shows_placement_and_rpc(tmp_path):
    src = tmp_path / "app.pyweb"
    src.write_text(
        'from pyweb import App\napp = App()\n@app.page("/")\n'
        "def Home():\n    count = 0\n"
        "    def increment():\n        count += 1\n"
        "    <main>\n        <button onclick={increment}>\n"
        "            Count: {count}\n        </button>\n    </main>\n")
    proc = subprocess.run(
        [sys.executable, "-m", "pyweb.cli", "inspect", str(src)],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "browser" in proc.stdout and "count" in proc.stdout
