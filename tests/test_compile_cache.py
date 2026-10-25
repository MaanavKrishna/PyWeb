"""The UI kit is compiled once per process, and module code is reused across reloads; the output must be
exactly what a fresh compile gives, in any order and from several threads."""

import glob
import os
import threading

import pytest

from pyweb.compiler import compile_source, pipeline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = sorted(glob.glob(os.path.join(ROOT, "examples", "*", "app.pyweb"))
              + glob.glob(os.path.join(ROOT, "pyweb", "templates", "*.pyweb")))


def output(path):
    pages = compile_source(open(path, encoding="utf-8").read(), filename=path)["pages"]
    return {name: (p["js"], p["html"]) for name, p in pages.items()}


@pytest.fixture(scope="module")
def fresh():
    old = os.environ.get("PYWEB_NO_KIT_CACHE")
    os.environ["PYWEB_NO_KIT_CACHE"] = "1"
    saved = dict(pipeline._KIT_LIBS)
    pipeline._KIT_LIBS.clear()
    try:
        return {path: output(path) for path in APPS}
    finally:
        pipeline._KIT_LIBS.update(saved)
        if old is None:
            os.environ.pop("PYWEB_NO_KIT_CACHE")
        else:
            os.environ["PYWEB_NO_KIT_CACHE"] = old


@pytest.mark.parametrize("order", ["forward", "reverse"])
def test_cached_kit_gives_the_same_output_in_any_order(fresh, order):
    pipeline._KIT_LIBS.clear()
    for path in (APPS if order == "forward" else APPS[::-1]):
        assert output(path) == fresh[path], os.path.relpath(path, ROOT)
    assert pipeline._KIT_LIBS                             # the kit really was cached


def test_concurrent_compiles_match(fresh):
    bad = []

    def work(i):
        for path in APPS[i:] + APPS[:i]:
            if output(path) != fresh[path]:
                bad.append(path)
    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not bad


def test_a_changed_kit_file_is_compiled_again(tmp_path, monkeypatch):
    first = next(iter(pipeline._KIT_LIBS)) if pipeline._KIT_LIBS else None
    if first is None:
        output(APPS[0])
        first = next(iter(pipeline._KIT_LIBS))
    stamp, lib = pipeline._KIT_LIBS[first][0], pipeline._KIT_LIBS[first][1]
    monkeypatch.setattr(os.path, "getmtime", lambda p, _real=os.path.getmtime: stamp[0] + 1 if p == first else _real(p))
    for path in APPS:
        output(path)
    assert pipeline._KIT_LIBS[first][1] is not lib


def test_reloading_an_app_reuses_module_code(tmp_path):
    from pyweb import app_loader
    from pyweb.hosting import Site
    path = tmp_path / "app.pyweb"
    path.write_text("from pyweb import App\nfrom pyweb.ui import Button\napp = App()\n\n@app.page('/')\n"
                    "def Home():\n    n = 1\n    <Button>{n}</Button>\n")
    Site(str(path))
    before = len(app_loader._CODE)
    Site(str(path))
    assert len(app_loader._CODE) == before                # nothing new to compile
    path.write_text(path.read_text().replace("n = 1", "n = 2"))
    site = Site(str(path))
    assert len(app_loader._CODE) == before + 1            # only the changed app file
    from pyweb.testing import TestClient
    assert ">2<" in TestClient(str(path)).get("/").text.replace(" ", "")
    del site


def test_disk_cache_gives_the_same_output_in_a_new_process(tmp_path):
    """A second process reads parse results and module code from disk; its output matches exactly."""
    import json
    import subprocess
    import sys
    script = ("import json, sys\n"
              "from pyweb.app_loader import LoadedApp\n"
              "from pyweb.compiler import diskcache\n"
              "out = {}\n"
              "for p in sys.argv[1:]:\n"
              "    a = LoadedApp(p)\n"
              "    out[p] = {n: [pg['js'], pg['html']] for n, pg in a.compiled['pages'].items()}\n"
              "print(json.dumps(out))\n")
    apps = [p for p in APPS if "templates" not in p][:4]
    env = {**os.environ, "PYWEB_CACHE": "1", "PYWEB_CACHE_DIR": str(tmp_path), "PYTHONPATH": ROOT}

    def run(cache="1"):
        res = subprocess.run([sys.executable, "-c", script, *apps], env={**env, "PYWEB_CACHE": cache},
                             capture_output=True, text=True, cwd=ROOT, check=True)
        return json.loads(res.stdout)

    plain = run("0")
    assert not list(tmp_path.iterdir())
    first = run()
    entries = list(tmp_path.rglob("*.bin"))
    assert any(e.name.startswith("parse-") for e in entries) and any(e.name.startswith("code-") for e in entries)
    assert run() == first == plain
    for e in entries:                          # corrupt every entry: it recovers and rewrites them
        e.write_bytes(b"garbage")
    assert run() == plain
    assert all(e.read_bytes() != b"garbage" for e in tmp_path.rglob("*.bin"))


def test_disk_cache_is_keyed_by_version(tmp_path, monkeypatch):
    from pyweb.compiler import diskcache
    monkeypatch.setenv("PYWEB_CACHE", "1")
    monkeypatch.setenv("PYWEB_CACHE_DIR", str(tmp_path))
    diskcache.put("parse", "k", b"one")
    assert diskcache.get("parse", "k") == b"one"
    monkeypatch.setattr("pyweb.__version__", "0.0.0-other")
    assert diskcache.get("parse", "k") is None


def test_disk_cache_off_unless_asked(tmp_path, monkeypatch):
    from pyweb.compiler import diskcache
    monkeypatch.setenv("PYWEB_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("PYWEB_CACHE", "0")
    diskcache.put("parse", "k", b"one")
    assert diskcache.get("parse", "k") is None and not list(tmp_path.iterdir())


def test_disk_cache_prunes_old_entries(tmp_path, monkeypatch):
    from pyweb.compiler import diskcache
    monkeypatch.setenv("PYWEB_CACHE", "1")
    monkeypatch.setenv("PYWEB_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(diskcache, "KEEP", 5)
    for i in range(9):
        diskcache.put("parse", str(i), b"x")
    assert len(list(tmp_path.rglob("*.bin"))) == 5
