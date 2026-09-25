"""Deploy manifests, upload validation, islands + streaming SSR."""

import pytest

from pyweb import deploy, uploads
from pyweb.compiler import parser as P
from pyweb.compiler.codegen.emit_html import (
    emit_island,
    stream_fragment,
    stream_shell,
)


def test_dockerfile_shape():
    df = deploy.dockerfile(python="3.13-slim", port=8000)
    assert "FROM python:3.13-slim" in df and "EXPOSE 8000" in df
    assert "pyweb.cli" in df


def test_compose_with_and_without_db():
    assert "DATABASE_URL" not in deploy.compose()
    assert "DATABASE_URL=postgres://" in deploy.compose(db_url="postgres://x")


def test_k8s_manifest():
    m = deploy.k8s_manifest(app="shop", image="shop:1", port=9000, replicas=3)
    assert "replicas: 3" in m and "shop:1" in m and "containerPort: 9000" in m
    assert m.count("---") == 1


def test_expand_env_defaults_and_missing():
    assert deploy.expand_env("a=${X:-d}", {}) == "a=d"
    assert deploy.expand_env("a=${X}", {"X": "1"}) == "a=1"
    assert deploy.expand_env("a=${X}", {}) == "a="


def test_required_env():
    assert deploy.required_env(["A"], {"A": "1"}) == {"A": "1"}
    with pytest.raises(RuntimeError):
        deploy.required_env(["MISSING"], {})


def test_safe_filename():
    assert uploads.safe_filename("../../etc/passwd") == "passwd"
    assert uploads.safe_filename("a/b\\c?.png") != ""
    assert uploads.safe_filename("") == "file"
    assert uploads.safe_filename("...") == "file"


def test_upload_validation_ok():
    clean, errors = uploads.validate_upload(filename="pic.png", size=100,
                                             content_type="image/png",
                                             allowed_types=["image/png"])
    assert errors == [] and clean == "pic.png"


def test_upload_rejects_traversal_type_size():
    _, e1 = uploads.validate_upload(filename="../x.sh", size=10, content_type="text/plain")
    assert "unsafe filename" in e1
    _, e2 = uploads.validate_upload(filename="a.png", size=10**9, content_type="image/png")
    assert any("too large" in e for e in e2)
    _, e3 = uploads.validate_upload(filename="a.exe", size=10, content_type="application/x-exe",
                                     allowed_types=["image/png"])
    assert any("unsupported type" in e for e in e3)
    _, e4 = uploads.validate_upload(filename="a.png", size=None, content_type="image/png")
    assert "unknown size" in e4


def test_storage_path_unique_prefixed():
    a = uploads.storage_path("pic.png")
    b = uploads.storage_path("pic.png")
    assert a != b and a.startswith("uploads/") and a.endswith("-pic.png")


def test_island_marker_and_content():
    ui = P.parse_ui_block({1: "<p>Hi</p>"})
    out = emit_island("counter", ui, {})
    assert 'data-pyweb-island="counter"' in out and "<p>Hi</p>" in out


def test_streaming_roundtrip():
    ui = P.parse_ui_block({1: "<h1>T</h1>"})
    head, tail = stream_shell("/r", "T", js_url="/static/H.js")
    frag = stream_fragment(ui, {})
    page = head + frag + tail
    assert page.startswith("<!doctype html>") and "<h1>T</h1>" in page
    assert page.rstrip().endswith("</html>") and "/static/H.js" in tail
