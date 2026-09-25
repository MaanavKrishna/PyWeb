"""Forms, security checks, observability, npm d.ts generation."""

import io

from pyweb import forms, observability, security
from pyweb.forms import fields_for, render_form, validate
from pyweb.models import Email, Model
from pyweb.npm import from_dts, package
from pyweb.observability import Logger, Metrics, Tracer, format_error


class User(Model):
    name: str
    email: Email
    age: int


def test_fields_and_render():
    fields = fields_for(User)
    assert [f["name"] for f in fields] == ["name", "email", "age"]
    assert [f["kind"] for f in fields] == ["text", "email", "number"]
    html = render_form(User, action="/u")
    assert 'type="email"' in html and 'action="/u"' in html


def test_validate_ok_and_errors():
    clean, errors = validate(User, {"name": "a", "email": "a@b.c", "age": "3"})
    assert clean["age"] == 3 and errors == {}
    clean, errors = validate(User, {"name": "a", "email": "bad", "age": "x"})
    assert "email" in errors and "age" in errors
    _, errors = validate(User, {})
    assert set(errors) == {"name", "email", "age"}


def test_security_flags_sql_and_cmd():
    src = "q = f\"SELECT * FROM t WHERE x={v}\"\nos.system('ls')\n"
    kinds = [f["kind"] for f in security.check_source(src)]
    assert "sql-injection" in kinds and "command-injection" in kinds


def test_secret_leak_detection():
    src = "from pyweb import browser\n@browser\ndef h():\n    return API_KEY\n"
    kinds = [f["kind"] for f in security.check_source(src)]
    assert "secret-leak" in kinds
    try:
        security.assert_browser_safe(src)
        raise AssertionError("should raise")
    except ValueError:
        pass


def test_clean_source_passes():
    src = "from pyweb import browser\n@browser\ndef h():\n    return count + 1\n"
    assert security.assert_browser_safe(src)


def test_escape():
    assert security.escape("<a>&") == "&lt;a&gt;&amp;"


def test_logger_json_lines():
    buf = io.StringIO()
    log = Logger(stream=buf)
    log.info("hi", route="/")
    import json
    assert json.loads(buf.getvalue())["msg"] == "hi"


def test_tracer_and_metrics():
    t = Tracer()
    with t.trace("rpc", trace_id="abc"):
        pass
    assert t.spans[0]["trace"] == "abc" and "dur_ms" in t.spans[0]
    m = Metrics()
    m.inc("hits", 2)
    m.observe("lat", 1.0)
    m.observe("lat", 3.0)
    s = m.summary()
    assert s["counters"]["hits"] == 2 and s["lat"]["avg"] == 2.0


def test_format_error_points_at_line():
    try:
        raise ValueError("bad email")
    except ValueError as exc:
        out = format_error(exc, source_lines=["a", "raise ValueError('x')"], filename="u.py")
        assert "ValueError" in out


def test_npm_package_and_dts():
    assert package("chart.js")["npm"] == "chart.js"
    py = from_dts("interface ChartProps {\n data: number[]\n animated?: boolean\n name: string\n meta: Record<string, number>\n}")
    assert "data: list[float]" in py and "animated: bool = None" in py
    assert "Record" not in py
    assert "dataclass" in py
