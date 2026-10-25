"""Page variables carry their names so ``pyweb dev`` can keep them across reloads; production pays ~20 bytes."""

import gzip

from pyweb.compiler import compile_source

SRC = '''from pyweb import App
app = App()

def Counter(start: int):
    c = start

    def bump():
        c += 1
    <button onclick={bump}>{c}</button>

@app.page("/")
def Home():
    count = 0

    def inc():
        count += 1
    <main><button onclick={inc}>{count}</button><Counter start={1} /></main>
'''


def page_js():
    out = compile_source(SRC, filename="app.pyweb")
    return out["pages"]["Home"]["js"]


def test_page_signals_are_named_and_component_signals_are_not():
    js = page_js()
    assert '$signal("count" in $s ? $s["count"] : 0, "count")' in js
    line = next(x for x in js.splitlines() if "const c = $signal(" in x)
    assert '"c"' not in line                     # component state isn't kept


def test_the_runtime_registers_named_signals_only_when_the_dev_script_asks():
    from pyweb.build import minify_js
    import os
    import pyweb
    path = os.path.join(os.path.dirname(pyweb.__file__), "runtime", "browser", "runtime.js")
    src = open(path, encoding="utf-8").read()
    assert "if (name && G.keep) G.keep(name, s);" in src
    assert len(gzip.compress(minify_js(src).encode(), 9)) < 15360


def test_the_dev_script_is_inline_safe():
    from pyweb.cli import DEV_RELOAD_JS
    assert DEV_RELOAD_JS.startswith("<script>") and DEV_RELOAD_JS.endswith("</script>")
    bodies = DEV_RELOAD_JS[len("<script>"):-len("</script>")].split("</script><script>")
    assert len(bodies) == 5 and all("</" not in b for b in bodies)     # reload, inspect, assist, timeline, welcome
    assert "sessionStorage" in bodies[0] and "G.keep" in bodies[0] and "fresh" in bodies[0]
    assert "/__pyweb/dev/open" in bodies[1] and "/__pyweb/dev/ai/" in bodies[2]
