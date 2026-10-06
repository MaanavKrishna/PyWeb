"""Telemetry: logs (with redaction), Prometheus metrics (and adding them up across
processes), traces carried from a request into the jobs it queues, error hooks,
N+1 detection, OpenTelemetry spans and the dev toolbar (never in production)."""

import io
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request

import pytest

from pyweb import auth as pyauth
from pyweb import jobs, telemetry
from pyweb import models as M
from pyweb.hosting import Site
from pyweb.jobs import core
from pyweb.telemetry import instruments as I
from pyweb.telemetry import logs, metrics, tracing

APP = '''
from pyweb import App, server
from pyweb.models import Model, Field
from pyweb.telemetry import span

app = App(database="sqlite:///{db}", title="Shop")

class Item(Model):
    name: str = Field(max=40)

seen = []

@app.on_error
def remember(error, info):
    seen.append((type(error).__name__, info))

@app.job(retries=0)
def index(item_id: int):
    Item.get(item_id)
    if item_id < 0:
        raise RuntimeError("index exploded")
    return item_id

@server
def add(name: str) -> int:
    with span("make item", name=name):
        item = Item.create(name=name)
    index.enqueue(item.id)
    return item.id

@server
def loop() -> int:
    total = 0
    for i in range(12):
        total += Item.where(Item.id == i).count()      # the same query, again and again: N+1
    return total

@server
def boom() -> int:
    raise KeyError("password=hunter2")

@server
def bad_job() -> int:
    index.enqueue(-1)
    return 0

@app.page("/")
def Home():
    <p>shop</p>

@app.page("/broken")
def Broken():
    {{1 / 0}}
'''


@pytest.fixture
def shop(tmp_path, monkeypatch):
    jobs.stop_all(0)
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.delenv("PYWEB_ENV", raising=False)
    monkeypatch.delenv("PYWEB_JOBS", raising=False)
    monkeypatch.delenv("PYWEB_METRICS_TOKEN", raising=False)
    jobs.use_backend(None)
    before = dict(core.REGISTRY)
    hooks = list(telemetry.ERROR_HOOKS)
    telemetry.RECENT.clear()
    (tmp_path / "app.pyweb").write_text(APP.format(db=tmp_path / "shop.db"))

    def make(debug=True):
        return Site(str(tmp_path / "app.pyweb"), debug=debug, rate_limit=False)
    yield make
    jobs.use_backend(None)
    core.REGISTRY.clear()
    core.REGISTRY.update(before)
    telemetry.ERROR_HOOKS[:] = hooks
    M._state.db = None


def rpc(site, fn, headers=None, **args):
    status, hdrs, body = site.respond("POST", f"/__pyweb/rpc/{fn}",
                                      {"Content-Type": "application/json", "Host": "localhost", **(headers or {})},
                                      json.dumps({"args": args}).encode())
    return status, dict(hdrs), json.loads(body)


def app_module(site):
    return site.app.module


# ------------------------------------------------------------------ logs

def test_redaction_of_fields_text_and_urls(monkeypatch):
    assert logs.redact({"user": "ann", "password": "x", "nested": {"api_key": "k", "ok": 1}}) == \
        {"user": "ann", "password": "[redacted]", "nested": {"api_key": "[redacted]", "ok": 1}}
    assert logs.redact_text("GET /reset?token=abc123&next=/home") == "GET /reset?token=[redacted]&next=/home"
    assert logs.redact_text('{"password": "hunter2", "name": "ann"}') == '{"password": "[redacted]", "name": "ann"}'
    assert logs.redact_text("Authorization: Bearer xyz") .startswith("Authorization: [redacted]")
    monkeypatch.setenv("PYWEB_LOG_REDACT", "iban")
    assert logs.redact({"iban": "DE00"}) == {"iban": "[redacted]"}


def test_json_lines_carry_the_request_context():
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(logs.JSONFormatter())
    lg = logging.getLogger("test.telemetry.json")
    lg.addHandler(handler)
    lg.setLevel(logging.INFO)
    try:
        req, token = tracing.begin("http", route="rpc:pay", method="POST", path="/x")
        req.user = "7"
        lg.info("charged card token=tok_123", extra={"amount": 5, "card_secret": "s"})
        tracing.end(token, req, status=200)
    finally:
        lg.removeHandler(handler)
    rec = json.loads(out.getvalue())
    assert rec["msg"] == "charged card token=[redacted]"
    assert rec["amount"] == 5 and rec["card_secret"] == "[redacted]"
    assert rec["route"] == "rpc:pay" and rec["user"] == "7"
    assert rec["request_id"] == rec["trace_id"] == req.trace_id and len(rec["trace_id"]) == 32


def test_text_format_is_readable():
    rec = logging.LogRecord("pyweb.access", logging.INFO, "", 0, "GET / 200 3.0ms", (), None)
    rec.method, rec.status = "GET", 200
    line = logs.TextFormatter(color=False).format(rec)
    assert line == "info    pyweb.access: GET / 200 3.0ms"


# --------------------------------------------------------------- tracing

def test_traceparent_parsing():
    ok = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert tracing.parse_traceparent(ok) == ("4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7", True)
    for bad in ("", "garbage", "00-" + "0" * 32 + "-00f067aa0ba902b7-01", "00-4bf92f35-00f067aa0ba902b7-01",
                "00-4bf92f3577b34da6a3ce929d0e0e4736-" + "0" * 16 + "-01"):
        assert tracing.parse_traceparent(bad) is None


def test_request_ids_from_outside_need_a_trusted_proxy():
    req, token = tracing.begin("http", headers={"X-Request-Id": "lb-abcdef123"})
    tracing.end(token, req)
    assert req.request_id == req.trace_id                     # not believed
    req, token = tracing.begin("http", headers={"X-Request-Id": "lb-abcdef123"}, trust_proxy=True)
    tracing.end(token, req)
    assert req.request_id == "lb-abcdef123"
    req, token = tracing.begin("http", headers={"X-Request-Id": "<script>"}, trust_proxy=True)
    tracing.end(token, req)
    assert req.request_id == req.trace_id                     # not a safe id: ignored


def test_incoming_traceparent_is_continued(shop):
    site = shop()
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    status, hdrs, _ = rpc(site, "add", {"traceparent": tp}, name="pen")
    assert status == 200
    assert hdrs["traceparent"].startswith("00-4bf92f3577b34da6a3ce929d0e0e4736-")
    assert hdrs["X-Request-Id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    status, hdrs, _ = site.respond("GET", "/", {"Host": "localhost"})
    assert dict(hdrs)["X-Request-Id"] != "4bf92f3577b34da6a3ce929d0e0e4736"   # a new request, a new trace


def test_a_job_runs_in_the_trace_of_the_request_that_queued_it(shop):
    site = shop()
    tp = "00-11112222333344445555666677778888-00f067aa0ba902b7-01"
    rpc(site, "add", {"traceparent": tp}, name="pen")
    queued = jobs.backend().list(name="index")
    assert queued[0]["trace"].startswith("00-11112222333344445555666677778888-")
    assert queued[0]["args"] == {"args": [1], "kwargs": {}}        # the trace isn't an argument
    seen = []
    orig = core.REGISTRY["index"].fn

    def spy(item_id):
        t = telemetry.current()
        seen.append((t.kind, t.trace_id, t.route, t.parent_span))
        return orig(item_id)
    core.REGISTRY["index"].fn = spy
    try:
        before = I.jobs_done.value(job="index", outcome="done")
        assert jobs.Worker(schedule=False).drain() == 1
    finally:
        core.REGISTRY["index"].fn = orig
    kind, trace_id, route, parent = seen[0]
    assert (kind, trace_id, route) == ("job", "11112222333344445555666677778888", "job:index")
    assert parent and parent != "00f067aa0ba902b7"           # the request's own span is the parent
    assert I.jobs_done.value(job="index", outcome="done") == before + 1


def test_query_counts_and_n_plus_one_warning(shop, caplog):
    site = shop()
    before = I.db_repeats.value()
    with caplog.at_level(logging.INFO, logger="pyweb.access"):
        status, _, _ = rpc(site, "loop")
    assert status == 200
    assert I.db_repeats.value() == before + 1
    line = next(r for r in caplog.records if r.name == "pyweb.access" and "/__pyweb/rpc/loop" in r.getMessage())
    assert line.levelno == logging.WARNING and line.queries >= 12
    assert "N+1" in line.warnings[0]


# --------------------------------------------------------------- metrics

def test_metrics_render_in_prometheus_format():
    reg = metrics.Registry()
    c = reg.counter("t_requests_total", "Requests", ("route",))
    c.inc(route='a"b\\c')
    c.inc(2, route="x")
    h = reg.histogram("t_seconds", "Time", buckets=(0.1, 1))
    for v in (0.05, 0.5, 3):
        h.observe(v)
    reg.gauge("t_open", "Open", callback=lambda: [({}, 4)])
    text = metrics.render(metrics.merge([reg.snapshot()]))
    assert "# TYPE t_requests_total counter" in text
    assert 't_requests_total{route="a\\"b\\\\c"} 1' in text
    assert 't_requests_total{route="x"} 2' in text
    assert 't_seconds_bucket{le="0.1"} 1' in text and 't_seconds_bucket{le="1"} 2' in text
    assert 't_seconds_bucket{le="+Inf"} 3' in text and "t_seconds_count 3" in text and "t_seconds_sum 3.55" in text
    assert "t_open 4" in text
    with pytest.raises(ValueError):
        c.inc(wrong="label")


def test_label_values_cannot_grow_without_bound(monkeypatch):
    monkeypatch.setattr(metrics, "MAX_SERIES", 3)
    c = metrics.Registry().counter("t_total", "x", ("path",))
    for i in range(10):
        c.inc(path=f"/p/{i}")
    assert len(c.series) == 4 and c.value(path="other") == 7


def test_processes_add_up(tmp_path, monkeypatch):
    """Counters and histograms sum; gauges sum, take the max or the min as declared;
    a process that died keeps its totals but not its gauges."""
    def snap(n, sockets, jobs_waiting, started):
        reg = metrics.Registry()
        reg.counter("t_total", "x").inc(n)
        reg.histogram("t_s", "x", buckets=(1,)).observe(0.5)
        reg.gauge("t_sockets", "x").set(sockets)
        reg.gauge("t_jobs", "x", merge="max").set(jobs_waiting)
        reg.gauge("t_start", "x", merge="min").set(started)
        return reg.snapshot()
    merged = metrics.merge([snap(2, 5, 7, 100), snap(3, 1, 7, 50)])
    text = metrics.render(merged)
    assert "t_total 5" in text and "t_s_count 2" in text and "t_sockets 6" in text
    assert "t_jobs 7" in text and "t_start 50" in text

    folder = tmp_path / "m"
    monkeypatch.setenv("PYWEB_METRICS_DIR", str(folder))
    folder.mkdir()
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    pid = int(dead.stdout)
    (folder / f"{pid}.json").write_text(json.dumps({"pid": pid, "metrics": snap(10, 9, 0, 1)}))
    merged = metrics.collect_all()
    assert merged["t_total"]["series"][()] == 10 and "t_sockets" not in merged


def test_metrics_endpoint_needs_a_token_in_production(shop, monkeypatch):
    site = shop(debug=False)
    site.respond("GET", "/", {"Host": "localhost"})
    assert site.respond("GET", "/metrics", {"Host": "localhost"})[0] == 404      # nothing here without a token
    monkeypatch.setenv("PYWEB_METRICS_TOKEN", "s3cret-token")
    assert site.respond("GET", "/metrics", {"Authorization": "Bearer nope"})[0] == 404
    status, hdrs, body = site.respond("GET", "/metrics", {"Authorization": "Bearer s3cret-token"})
    assert status == 200 and dict(hdrs)["Content-Type"].startswith("text/plain; version=0.0.4")
    text = body.decode()
    assert 'pyweb_http_requests_total{method="GET",route="page:Home",status="200"}' in text
    assert "pyweb_http_request_duration_seconds_bucket" in text and "pyweb_build_info{version=" in text
    assert "pyweb_db_queries_total" in text
    assert site.respond("GET", "/__pyweb/metrics", {"Authorization": "Bearer s3cret-token"})[0] == 200


def test_metrics_open_while_developing_and_routes_are_labelled(shop):
    site = shop()
    rpc(site, "add", name="cup")
    site.respond("GET", "/nope", {"Host": "localhost"})
    site.respond("GET", "/healthz", {})
    status, _, body = site.respond("GET", "/metrics", {})
    text = body.decode()
    assert status == 200
    for route in ("rpc:add", "not_found", "health"):
        assert f'route="{route}"' in text
    assert I.http_requests.value(method="POST", route="rpc:add", status="200") >= 1


def test_an_app_page_at_metrics_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    (tmp_path / "app.pyweb").write_text('from pyweb import App\napp = App()\n\n@app.page("/metrics")\n'
                                        'def Stats():\n    <p>business metrics</p>\n')
    site = Site(str(tmp_path / "app.pyweb"), debug=True)
    assert b"business metrics" in site.respond("GET", "/metrics", {})[2]
    assert site.respond("GET", "/__pyweb/metrics", {})[0] == 200


def test_worker_metrics_port():
    server = telemetry.serve_metrics(0, host="127.0.0.1")
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as r:
            assert r.status == 200 and b"pyweb_build_info" in r.read()
    finally:
        server.shutdown()


def test_job_store_counts(shop):
    site = shop()
    rpc(site, "add", name="a")
    rpc(site, "add", name="b")
    assert jobs.backend().counts() == {"queued": 2}
    I._jobs_cache[0] = 0
    assert ({"state": "queued"}, 2) in I._jobs_waiting()


# ---------------------------------------------------------------- errors

def test_on_error_hooks_get_the_request_context(shop):
    site = shop()
    module = app_module(site)
    status, hdrs, body = rpc(site, "boom")
    assert status == 500 and body["error"]["code"] == "internal"
    name, info = module.seen[-1]
    assert name == "KeyError" and info["where"] == "rpc" and info["rpc"] == "boom"
    assert info["route"] == "rpc:boom" and info["request_id"] == hdrs["X-Request-Id"]
    site.respond("GET", "/broken", {"Host": "localhost"})
    assert module.seen[-1][0] == "ZeroDivisionError" and module.seen[-1][1]["where"] == "page"


def test_a_job_out_of_attempts_is_reported(shop):
    site = shop()
    module = app_module(site)
    rpc(site, "bad_job")
    jobs.Worker(schedule=False).drain()
    name, info = module.seen[-1]
    assert name == "RuntimeError" and info["where"] == "job" and info["job"] == "index"
    assert I.jobs_done.value(job="index", outcome="dead") >= 1


def test_a_broken_hook_never_breaks_the_request(shop, caplog):
    site = shop()

    @telemetry.on_error
    def broken(error, info):
        raise RuntimeError("reporter down")
    status, _, _ = rpc(site, "boom")
    assert status == 500
    assert any("on_error hook" in r.getMessage() for r in caplog.records)


def test_reloading_the_app_replaces_its_hooks():
    def make():
        def hook(e, i):
            pass
        return hook
    before = list(telemetry.ERROR_HOOKS)
    try:
        telemetry.on_error(make())
        telemetry.on_error(make())                  # same module and name: the newer one replaces it
        assert len(telemetry.ERROR_HOOKS) == len(before) + 1
    finally:
        telemetry.ERROR_HOOKS[:] = before


def test_error_logs_redact_the_message(shop, caplog):
    site = shop()
    rpc(site, "boom")
    rec = next(r for r in caplog.records if r.name == "pyweb.errors")
    out = logs.JSONFormatter().format(rec)
    assert "hunter2" not in out and "[redacted]" in out


# -------------------------------------------------------------- OpenTelemetry

def test_opentelemetry_span_tree(shop):
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("pyweb-test")
    tracing._tracer, tracing._checked = tracer, True
    try:
        site = shop()
        tp = "00-aaaabbbbccccddddeeeeffff00001111-00f067aa0ba902b7-01"
        status, hdrs, _ = rpc(site, "add", {"traceparent": tp}, name="lamp")
        assert status == 200
        jobs.Worker(schedule=False).drain()
    finally:
        tracing.reset_otel()
    spans = exporter.get_finished_spans()
    by_name = {}
    for s in spans:
        by_name.setdefault(s.name, []).append(s)
    root = by_name["POST rpc:add"][0]
    assert format(root.context.trace_id, "032x") == "aaaabbbbccccddddeeeeffff00001111"
    assert root.kind == trace.SpanKind.SERVER and root.attributes["http.route"] == "rpc:add"
    assert root.attributes["http.response.status_code"] == 200
    make = by_name["make item"][0]
    assert make.parent.span_id == root.context.span_id
    inserts = [s for s in spans if s.name == "db INSERT"]
    assert inserts and all(s.context.trace_id == root.context.trace_id for s in inserts)
    job = by_name["job index"][0]
    assert job.kind == trace.SpanKind.CONSUMER and job.parent.span_id == root.context.span_id
    assert hdrs["traceparent"] == f"00-{root.context.trace_id:032x}-{root.context.span_id:016x}-01"


def test_otel_is_off_unless_configured(monkeypatch):
    tracing.reset_otel()
    try:
        assert tracing.otel_tracer() is None          # installed, but nobody set up a provider
        monkeypatch.setenv("PYWEB_OTEL", "0")
        tracing.reset_otel()
        assert tracing.otel_tracer() is None
    finally:
        tracing.reset_otel()


# ------------------------------------------------------------- dev toolbar

def test_dev_toolbar_on_pages_while_developing(shop):
    from pyweb.cli import _DevSite, _DevState
    site = shop()
    state = _DevState(site.app_path)
    state.site = site
    dev = _DevSite(state)
    rpc(site, "add", name="mug")
    status, hdrs, raw = dev.respond("GET", "/", {"Host": "localhost", "Accept-Encoding": "gzip"})
    assert status == 200 and b"pyweb-devtools" in raw and b"window.__PYWEB_REQ__=" in raw
    summary = json.loads(raw.split(b"window.__PYWEB_REQ__=")[1].split(b";</script>")[0])
    assert summary["route"] == "page:Home" and summary["id"] == dict(hdrs)["X-Request-Id"]
    status, _, body = dev.respond("GET", "/__pyweb/dev/requests", {})
    reqs = json.loads(body)["requests"]
    add = next(r for r in reversed(reqs) if r["route"] == "rpc:add")
    assert [s["name"] for s in add["spans"]] == ["make item"]
    assert any(q["sql"].startswith("INSERT") for q in add["sql"])
    assert add["events"][0]["kind"] == "job" and add["events"][0]["name"] == "index"
    jobs_ = json.loads(dev.respond("GET", "/__pyweb/dev/jobs", {})[2])
    assert jobs_["jobs"][0]["name"] == "index"
    # a page elsewhere can't press buttons in your dev server
    assert dev.respond("POST", f"/__pyweb/dev/jobs/{jobs_['jobs'][0]['id']}/retry",
                       {"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_no_toolbar_in_production(shop, tmp_path):
    site = shop(debug=False)
    status, _, raw = site.respond("GET", "/", {"Host": "localhost"})
    assert status == 200 and b"pyweb-devtools" not in raw
    assert site.respond("GET", "/__pyweb/dev/requests", {"Host": "localhost"})[0] == 404
    from pyweb.cli import main
    out = tmp_path / "dist"
    main(["build", site.app_path, "--out", str(out), "--production"])
    for root, _, files in os.walk(out):
        for fn in files:
            with open(os.path.join(root, fn), "rb") as fh:
                assert b"pyweb-devtools" not in fh.read(), fn


def test_production_records_nothing_for_the_toolbar(shop):
    site = shop(debug=False)
    before = len(telemetry.RECENT)
    site.respond("GET", "/", {"Host": "localhost"})
    assert len(telemetry.RECENT) == before


# -------------------------------------------------------- several processes

@pytest.mark.skipif(not hasattr(os, "fork"), reason="forked workers")
def test_metrics_add_up_across_worker_processes(tmp_path):
    (tmp_path / "app.pyweb").write_text('from pyweb import App\napp = App()\n\n@app.page("/")\n'
                                        'def Home():\n    <p>hi</p>\n')
    env = {**os.environ, "PYWEB_METRICS_TOKEN": "tok-123456", "PYWEB_METRICS_EVERY": "0.2",
           "PYWEB_AUTH_SECRET": "x" * 64, "PYWEB_WORKER": "0", "PYWEB_ENV": "development"}
    env.pop("PYWEB_METRICS_DIR", None)
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "serve", str(tmp_path / "app.pyweb"),
                             "--port", str(port), "--host", "127.0.0.1", "--workers", "3"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        url = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        while True:
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1).read()
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.1)
        for _ in range(30):
            # a new connection each time, so the kernel spreads them across the workers
            urllib.request.urlopen(url + "/", timeout=5).read()
        total = 0
        deadline = time.time() + 10
        while time.time() < deadline:
            req = urllib.request.Request(url + "/metrics", headers={"Authorization": "Bearer tok-123456"})
            text = urllib.request.urlopen(req, timeout=5).read().decode()
            line = next((x for x in text.splitlines()
                         if x.startswith('pyweb_http_requests_total{method="GET",route="page:Home",status="200"}')), "")
            total = int(float(line.split()[-1])) if line else 0
            if total == 30:
                break
            time.sleep(0.2)
        assert total == 30
    finally:
        proc.terminate()
        proc.wait(10)
