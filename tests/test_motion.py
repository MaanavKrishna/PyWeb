"""Motion: the markup API (attributes → data-pw-motion and the server-rendered first pose), the
Motion settings, head tags, browser-code imports, and the maths (springs, tweens, inertia) in
Node. The engine itself in a browser: tests/e2e/test_motion_browser.py."""

import gzip
import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pyweb import motion
from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError
from pyweb.testing import TestClient

HEAD = "from pyweb import App\napp = App()\n\n\n"
BROWSER = Path(__file__).parent.parent / "pyweb" / "runtime" / "browser"


def body(text):
    return text.split('data-pw-root="Home">')[1].split("<script")[0]


def specs(text):
    return [json.loads(html.unescape(m)) for m in re.findall(r'data-pw-motion="([^"]*)"', text)]


PAGE = HEAD + '''@app.page("/")
def Home():
    items = [1, 2]

    def add():
        items = items + [3]
    <main>
        <button onclick={add} while_hover={{"scale": 1.05}} while-tap={{"scale": 0.95}}>add</button>
        for i in items:
            <li initial={{"opacity": 0, "y": 12}} animate={{"opacity": 1, "y": 0}} exit={{"x": -40}}
                style="color: red" layout>{i}</li>
        <p animate="fade-up">hi</p>
        <ul initial="hidden" animate="show" variants={{"hidden": {"opacity": 0, "scale": 0.9}, "show": {"opacity": 1}}}></ul>
        <div initial={False} animate={{"x": 10}}></div>
        <span animate="count">{len(items)}</span>
    </main>
'''


@pytest.mark.parametrize("verify", ["0", "1"])
def test_motion_attributes_render_on_the_server(monkeypatch, verify):
    monkeypatch.setenv("PYWEB_SSR_VERIFY", verify)
    page = TestClient(source=PAGE).get("/").text
    b = body(page)
    s = specs(b)
    assert s[0] == {"while_hover": {"scale": 1.05}, "while_tap": {"scale": 0.95}}
    assert s[1] == {"initial": {"opacity": 0, "y": 12}, "animate": {"opacity": 1, "y": 0}, "exit": {"x": -40},
                    "layout": True}
    # The first frame starts in the initial pose, after the element's own style.
    assert 'style="color: red; translate: 0px 12px; opacity: 0">1</li>' in b
    assert 'data-pw-motion="{&quot;animate&quot;:&quot;fade-up&quot;}" style="translate: 0px 16px; opacity: 0"' in b
    assert 'style="scale: 0.9; opacity: 0"></ul>' in b                     # a variant label
    assert re.search(r'x&quot;:10}}"></div>', b)                           # initial=False: no pose
    assert "<span data-pw-motion=" in b and ">2</span>" in b
    # The page loads the engine and its settings.
    assert '<script type="module" src="/static/motion.js"></script>' in page
    assert re.search(r"<meta name=\"pyweb-motion\" content='\{\"feel\":\"snappy\"", page)
    assert '<link rel="stylesheet" href="/__pyweb/motion.css' in page


def test_motion_attributes_in_browser_code():
    js = compile_source(PAGE)["pages"]["Home"]["js"]
    assert '"data-pw-motion": JSON.stringify({"while_hover": {"scale": 1.05}, "while_tap": {"scale": 0.95}})' in js
    # The browser applies the initial pose itself (before paint), so style stays the element's own.
    assert '"style": ((i, s) => s)({"opacity": 0, "y": 12}, "color: red", null, {"opacity": 1, "y": 0})' in js
    assert "motion-api.js" not in js


def test_pages_without_motion_load_nothing():
    page = TestClient(source=HEAD + '@app.page("/")\ndef Home():\n    <p>plain</p>\n').get("/").text
    assert "motion" not in page
    # An app with motion anywhere loads it on every page (rows added later animate too).
    src = HEAD + '@app.page("/")\ndef Home():\n    <p>plain</p>\n\n\n@app.page("/b")\ndef B():\n' \
                 '    <p animate="fade">b</p>\n'
    assert "/static/motion.js" in TestClient(source=src).get("/").text


def test_component_props_and_kit_components():
    src = HEAD.replace("app = App()", "from pyweb.ui import Card, Badge\napp = App()") + '''def Panel(layout="grid", children=None, **attrs):
    <section class={layout} {**attrs}>{children}</section>


@app.page("/")
def Home():
    n = 0

    def inc():
        n += 1
    <main>
        <Panel layout="wide" animate="fade" onclick={inc}>x</Panel>
        <Card animate={{"opacity": 1}} initial={{"opacity": 0}}>c</Card>
        <Badge motion={False}>b</Badge>
    </main>
'''
    b = body(TestClient(source=src).get("/").text)
    # A component's own `layout` parameter isn't taken for motion; unknown motion props are.
    assert '<section class="wide" data-pw-motion="{&quot;animate&quot;:&quot;fade&quot;}" style="opacity: 0">' in b
    assert re.search(r'class="pw-card" data-pw-motion="\{&quot;animate&quot;:\{&quot;opacity&quot;:1\},'
                     r'&quot;initial&quot;:\{&quot;opacity&quot;:0\}\}" style="opacity: 0"', b)
    assert 'data-motion="none"' in b
    js = compile_source(src)["pages"]["Home"]["js"]
    assert '"data-pw-motion": JSON.stringify({"animate": "fade"})' in js
    assert '"data-motion": ((m) => (m === false || m === "none" ? "none" : null))(false)' in js


def test_motion_settings():
    from pyweb import App
    assert App().motion is None
    app = App(motion=motion.Motion(feel="gentle", speed=2, reduced="always"))
    assert app.motion.config()["feel"] == "gentle"
    assert "--pw-duration: 120ms" in app.motion.css_vars()
    assert App(motion=False).motion.feel == "none"
    assert App(motion="bouncy").motion.feel == "bouncy"
    assert "--pw-duration: 0ms" in motion.Motion(feel="none").css_vars()
    for bad in ({"feel": "wobbly"}, {"speed": 0}, {"speed": True}, {"reduced": "sometimes"}):
        with pytest.raises(ValueError):
            motion.Motion(**bad)
    with pytest.raises(TypeError):
        App(motion=3)
    src = "from pyweb import App\nfrom pyweb.motion import Motion\napp = App(motion=Motion(speed=0.5))\n\n\n" \
          '@app.page("/")\ndef Home():\n    <p animate="fade">x</p>\n'
    c = TestClient(source=src)
    page = c.get("/").text
    assert '"speed":0.5' in page
    css = c.get("/__pyweb/motion.css")
    assert css.status == 200 and "@media (scripting:none)" in css.text


def test_presets():
    try:
        motion.preset("hero-in", {"initial": {"opacity": 0, "y": 40}, "animate": {"opacity": 1, "y": 0}})
        assert motion.Motion().config()["presets"]["hero-in"]["initial"] == {"opacity": 0, "y": 40}
        assert motion.initial_style("hero-in") == {"translate": "0px 40px", "opacity": "0"}
        with pytest.raises(ValueError):
            motion.preset("bad name", {})
        with pytest.raises(ValueError):
            motion.preset("x", {"colour": 1})
    finally:
        motion._PRESETS.clear()
    assert motion.initial_style({"x": "100%", "rotate": 45, "blur": 4, "scale": 0.5}) == {
        "translate": "100% 0px", "scale": "0.5", "rotate": "45deg", "filter": "blur(4px)"}
    assert motion.initial_style({"opacity": 0}, {"color": "red"}) == {"color": "red", "opacity": "0"}


def test_browser_code_api():
    src = HEAD + '''from pyweb.motion import animate, stagger, scroll_progress


@app.page("/")
def Home():
    def go(e):
        animate(".row", {"x": [0, 10, 0]}, {"delay": stagger(0.05)})
    <button onclick={go}>go</button>
'''
    js = compile_source(src)["pages"]["Home"]["js"]
    assert 'import * as $motion from "./motion-api.js";' in js
    assert '$motion.animate(".row", {"x": [0, 10, 0]}, {"delay": $motion.stagger(0.05)})' in js
    assert TestClient(source=src).get("/").status == 200        # server stand-ins import fine
    with pytest.raises(CompileError, match="only exists on the server"):
        compile_source(src.replace("from pyweb.motion import animate,", "from pyweb.motion import Motion, animate,")
                       .replace("animate(\".row\"", "Motion(\".row\""))


def test_engine_files_are_served_and_small():
    from pyweb.build import minify_js
    from pyweb.hosting import MOTION_FILES
    sizes = {}
    for name in MOTION_FILES:
        src = (BROWSER / name).read_text()
        assert "innerHTML" not in src and "eval(" not in src, name
        sizes[name] = len(gzip.compress(minify_js(src).encode(), 9))
    assert sizes["motion.js"] + sizes["motion-math.js"] <= 8 * 1024, sizes          # the core
    assert all(v <= 4 * 1024 for k, v in sizes.items() if k != "motion.js"), sizes
    c = TestClient(source=PAGE)
    for name in MOTION_FILES:
        r = c.get(f"/static/{name}")
        assert r.status == 200 and r.header("Content-Type").startswith("text/javascript"), name


def test_build_ships_the_engine(tmp_path, monkeypatch, capsys):
    from pyweb.cli import main
    app = tmp_path / "app.pyweb"
    app.write_text(PAGE)
    main(["build", str(app), "--out", str(tmp_path / "dist"), "--production"])
    static = tmp_path / "dist" / "static"
    files = os.listdir(static)
    assert "motion.js" in files and "motion-layout.js" in files
    rt = next(f for f in files if f.startswith("runtime.") and f.endswith(".js"))
    api = (static / "motion-api.js").read_text()
    assert f'"./{rt}"' in api and '"./runtime.js"' not in api


# ------------------------------------------------------------------ the maths, in Node

node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(js, tmp_path):
    mod = tmp_path / "t.mjs"
    mod.write_text(f"import * as M from {json.dumps((BROWSER / 'motion-math.js').as_uri())};\nconst out = {{}};\n"
                   f"{js}\nconsole.log(JSON.stringify(out));")
    res = subprocess.run(["node", str(mod)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@node
def test_springs(tmp_path):
    out = run("""
      const under = M.spring({ from: 0, to: 100, stiffness: 500, damping: 20 });
      const crit = M.spring({ from: 0, to: 100, stiffness: 100, damping: 20 });
      const over = M.spring({ from: 0, to: 100, stiffness: 100, damping: 60 });
      out.under = Math.max(...M.sample(under)); out.crit = Math.max(...M.sample(crit));
      out.over = Math.max(...M.sample(over));
      out.ends = [under.value(under.duration), crit.value(crit.duration), over.value(10)];
      out.durations = [under.duration, crit.duration, over.duration];
      out.start = [under.value(0), under.velocity(0)];
      // Interrupted at 0.08 s and sent back: the next spring starts at the same place and speed.
      const t = 0.08, v = under.value(t), vel = under.velocity(t);
      const next = M.spring({ from: v, to: 0, velocity: vel, stiffness: 500, damping: 20 });
      out.handoff = [v, next.value(1e-9), vel, next.velocity(0)];
      const d = M.springFor(0.5, 0);
      out.noBounce = Math.max(...M.sample(M.spring({ from: 0, to: 1, ...d })));
    """, tmp_path)
    assert 100 < out["under"] < 130                                   # it overshoots
    assert out["crit"] <= 100.0001 and out["over"] <= 100.0001         # these don't
    assert out["ends"] == [100, 100, 100]
    assert all(0.1 < d < 5 for d in out["durations"]) and out["durations"][2] > out["durations"][1]   # overdamped: slow
    assert out["start"] == [0, 0]
    v, v2, vel, vel2 = out["handoff"]
    assert abs(v - v2) < 1e-4 and abs(vel - vel2) / abs(vel) < 0.01
    assert out["noBounce"] <= 1.0001


@node
def test_tweens_inertia_and_stagger(tmp_path):
    out = run("""
      const t = M.tween({ values: [0, 10], duration: 1, ease: "linear" });
      out.lin = [t.value(0), t.value(0.5), t.value(1), t.velocity(0.5)];
      const k = M.tween({ values: [0, 10, 0], duration: 1, ease: "linear" });
      out.keys = [k.value(0.25), k.value(0.5), k.value(0.75)];
      const e = M.easing("ease-in-out");
      out.ease = [e(0), e(0.5), e(1)];
      const i = M.inertia({ from: 0, velocity: 1000, power: 0.8, timeConstant: 0.35 });
      out.coast = i.value(i.duration);
      const b = M.inertia({ from: 0, velocity: 1000, max: 100 });
      out.bounded = b.value(b.duration);
      const s = M.stagger(0.1, { from: "center" });
      out.stagger = [0, 1, 2, 3, 4].map((n) => Math.round(s(n, 5) * 100) / 100);
    """, tmp_path)
    assert out["lin"][:3] == [0, 5, 10] and abs(out["lin"][3] - 10) < 0.01
    assert [round(v, 6) for v in out["keys"]] == [5, 10, 5]
    assert out["ease"][0] == 0 and abs(out["ease"][1] - 0.5) < 1e-3 and out["ease"][2] == 1
    assert abs(out["coast"] - 280) < 1                                 # 0.8 × 1000 × 0.35
    assert out["bounded"] == 100                                        # springs to the edge
    assert out["stagger"] == [0.2, 0.1, 0, 0.1, 0.2]
