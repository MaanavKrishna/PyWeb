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
    monkeypatch.setattr(os.path, "getmtime", lambda p, _real=os.path.getmtime: stamp + 1 if p == first else _real(p))
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
