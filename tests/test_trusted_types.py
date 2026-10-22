"""Trusted Types and CSP violation reports (the browser side is in tests/e2e/test_trusted_types_browser.py)."""

import json
import logging
import os
import re

from pyweb import csp_report
from pyweb.serve import DEFAULT_CSP, csp_for
from pyweb.telemetry.instruments import csp_violations
from pyweb.testing import TestClient

RUNTIME = os.path.join(os.path.dirname(__file__), "..", "pyweb", "runtime", "browser")
SINKS = re.compile(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write|parseFromString|"
                   r"createContextualFragment|srcdoc|new Function|\beval\(")


def test_the_runtime_only_reaches_html_sinks_through_its_policies():
    found = []
    for name in sorted(os.listdir(RUNTIME)):
        if name.endswith(".js"):
            for n, line in enumerate(open(os.path.join(RUNTIME, name), encoding="utf-8"), 1):
                if SINKS.search(line) and not line.lstrip().startswith("//"):
                    found.append((name, n, "Policy.createHTML(" in line))
    assert found and all(ok for _, _, ok in found), found


def test_default_policy_enforces_trusted_types_and_reports():
    assert "require-trusted-types-for 'script'" in DEFAULT_CSP
    assert "trusted-types pyweb-md pyweb-nav" in DEFAULT_CSP
    assert "report-uri /__pyweb/csp-report" in DEFAULT_CSP and "report-to" not in DEFAULT_CSP


def test_pages_with_npm_packages_drop_trusted_types_only(monkeypatch):
    monkeypatch.delenv("PYWEB_CSP", raising=False)
    plain = csp_for(DEFAULT_CSP, b"<p>hi</p>")
    npm = csp_for(DEFAULT_CSP, b'<script type="importmap">{"imports": {}}</script>')
    assert "trusted-types" in plain
    assert "trusted-types" not in npm and "script-src 'self' 'sha256-" in npm
    assert "report-uri /__pyweb/csp-report" in npm and "object-src 'none'" in npm


def test_a_custom_policy_is_left_alone(monkeypatch):
    custom = "default-src 'self'; require-trusted-types-for 'script'; trusted-types pyweb-md pyweb-nav"
    monkeypatch.setenv("PYWEB_CSP", custom)
    assert "require-trusted-types-for" in csp_for(custom, b'<script type="importmap">{}</script>')


REPORT_URI = {"csp-report": {
    "document-uri": "https://app.test/account?token=secret#x", "violated-directive": "script-src-elem",
    "effective-directive": "script-src-elem", "blocked-uri": "https://evil.test/x.js?k=1",
    "source-file": "https://app.test/static/app.js", "line-number": 12, "script-sample": "alert(1)" * 30}}
REPORT_TO = [{"type": "csp-violation", "url": "https://app.test/", "body": {
    "documentURL": "https://app.test/", "effectiveDirective": "require-trusted-types-for",
    "blockedURL": "trusted-types-sink", "sample": "Element innerHTML|<img>", "disposition": "enforce"}},
    {"type": "deprecation", "body": {}}]


def test_reports_in_both_formats_are_parsed_and_cleaned():
    (one,) = csp_report.parse("application/csp-report", json.dumps(REPORT_URI).encode())
    assert one["directive"] == "script-src-elem" and one["line"] == 12
    assert one["page"] == "https://app.test/account" and one["blocked"] == "https://evil.test/x.js"
    assert len(one["sample"]) == 80
    (two,) = csp_report.parse("application/reports+json", json.dumps(REPORT_TO).encode())
    assert two["directive"] == "require-trusted-types-for" and two["blocked"] == "trusted-types-sink"
    weird = {"csp-report": {"effective-directive": "made-up", "line-number": True}}
    (odd,) = csp_report.parse("", json.dumps(weird).encode())
    assert odd["directive"] == "other" and odd["line"] is None
    assert csp_report.parse("", b"not json") == [] and csp_report.parse("", b'{"x": 1}') == []


def test_the_endpoint_counts_logs_and_refuses_junk(caplog, tmp_path):
    (tmp_path / "app.pyweb").write_text('from pyweb import App\napp = App()\n@app.page("/")\ndef H():\n    <p>hi</p>\n')
    client = TestClient(str(tmp_path / "app.pyweb"))
    before = csp_violations.value(directive="script-src-elem")
    with caplog.at_level(logging.WARNING, logger="pyweb.csp"):
        r = client.request("POST", "/__pyweb/csp-report", json.dumps(REPORT_URI).encode(),
                           {"Content-Type": "application/csp-report"})
    assert r.status == 204
    assert csp_violations.value(directive="script-src-elem") == before + 1
    assert "https://evil.test/x.js" in caplog.text and "token=secret" not in caplog.text
    assert client.request("GET", "/__pyweb/csp-report").status == 405
    assert client.request("POST", "/__pyweb/csp-report", b"{}", {"Content-Type": "text/plain"}).status == 415
    assert client.request("POST", "/__pyweb/csp-report", b"x" * 20000,
                          {"Content-Type": "application/csp-report"}).status == 413


def test_log_lines_are_capped(monkeypatch, caplog):
    monkeypatch.setattr(csp_report, "LOG_PER_MINUTE", 3)
    monkeypatch.setattr(csp_report, "_window", [0.0, 0])
    with caplog.at_level(logging.WARNING, logger="pyweb.csp"):
        for _ in range(10):
            csp_report.handle("POST", {"content-type": "application/csp-report"}, json.dumps(REPORT_URI).encode())
    assert caplog.text.count("CSP blocked") == 3
