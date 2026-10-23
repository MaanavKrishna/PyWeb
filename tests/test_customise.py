"""Customising the UI kit: helpers imported from .pyweb files, cx/variants/style, ui.extend, the
dev toolbar's theme endpoints and "Save to app.pyweb", editor completions and warnings, and the
accessibility lint."""

import json
import re

import pytest

from pyweb import a11y, lsp
from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError
from pyweb.testing import TestClient
from pyweb.ui import cx, extending, style, variants
from pyweb.ui.source_edit import set_theme, theme_code


@pytest.fixture(autouse=True)
def _clean_extensions():
    extending._EXTENDED.clear()
    yield
    extending._EXTENDED.clear()


# ------------------------------------------------------------------ functions from other .pyweb files

def test_functions_from_other_pyweb_files_work_in_the_browser(tmp_path):
    (tmp_path / "fmt.pyweb").write_text(
        'UNIT = "pts"\n\n\ndef _pad(s):\n    return " " + s + " "\n\n\n'
        'def label(n):\n    return _pad(str(n) + " " + UNIT)\n')
    app = tmp_path / "app.pyweb"
    app.write_text('from pyweb import App\nfrom fmt import label\napp = App()\n\n\n@app.page("/")\ndef Home():\n'
                   '    n = 1\n\n    def inc():\n        n += 1\n    <p onclick={inc}>{label(n)}</p>\n')
    js = compile_source(app.read_text(), str(app))["pages"]["Home"]["js"]
    assert "function label(n)" in js and "function _pad(s)" in js and 'const UNIT = "pts";' in js
    assert "<p> 1 pts </p>" in TestClient(str(app)).get("/").text
    app.write_text(app.read_text().replace("from fmt import label", "from fmt import label as lbl"))
    with pytest.raises(CompileError, match="without `as`"):
        compile_source(app.read_text(), str(app))


# ------------------------------------------------------------------ cx, variants, style

def test_cx_merges_conflicting_utilities():
    assert cx("px-2 py-1", "px-4") == "py-1 px-4"
    assert cx("text-sm text-gray-500", "text-lg", {"text-red-600": True, "hidden": False}) == "text-lg text-red-600"
    assert cx("p-2", "md:p-4", "md:p-6", "hover:bg-red-500 bg-white") == "p-2 md:p-6 hover:bg-red-500 bg-white"
    assert cx("block", ["flex", None, ""], "font-bold font-sans", "font-medium") == "flex font-sans font-medium"
    assert cx(None, False, "") == ""
    assert cx("pw-button pw-button") == "pw-button"


def test_variants_and_style():
    card = variants(base="rounded border p-4", variant={"plain": "bg-white", "brand": "bg-indigo-600 text-white"},
                    size={"sm": "p-2 text-sm", "lg": "p-6"}, default={"variant": "plain", "size": "sm"},
                    compound=[{"variant": "brand", "size": "lg", "class": "shadow-lg"}])
    assert style(card) == "rounded border bg-white p-2 text-sm"
    assert style(card, variant="brand", size="lg") == "rounded border bg-indigo-600 text-white p-6 shadow-lg"
    assert style(card, size="lg", class_="p-10 bg-black") == "rounded border p-10 bg-black"


SPEC_APP = '''from pyweb import App
from pyweb.ui import cx, variants, style
app = App()

card = variants(base="rounded p-4", variant={"a": "bg-white", "b": "bg-black text-white"}, default={"variant": "a"})


def Card(variant=None, class_=None, children=None, **attrs):
    <div class={style(card, variant=variant, class_=class_)} {**attrs}>{children}</div>


@app.page("/")
def Home():
    dark = False

    def flip():
        dark = not dark
    <main>
        <button onclick={flip}>flip</button>
        <Card id="c" variant={"b" if dark else "a"} class_="p-2">x</Card>
        <p class={cx("px-2", dark and "px-6")}>y</p>
    </main>
'''


@pytest.mark.parametrize("verify", ["0", "1"])
def test_class_helpers_on_the_server_and_in_browser_code(monkeypatch, verify):
    monkeypatch.setenv("PYWEB_SSR_VERIFY", verify)
    html = TestClient(source=SPEC_APP).get("/").text
    assert '<div class="rounded bg-white p-2" id="c">x</div>' in html and '<p class="px-2">y</p>' in html
    js = compile_source(SPEC_APP)["pages"]["Home"]["js"]
    assert "function style(spec" in js and "function cx(" in js
    assert re.search(r'const card = \{"base": "rounded p-4", "variant": \{"a": "bg-white"', js)  # worked out at compile time


# ------------------------------------------------------------------ ui.extend

def test_extend_adds_css_for_kit_variants():
    from pyweb import ui
    ui.extend("Button", variant={"brand": {"background": "var(--pw-accent-11)", "color": "white",
                                           "hover": {"background": "var(--pw-accent-12)"}}},
              size={"xl": "height: 52px; padding: 0 28px"})
    css = extending.css()
    assert '.pw-button[data-variant="brand"]{background:var(--pw-accent-11);color:white}' in css
    assert '.pw-button[data-variant="brand"]:hover{background:var(--pw-accent-12)}' in css
    assert '.pw-button[data-size="xl"]{height:52px;padding:0 28px}' in css
    assert css.startswith("@layer pyweb.components{")
    assert "brand" in extending.values("Button")["variant"]
    for bad in ({"x": {"color": "red;}body{x:y"}}, {"x": {"background": "url(javascript:alert(1))"}},
                {"x": {"bad prop": "1"}}, {"x y": {"color": "red"}}, {"x": {"color": "red", "wiggle": {"x": 1}}}):
        with pytest.raises(ValueError):
            ui.extend("Button", variant=bad)
    with pytest.raises(ValueError, match="no kit component"):
        ui.extend("Nope", variant={"a": {"color": "red"}})


def test_extend_reaches_the_page():
    src = '''from pyweb import App, ui
from pyweb.ui import Button, Theme
app = App(theme=Theme(extend={"Badge": {"tone": {"brand": {"background": "hotpink"}}}}))
ui.extend("Button", variant={"brand": {"color": "white"}})


@app.page("/")
def Home():
    <Button variant="brand">Go</Button>
'''
    c = TestClient(source=src)
    html = c.get("/").text
    assert 'data-variant="brand"' in html
    href = re.search(r'href="(/__pyweb/theme\.css\?v=[^"]+)"', html).group(1)
    css = c.get(href).text
    assert '.pw-button[data-variant="brand"]{color:white}' in css
    assert '.pw-badge[data-tone="brand"]{background:hotpink}' in css


# ------------------------------------------------------------------ dev toolbar: theme

def test_theme_code_and_source_edits():
    assert theme_code({"accent": "#ff0066", "radius": "12", "font": "system"}) == 'Theme(accent="#ff0066", radius=12)'
    with pytest.raises(ValueError):
        theme_code({"radius": 99})
    src = '"""App — ünïcode."""\nfrom pyweb import App\napp = App(title="Tést",\n          lang="en")  # keep\n\n\n' \
          '@app.page("/")\ndef Home():\n    <p>x</p>\n'
    one = set_theme(src, {"accent": "#ff0066"})
    assert 'app = App(title="Tést",\n          lang="en", theme=Theme(accent="#ff0066"))  # keep' in one
    assert "from pyweb import App\nfrom pyweb.ui import Theme\n" in one
    two = set_theme(one, {"radius": 4})
    assert "theme=Theme(radius=4))" in two and two.count("from pyweb.ui import Theme") == 1
    assert set_theme("from pyweb import App\napp = App()\n", {"dark": "dark"}).endswith('app = App(theme=Theme(dark="dark"))\n')
    kept = set_theme('from pyweb.ui import Theme\napp = App(theme=Theme(extend={"Button": {}}))\n', {"radius": 2})
    assert 'App(theme=Theme(radius=2, extend={"Button": {}}))' in kept
    with pytest.raises(ValueError, match="no App"):
        set_theme("x = 1\n", {})


def test_dev_toolbar_theme_endpoints(tmp_path):
    from pyweb.cli import _DevSite, _DevState, _dev_load
    from pyweb.hosting import Site
    app = tmp_path / "app.pyweb"
    app.write_text('from pyweb import App\nfrom pyweb.ui import Button\napp = App(title="T")\n\n\n'
                   '@app.page("/")\ndef Home():\n    <Button>Go</Button>\n')
    state = _DevState(str(app))
    _dev_load(state, Site)
    dev = _DevSite(state)
    status, hdrs, body = dev.respond("GET", "/__pyweb/dev/theme.css?radius=20&accent=%23ff0066", {})
    assert status == 200 and b"--pw-radius:20px" in body.replace(b" ", b"")
    status, _h, body = dev.respond("GET", "/__pyweb/dev/theme.css?radius=500", {})
    assert status == 400
    status, _h, body = dev.respond("GET", "/__pyweb/dev/theme", {})
    info = json.loads(body)
    assert info["theme"]["radius"] == 10 and "zinc" in info["presets"] and info["can_save"]
    evil = dev.respond("POST", "/__pyweb/dev/theme", {"Origin": "https://evil.example", "Host": "127.0.0.1:8000"},
                       b'{"radius": 3}')
    assert evil[0] == 403 and "theme=" not in app.read_text()
    ok = dev.respond("POST", "/__pyweb/dev/theme", {"Sec-Fetch-Site": "same-origin"}, b'{"radius": 3, "accent": "#6366f1"}')
    assert ok[0] == 200 and json.loads(ok[2])["code"] == "Theme(radius=3)"
    assert 'app = App(title="T", theme=Theme(radius=3))' in app.read_text()
    bad = dev.respond("POST", "/__pyweb/dev/theme", {"Sec-Fetch-Site": "same-origin"}, b'{"density": "huge"}')
    assert bad[0] == 400
    # Never outside `pyweb dev`.
    assert TestClient(str(app)).get("/__pyweb/dev/theme").status == 404


# ------------------------------------------------------------------ editor support

EDITOR = '''from pyweb import App, ui
from pyweb.ui import Button, Badge
app = App(lang="en")
ui.extend("Button", variant={"brand": {"color": "white"}})


@app.page("/")
def Home():
    <main>
        <Button variant="fancy">x</Button>
        <Button variant="brand" size="huge">y</Button>
        <div animate="wobble" transition={{"stiffness": 5000, "type": "springy"}}>z</div>
        <ul initial="hidden" animate="show" variants={{"hidden": {"opacity": 0}, "show": {"opacity": 1}}}></ul>
        <p animate="fade-up">ok</p>
    </main>
'''


def test_editor_warnings():
    found = {(d["range"]["start"]["line"] + 1, d.get("code")) for d in lsp.diagnostics(EDITOR, "app.pyweb")}
    assert (10, "unknown-variant") in found                   # fancy
    assert (11, "unknown-variant") in found                   # huge (brand is the app's own)
    msgs = [d["message"] for d in lsp.diagnostics(EDITOR, "app.pyweb") if d["range"]["start"]["line"] == 10]
    assert any("size 'huge'" in m for m in msgs) and not any("brand" in m and "variant 'brand'" in m for m in msgs)
    assert (12, "unknown-preset") in found and (12, "motion-range") in found
    assert not any(line in (13, 14) for line, _c in found)


def _complete(text, line, marker):
    lines = text.splitlines()
    col = lines[line].index(marker) + len(marker)
    return [i["label"] for i in lsp.completions(text, "app.pyweb", line, col)]


def test_editor_completions():
    src = EDITOR.replace('variant="fancy"', 'variant=""')
    labels = _complete(src, 9, 'variant="')
    assert labels[:2] == ["default", "secondary"] and "brand" in labels
    assert "fade-up" in _complete(src, 11, 'animate="')
    t = "from pyweb.ui import Theme\napp = App(theme=Theme(radius=4, ))\n"
    assert "accent" in _complete(t, 1, "radius=4, ") and "radius" not in _complete(t, 1, "radius=4, ")
    assert _complete('Motion(feel="', 0, 'feel="') == ["snappy", "gentle", "bouncy", "none"]
    assert "Dialog" in _complete("from pyweb.ui import ", 0, "import ") and "cx" in _complete("from pyweb.ui import ", 0, "import ")
    assert "stiffness" in _complete('<div transition={{"', 0, '{{"')


# ------------------------------------------------------------------ accessibility lint

A11Y = '''from pyweb import App
app = App(lang="")


@app.page("/")
def Home():
    <main>
        <img src="/a.png" />
        <img src="/b.png" alt="" />
        <div onclick={print}>open</div>
        <div role="button" tabindex="0" onclick={print}>ok</div>
        <input name="q" />
        <input name="r" placeholder="Search" />
        <label>Name <input name="n" /></label>
        <label for="e">Email</label>
        <input id="e" name="e" />
        <input type="hidden" name="h" />
        <button></button>
        <button aria-label="Close">×</button>
        <button><img src="/x.png" alt="Delete" /></button>
        <a href="/x"></a>
        <a href="/y">y</a>
        <span tabindex="3">s</span>
    </main>
'''


def test_accessibility_lint():
    found = [(f["line"], f["kind"]) for f in a11y.check(A11Y)]
    assert found == [(2, "a11y-html-lang"), (8, "a11y-img-alt"), (10, "a11y-click-static"), (12, "a11y-input-label"),
                     (18, "a11y-button-name"), (21, "a11y-link-name"), (23, "a11y-tabindex-positive")]


def test_kit_examples_and_templates_pass_the_lint():
    import glob
    files = glob.glob("pyweb/ui/kit/*.pyweb") + glob.glob("examples/*/app.pyweb") + \
        glob.glob("pyweb/templates/**/*.pyweb", recursive=True)
    assert files
    for f in files:
        assert a11y.check(open(f, encoding="utf-8").read(), f) == [], f


def test_check_reports_accessibility(tmp_path, capsys):
    from pyweb.cli import main
    app = tmp_path / "app.pyweb"
    app.write_text(A11Y)
    main(["check", str(app)])                                  # warnings: no failure
    out = capsys.readouterr().out
    assert "a11y-img-alt" in out and f"{app}:8:" in out
    with pytest.raises(SystemExit):
        main(["check", "--strict", str(app)])
