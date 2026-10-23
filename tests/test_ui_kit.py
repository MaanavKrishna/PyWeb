"""The UI kit's foundations: attribute pass-through ({**attrs}, **attrs), the theme engine, kit
imports, serving, and `pyweb ui`. Components' behaviour in a browser: tests/e2e/test_ui_browser.py."""

import json
import os
import re

import pytest

from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError
from pyweb.testing import TestClient
from pyweb.ui import theme as T

HEAD = "from pyweb import App\napp = App()\n\n"


def body(html):
    return html.split('data-pw-root="Home">')[1].split("</div><script")[0].split("</div></body>")[0]


# ------------------------------------------------------------------ {**attrs} and **attrs

SPREAD_APP = HEAD + '''
def Button(variant="default", onclick=None, children=None, **attrs):
    <button class={"btn btn-" + variant} onclick={onclick} {**attrs}>{children}</button>


@app.page("/")
def Home():
    n = 0
    extra = {"data-x": "1", "class": "wide", "title": "from-dict", "onclick": None, "children": "no"}

    def inc():
        n += 1
    <main>
        <Button variant="primary" onclick={inc} id="b" aria-label="go" class_="big">n={n}</Button>
        <div class="box" {**extra} title="mine">x</div>
        <Button {**extra}>spread</Button>
    </main>
'''


@pytest.mark.parametrize("verify", ["1", ""])
def test_spread_and_rest_props_render_on_the_server(monkeypatch, verify):
    monkeypatch.setenv("PYWEB_SSR_VERIFY", verify)       # "1": the compiled renderer must match the interpreter
    html = body(TestClient(source=SPREAD_APP).get("/").text)
    # Unknown props reach the element; class_ merges with the component's classes.
    assert '<button class="btn btn-primary big" id="b" aria-label="go">n=0</button>' in html
    # Written attributes win over the dict; classes merge; handlers and children never become attributes.
    assert '<div class="box wide" data-x="1" title="mine">x</div>' in html
    # A {**dict} on a component: keys it names become props, the rest pass through.
    assert '<button class="btn btn-default wide" data-x="1" title="from-dict">spread</button>' in html


def test_spread_and_rest_props_in_browser_code():
    js = compile_source(SPREAD_APP)["pages"]["Home"]["js"]
    assert 'const attrs = $p["$rest"] || (() => ({}));' in js
    assert '"$spread' in js and "() => attrs()" in js
    assert '"$rest": () => ({"id": "b", "aria-label": "go", "class_": "big"})' in js
    assert "$spreadProps(" in js
    # An optional handler prop that wasn't passed does nothing when the event fires.
    assert "const f = onclick(); return f ? f(e) : undefined;" in js


def test_unknown_props_still_fail_without_rest():
    src = HEAD + 'def Card(title):\n    <h2>{title}</h2>\n\n\n@app.page("/")\ndef Home():\n    <Card title="a" id="x" />\n'
    with pytest.raises(CompileError, match="has no prop 'id'.*\\*\\*attrs"):
        compile_source(src)
    with pytest.raises(Exception, match="spread a dict"):
        compile_source(HEAD + '@app.page("/")\ndef Home():\n    <div {attrs}>x</div>\n')
    with pytest.raises(CompileError, match="only components take"):
        compile_source(HEAD + '@app.page("/")\ndef Home(**attrs):\n    <div>x</div>\n')


def test_markup_after_an_if_block_is_outside_it():
    src = HEAD + '''
def B(loading=False, children=None):
    <button>
        if loading:
            <span>spin</span>
        {children}
        after
    </button>


@app.page("/")
def Home():
    <main><B>x</B></main>
'''
    assert "<button>xafter</button>" in compile_source(src)["pages"]["Home"]["html_body"]


# ------------------------------------------------------------------ the theme

def test_every_preset_is_readable_in_light_and_dark():
    for name in T.PRESETS:
        th = T.Theme.preset(name)
        for dark in (False, True):
            v = th.variables(dark=dark)
            for scale_name in ("accent", "gray", "danger", "success", "warning", "info"):
                steps = [v[f"--pw-{scale_name}-{i}"] for i in range(1, 13)]
                assert T.contrast(steps[10], steps[1]) >= 4.5, (name, dark, scale_name, "step 11")
                assert T.contrast(steps[11], steps[0]) >= 4.5, (name, dark, scale_name, "step 12")
                assert T.contrast(v[f"--pw-{scale_name}-contrast"], steps[8]) >= 4.5, (name, dark, scale_name)
            # Backgrounds get darker (light) or lighter (dark) step by step.
            g = [T.luminance(T.hex_to_rgb(v[f"--pw-gray-{i}"])) for i in range(1, 13)]
            assert g == sorted(g, reverse=not dark)


def test_colour_maths():
    for c in ("#6366f1", "#000000", "#ffffff", "#e11d48", "#00ff00"):
        assert T.rgb_to_hex(T.oklch_to_rgb(*T.rgb_to_oklch(T.hex_to_rgb(c)))) == c
    assert round(T.contrast("#000000", "#ffffff"), 1) == 21.0
    assert T.on_solid("#111111") == "#ffffff" and T.on_solid("#fafafa") == "#111111"


def test_theme_settings():
    th = T.Theme(accent="#0ea5e9", radius=4, font="inter", density="compact", dark="auto")
    css = th.css()
    assert "--pw-radius:4px" in css and '"Inter"' in css and "--pw-space-6:13.6px" in css
    assert "@media (prefers-color-scheme:dark)" in css and ':root[data-theme="dark"]' in css
    assert "@media" not in T.Theme(dark="dark").css()
    assert "--pw-ink:" in css and "--pw-accent-ink:" in css            # the auth pages' variable names
    for bad in ({"accent": "blue"}, {"radius": 99}, {"density": "tight"}, {"dark": "night"}, {"font": "x;}body{"}):
        with pytest.raises(ValueError):
            T.Theme(**bad)
    with pytest.raises(ValueError, match="presets"):
        T.Theme.preset("neon")
    assert T.Theme.preset("rose", radius=0).radius == 0
    assert T.Theme().version() != th.version()


# ------------------------------------------------------------------ kit imports and serving

KIT_APP = '''
from pyweb import App
from pyweb.ui import Button, Card, CardHeader, CardTitle, CardContent, Badge, Theme
app = App(title="Kit", theme=Theme.preset("violet"))


@app.page("/")
def Home():
    n = 0

    def inc():
        n += 1
    <Card id="c" class_="wide">
        <CardHeader><CardTitle>Billing</CardTitle></CardHeader>
        <CardContent>
            <Badge tone="success">Active</Badge>
            <Button onclick={inc} aria-label="add">Clicked {n}</Button>
            <Button variant="outline" size="sm" href="/plans">Plans</Button>
            <Button loading={True}>Saving</Button>
        </CardContent>
    </Card>
'''


def test_kit_components_render_with_theme_and_styles():
    c = TestClient(source=KIT_APP)
    html = c.get("/").text
    assert re.search(r'<link rel="stylesheet" href="/__pyweb/theme\.css\?v=\w+"><link rel="stylesheet" '
                     r'href="/__pyweb/ui\.css\?v=\w+">', html)
    b = body(html)
    assert '<div data-part="card" class="pw-card wide" id="c">' in b
    assert ('<button data-part="button" data-variant="default" data-size="md" type="button" class="pw-button" '
            'aria-label="add">Clicked 0</button>') in b
    assert '<a data-part="button" data-variant="outline" data-size="sm" href="/plans" class="pw-button">Plans</a>' in b
    assert 'disabled aria-busy="true" class="pw-button"><span class="pw-spinner" aria-hidden="true"></span>Saving' in b
    assert '<span data-part="badge" data-variant="soft" data-tone="success" class="pw-badge">Active</span>' in b
    css = c.get("/__pyweb/ui.css")
    assert css.status == 200 and css.header("Content-Type").startswith("text/css") and b"@layer pyweb" in css.body
    theme = c.get("/__pyweb/theme.css?v=1")
    assert theme.header("Cache-Control").endswith("immutable") and b"--pw-accent-9:" in theme.body
    assert T.Theme.preset("violet").variables()["--pw-accent-9"].encode() in theme.body


def test_pages_without_the_kit_get_no_stylesheets():
    html = TestClient(source=HEAD + '@app.page("/")\ndef Home():\n    <p>x</p>\n').get("/").text
    assert "/__pyweb/ui.css" not in html and "/__pyweb/theme.css" not in html
    themed = TestClient(source='from pyweb import App\napp = App(theme="ocean")\n\n\n@app.page("/")\ndef Home():\n'
                               '    <p>x</p>\n').get("/").text
    assert "/__pyweb/theme.css" in themed and "/__pyweb/ui.css" not in themed


def test_kit_imports_work_without_a_file_and_from_python():
    out = compile_source(KIT_APP)                                       # no file on disk (playground, docs)
    assert "Button" in out["context"].components and "pw-button" in out["pages"]["Home"]["html_body"]
    from pyweb import ui
    assert repr(ui.Button) == "<pyweb.ui component Button>"
    with pytest.raises(TypeError, match="use it in markup"):
        ui.Button()
    with pytest.raises(AttributeError):
        ui.Nope  # noqa: B018
    with pytest.raises(TypeError):
        from pyweb import App
        App(theme=42)


def test_kit_styles_stay_small():
    from pyweb.ui import serve
    assert serve.gzip_size("ui.css") < 12 * 1024


def test_kit_files_follow_the_conventions():
    """Every kit component passes attributes through, marks its part, and uses only theme tokens."""
    from pyweb.compiler.pipeline import KIT_DIR, kit_index
    for name, path in kit_index().items():
        src = open(path, encoding="utf-8").read()
        fn = re.search(rf"^def {name}\((.*?)\):\n(.*?)(?=^def |\Z)", src, re.M | re.S)
        assert fn, name
        assert "**attrs" in fn.group(1) and "{**attrs}" in fn.group(2), f"{name} must take and spread **attrs"
        assert "data-part=" in fn.group(2), f"{name} must mark its data-part"
    css = open(os.path.join(os.path.dirname(KIT_DIR), "static", "ui.css"), encoding="utf-8").read()
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert not re.search(r"(?<![\w-])#[0-9a-fA-F]{3,6}\b", rules), "ui.css must use theme variables, not colours"


# ------------------------------------------------------------------ pyweb ui

def test_ui_cli(tmp_path, capsys):
    from pyweb.cli import main
    main(["ui", "list"])
    assert "Button, ButtonGroup" in capsys.readouterr().out
    main(["ui", "add", "Button", "card", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "from components.button import Button, ButtonGroup" in out
    assert (tmp_path / "components" / "button.pyweb").is_file() and (tmp_path / "components" / "card.pyweb").is_file()
    with pytest.raises(SystemExit, match="exists"):
        main(["ui", "add", "Button", "--dir", str(tmp_path)])
    main(["ui", "diff", "button", "--dir", str(tmp_path)])
    assert "no differences" in capsys.readouterr().out
    mine = tmp_path / "components" / "button.pyweb"
    mine.write_text(mine.read_text().replace('size="md"', 'size="lg"'))
    main(["ui", "diff", "Button", "--dir", str(tmp_path)])
    assert '+def Button(variant="default", size="lg"' in capsys.readouterr().out
    # The copy is a normal library: the app imports it from components/.
    app = tmp_path / "app.pyweb"
    app.write_text('from pyweb import App\nfrom components.button import Button\napp = App()\n\n\n@app.page("/")\n'
                   'def Home():\n    <Button>Go</Button>\n')
    assert 'data-size="lg"' in TestClient(str(app)).get("/").text
    with pytest.raises(SystemExit, match="no kit component"):
        main(["ui", "add", "Nope", "--dir", str(tmp_path)])


# ------------------------------------------------------------------ overlays, pickers, toasts (7.2)

OVERLAYS = '''
from pyweb import App, server
from pyweb.ui import (Dialog, DialogTrigger, DialogContent, DialogTitle, Popover, PopoverTrigger, PopoverContent,
                      Tooltip, DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, MenuItem, SelectMenu,
                      SelectOption, toast)
app = App()


@server
def save() -> bool:
    toast("Saved", "All good", tone="success")
    return True


@app.page("/")
def Home():
    <main>
        <Dialog open={False}><DialogTrigger>Open</DialogTrigger><DialogContent><DialogTitle>T</DialogTitle></DialogContent></Dialog>
        <Popover side="top"><PopoverTrigger>P</PopoverTrigger><PopoverContent>c</PopoverContent></Popover>
        <Tooltip text="Copy"><button>c</button></Tooltip>
        <DropdownMenu><DropdownMenuTrigger>M</DropdownMenuTrigger><DropdownMenuContent><MenuItem shortcut="K">One</MenuItem></DropdownMenuContent></DropdownMenu>
        <SelectMenu name="size" value="m"><SelectOption value="m">Medium</SelectOption></SelectMenu>
    </main>
'''


def test_overlays_render_closed_with_their_behaviour_marked():
    c = TestClient(source=OVERLAYS)
    html = c.get("/").text
    assert '<script type="module" src="/__pyweb/ui/index.js"></script>' in html
    b = body(html)
    # Every interactive root names its behaviour; panels start closed and hidden (fine without JS).
    for kind in ("dialog", "popover", "tooltip", "menu", "select"):
        assert f'data-pw-ui="{kind}"' in b
    assert 'data-open="false"' in b                                      # open={False}: controlled
    assert re.search(r'<dialog data-part="content" data-state="closed"[^>]*class="pw-dialog"', b)
    assert 'data-side="top"' in b
    assert re.search(r'<div data-part="content" data-state="closed" tabindex="-1" hidden[^>]*class="pw-popover"', b)
    assert '<span class="pw-menu-shortcut" data-part="shortcut">K</span>' in b
    assert '<input type="hidden" data-part="input" name="size" value="m">' in b
    js = c.get("/__pyweb/ui/dialog.js")
    assert js.status == 200 and js.header("Content-Type").startswith("text/javascript")
    assert c.request("GET", "/__pyweb/ui/dialog.js", headers={"If-None-Match": js.header("ETag")}).status == 304
    assert c.get("/__pyweb/ui/../ui/dialog.js").status == 404
    assert c.get("/__pyweb/ui/nope.js").status == 404


def test_toasts_from_the_server():
    from urllib.parse import unquote
    c = TestClient(source=OVERLAYS)
    r = c.post("/__pyweb/rpc/save", {"args": {}})
    assert r.status == 200
    assert json.loads(unquote(r.header("X-PyWeb-Toast"))) == [{"title": "Saved", "description": "All good",
                                                                "tone": "success"}]
    from pyweb import context
    from pyweb.runtime.server import Request
    from pyweb.ui import toast
    ctx = context.RequestContext(Request("GET", "/", {}, b""))
    token = context.activate(ctx)
    try:
        toast("Undo?", duration=0, action={"label": "Undo", "href": "/undo/1"})
        with pytest.raises(ValueError, match="tone"):
            toast("x", tone="loud")
        with pytest.raises(ValueError, match="href"):
            toast("x", action={"label": "Go", "href": "javascript:alert(1)"})
    finally:
        context.deactivate(token)
    assert ctx.toasts == [{"title": "Undo?", "duration": 0, "action": {"label": "Undo", "href": "/undo/1"}}]


def test_behaviour_modules_stay_small():
    import gzip
    from pyweb.ui import serve
    for name in os.listdir(serve.BEHAVIOUR):
        size = len(gzip.compress(open(os.path.join(serve.BEHAVIOUR, name), "rb").read(), 9))
        assert size <= (5 * 1024 if name == "core.js" else 3 * 1024), (name, size)


def test_wrapped_signatures_parse():
    src = HEAD + 'def X(a=None,\n      children=None, **attrs):\n    <div {**attrs}>{a}{children}</div>\n\n\n' \
          '@app.page("/")\ndef Home():\n    <X a="1" id="x">k</X>\n'
    assert '<div id="x">1k</div>' in compile_source(src)["pages"]["Home"]["html_body"]


# ------------------------------------------------------------------ 7.3: the rest of the kit

def test_math_in_browser_code():
    src = HEAD + ('import math\nfrom math import pi as PI, sqrt\n\n\n@app.page("/")\ndef Home():\n    a = 1.0\n\n'
                  '    def inc():\n        a += 1\n    <p onclick={inc}>{round(math.cos(a), 3)} {PI} {sqrt(a)} '
                  '{math.log(a, 2)} {math.inf}</p>\n')
    js = compile_source(src)["pages"]["Home"]["js"]
    assert "Math.cos(a())" in js and "$py.text(Math.PI)" in js and "Math.sqrt(a())" in js
    assert "Math.log(x) / Math.log(b)" in js and "Infinity" in js
    with pytest.raises(CompileError, match="math.factorial isn't available in browser code"):
        compile_source(src.replace("math.inf", "math.factorial(3)"))


def test_lambda_defaults_capture_loop_values():
    """`lambda n=n: go(n)` keeps n: an event handler's event doesn't replace it."""
    src = HEAD + ('@app.page("/")\ndef Home():\n    picked = 0\n\n    def go(n):\n        picked = n\n'
                  '    <div>\n        for n in [1, 2]:\n            <button onclick={lambda n=n: go(n)}>{n}</button>\n'
                  '        <button onclick={lambda e, k=2: go(k)}>x</button>\n    </div>\n')
    js = compile_source(src)["pages"]["Home"]["js"]
    assert '"onclick": ((n) => (() => (go(n))))(n)' in js
    assert '"onclick": ((e, k = 2) => (go(k)))' in js


def test_star_import_brings_in_the_whole_kit():
    src = 'from pyweb import App\nfrom pyweb.ui import *\napp = App()\n\n\n@app.page("/")\ndef Home():\n' \
          '    <Card><Badge>new</Badge><Kbd>K</Kbd></Card>\n'
    html = body(TestClient(source=src).get("/").text)
    assert 'class="pw-card"' in html and 'class="pw-badge"' in html
    js = compile_source(src)["pages"]["Home"]["js"]
    assert "function Card(" in js and "function Tabs(" not in js     # only what the page uses is shipped


KIT_PAGE = '''from pyweb import App
from pyweb.ui import *
app = App()


@app.page("/")
def Home():
    rows = [{"id": i, "name": "Task " + str(i), "points": (i * 7) % 11} for i in range(1, 24)]
    sales = [{"m": "Jan", "a": 12, "b": 5}, {"m": "Feb", "a": 19.5, "b": 8}]
    <main>
        <Tabs default="a">
            <TabsList><TabsTrigger value="a">A</TabsTrigger><TabsTrigger value="b">B</TabsTrigger></TabsList>
            <TabsContent value="a">Panel A</TabsContent>
            <TabsContent value="b">Panel B</TabsContent>
        </Tabs>
        <Accordion default="x">
            <AccordionItem value="x" open={True}><AccordionTrigger>X?</AccordionTrigger><AccordionContent>Yes</AccordionContent></AccordionItem>
        </Accordion>
        <Switch label="Emails" checked={True} />
        <RadioGroup name="plan" value="pro" options={[("free", "Free"), ("pro", "Pro")]} />
        <ToggleGroup type="multiple" default={["b", "i"]} name="fmt"><ToggleGroupItem value="b">B</ToggleGroupItem></ToggleGroup>
        <Slider value={25} min={0} max={200} />
        <InputOTP length={6} group={3} name="code" />
        <Breadcrumb><BreadcrumbItem><BreadcrumbLink href="/">Home</BreadcrumbLink></BreadcrumbItem>
            <BreadcrumbSeparator /><BreadcrumbItem><BreadcrumbPage>Now</BreadcrumbPage></BreadcrumbItem></Breadcrumb>
        <Pagination page={7} pages={20} href="?p=" />
        <Pagination page={2} pages={4} href="?q=" />
        <ResizablePanelGroup><ResizablePanel size={40}>L</ResizablePanel><ResizableHandle /><ResizablePanel size={60}>R</ResizablePanel></ResizablePanelGroup>
        <Calendar name="d" value="2026-10-23" />
        <DatePicker name="e" placeholder="When?" />
        <DataTable rows={rows} columns={["name", {"key": "points", "align": "end"}]} page_size={5} />
        <BarChart data={sales} x="m" y={["a", "b"]} label="Sales" />
        <PieChart data={sales} name="m" value="a" />
        <Sparkline values={[1, 3, 2]} />
    </main>
'''


@pytest.mark.parametrize("verify", ["0", "1"])
def test_the_rest_of_the_kit_renders_on_the_server(monkeypatch, verify):
    monkeypatch.setenv("PYWEB_SSR_VERIFY", verify)
    b = body(TestClient(source=KIT_PAGE).get("/").text)
    for kind in ("tabs", "accordion", "radio-group", "toggle-group", "slider", "otp", "resizable", "calendar",
                 "popover"):
        assert f'data-pw-ui="{kind}"' in b, kind
    assert 'role="tablist"' in b and b.count('role="tab"') == 2 and b.count('role="tabpanel"') == 2
    assert '<details data-part="item" data-value="x" open data-state="open"' in b
    assert 'role="switch"' in b and re.search(r'<input type="radio" class="pw-radio" name="plan" value="pro" checked>', b)
    assert 'data-value="b,i"' in b and '<input type="hidden" name="fmt" value="b,i">' in b
    assert 'style="--pw-fill: 12.5%"' in b
    assert b.count('data-part="slot"') == 6 and b.count('data-part="separator">–') == 1
    assert 'autocomplete="one-time-code"' in b
    assert '<span data-part="page" aria-current="page">Now</span>' in b
    pages = re.findall(r'data-part="page" href="\?p=(\d+)"', b)
    assert pages == ["1", "6", "7", "8", "20"] and b.count('data-part="ellipsis"') == 2
    assert re.findall(r'data-part="page" href="\?q=(\d+)"', b) == ["1", "2", "3", "4"]
    assert 'style="flex: 40 1 0px"' in b
    assert '<input class="pw-control" data-part="input" type="date" name="d" value="2026-10-23"' in b
    assert '<span data-part="value">When?</span>' in b
    assert b.count('<td data-part="cell" data-align="start">Task') == 5 and "1–5 of 23" in b and "Page 1 of 5" in b
    assert b.count('data-part="bar"') == 4 and 'aria-label="Sales"' in b
    assert re.findall(r'<span style="top: [\d.]+%">([\d.]+)</span>', b)[:5] == ["0", "5", "10", "15", "20"]
    assert '<table class="pw-sr-only"><caption>Sales</caption>' in b
    assert b.count('data-part="slice"') == 2 and "38%" in b and "62%" in b
    assert 'class="pw-sparkline"' in b and 'aria-label="Trend: 1, 3, 2"' in b


def test_kit_pages_load_only_the_behaviour_they_use():
    c = TestClient(source=KIT_PAGE)
    for name in ("tabs", "accordion", "collapsible", "checkbox", "toggle", "toggle-group", "radio-group", "slider",
                 "otp", "resizable", "sidebar", "navigation-menu", "calendar", "carousel"):
        r = c.get(f"/__pyweb/ui/{name}.js")
        assert r.status == 200, name
        assert "innerHTML" not in r.text and "insertAdjacentHTML" not in r.text, name
