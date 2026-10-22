"""``pyweb profile``: the built-in sampler and the Tachyon report fix."""

import socket
import threading
import time
import urllib.request

import pytest

from pyweb import profile


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_report_script_ends_are_escaped(tmp_path):
    page = tmp_path / "fg.html"
    page.write_text('<script>var d = {"src": "x = \'</script>\'"};</script>\n<script src="a.js"></script>\n</body>',
                    encoding="utf-8")
    profile._fix_embedded_script_ends(page)
    html = page.read_text(encoding="utf-8")
    assert "'<\\/script>'" in html                       # the source line inside the data
    assert html.count("</script>") == 2                   # real tags are untouched


@pytest.mark.skipif(profile._free_threaded(), reason="the built-in sampler needs the GIL")
def test_builtin_sampler_writes_a_report(tmp_path, monkeypatch):
    monkeypatch.setenv("PYWEB_WORKER", "0")
    app = tmp_path / "app.pyweb"
    app.write_text('from pyweb import App\n\napp = App()\n\n\n@app.page("/")\ndef Home():\n'
                   '    total = sum(i * i for i in range(20000))\n\n    <p>{total}</p>\n', encoding="utf-8")
    port = _free_port()
    stop = threading.Event()

    def load():
        while not stop.is_set():
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
            except OSError:
                time.sleep(0.05)
    t = threading.Thread(target=load, daemon=True)
    t.start()
    lines = []
    try:
        report = profile._sample_in_process(str(app), 1.5, port)
    finally:
        stop.set()
        t.join(3)
    assert "samples in 1.5s" in report and "time spent under (inclusive)" in report, report
    out = tmp_path / "p.txt"
    monkeypatch.setattr(profile, "tachyon_available", lambda: False)
    monkeypatch.setattr(profile, "_sample_in_process", lambda *a: "3 samples in 1s\n\nx\n")
    assert profile.run(str(app), seconds=1, out=str(out), printer=lines.append) == str(out)
    assert out.read_text().startswith("3 samples") and any("report:" in s for s in lines)


def test_builtin_sampler_refuses_free_threaded_builds(monkeypatch):
    monkeypatch.setattr(profile, "_free_threaded", lambda: True)
    with pytest.raises(RuntimeError, match="PYTHON_GIL=1"):
        profile._sample_in_process("app.pyweb", 1, 0)
