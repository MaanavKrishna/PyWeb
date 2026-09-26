"""Track A: keyed live lists + conditional blocks keep/fine-grained updates.

Runs runtime.js in node with a fake DOM (see fakedom.js) via _node_harness.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _node_harness import extract_app_inner, run_js
from pyweb.compiler.pipeline import build_text

REPO = Path(__file__).parent.parent


def _app_with_markers(markers: str) -> str:
    return """
var app = document.getElementById("app");
loadSSR(app, %s);
""" % json.dumps(markers)


def test_live_list_append_keeps_existing_rows():
    case = _app_with_markers(
        '<ul><!--pw:1--><li>a</li><li>b</li><!--/pw:1--></ul>'
    ) + """
var s = PyWeb.signal(["a", "b"]);
var anchor = PyWeb.takeover(app, 1);
var renders = 0;
function row(item) {
  renders++;
  var li = document.createElement("li");
  var t = document.createTextNode("");
  li.appendChild(t);
  PyWeb.dynText(t, () => item);
  return { els: [li] };
}
PyWeb.liveList(anchor, () => s.get(), (it) => row(it), (x) => x);
function texts() {
  return app.querySelectorAll("li").map(function (n) { return n.textContent; });
}
var before = app.querySelectorAll("li");
s.set(["a", "b", "c"]);
var after = app.querySelectorAll("li");
console.log(JSON.stringify({
  texts: texts(), renders: renders,
  stable0: after[0] === before[0], stable1: after[1] === before[1],
}));
"""
    r = run_js(case)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip())
    assert got["texts"] == ["a", "b", "c"], got
    assert got["renders"] == 3, got  # only the new row rendered
    assert got["stable0"] and got["stable1"], got


def test_live_list_remove_middle_row():
    case = _app_with_markers(
        '<ul><!--pw:1--><li>a</li><li>b</li><li>c</li><!--/pw:1--></ul>'
    ) + """
var s = PyWeb.signal(["a", "b", "c"]);
var anchor = PyWeb.takeover(app, 1);
function row(item) {
  var li = document.createElement("li");
  li.appendChild(document.createTextNode(item));
  return { els: [li] };
}
PyWeb.liveList(anchor, () => s.get(), (it) => row(it), (x) => x);
var before = app.querySelectorAll("li");
s.set(["a", "c"]);
var after = app.querySelectorAll("li");
console.log(JSON.stringify({
  texts: after.map(function (n) { return n.textContent; }),
  keep0: after[0] === before[0], keep1: after[1] === before[2],
}));
"""
    r = run_js(case)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip())
    assert got["texts"] == ["a", "c"], got
    assert got["keep0"] and got["keep1"], got


def test_live_list_keyed_reorder_moves_nodes():
    case = _app_with_markers('<!--pw:2--><p>1</p><p>2</p><!--/pw:2-->') + """
var s = PyWeb.signal([{id: 1}, {id: 2}]);
var anchor = PyWeb.takeover(app, 2);
function row(item) {
  var p = document.createElement("p");
  p.appendChild(document.createTextNode(String(item.id)));
  return { els: [p] };
}
PyWeb.liveList(anchor, () => s.get(), (it) => row(it), (x) => x.id);
var before = app.querySelectorAll("p");
var cur = s.get();
s.set([cur[1], cur[0]]);
var after = app.querySelectorAll("p");
console.log(JSON.stringify({
  texts: after.map(function (n) { return n.textContent; }),
  moved0: after[0] === before[1], moved1: after[1] === before[0],
}));
"""
    r = run_js(case)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip())
    assert got["texts"] == ["2", "1"], got
    assert got["moved0"] and got["moved1"], got


def test_live_if_toggle_swaps_branches():
    case = _app_with_markers('<!--pw:3--><p>off</p><!--/pw:3-->') + """
var s = PyWeb.signal(false);
var anchor = PyWeb.takeover(app, 3);
var created = [];
function on() {
  created.push("on");
  var p = document.createElement("p");
  p.appendChild(document.createTextNode("on"));
  return [p];
}
function off() {
  created.push("off");
  var p = document.createElement("p");
  p.appendChild(document.createTextNode("off"));
  return [p];
}
PyWeb.liveIf(anchor, () => (s.get() ? 0 : 1), [on, off]);
var t0 = app.textContent;
s.set(true);
var t1 = app.textContent;
s.set(false);
var t2 = app.textContent;
console.log(JSON.stringify({ t0: t0, t1: t1, t2: t2, created: created }));
"""
    r = run_js(case)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip())
    assert got["t0"] == "off", got
    assert got["t1"] == "on", got
    assert got["t2"] == "off", got
    assert got["created"] == ["off", "on", "off"], got


def _e2e(source: str, signal_names: list[str], probe: str, route: str = "/") -> dict:
    art = build_text(source, filename="t.pyweb", route=route)
    inner = extract_app_inner(art.html)
    hook = "\n;globalThis.__hook = { %s };\n" % ", ".join(signal_names)
    case = (
        "loadSSR(document.getElementById(\"app\"), %s);\n" % json.dumps(inner)
        + art.js
        + hook
        + "__fireReady();\n"
        + probe
    )
    r = run_js(case)
    assert r.returncode == 0, r.stderr[-3000:] + "\n---JS---\n" + art.js[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_e2e_generated_list_append_remove():
    source = """from pyweb import signal

@page("/")
def shop():
    items = signal(["a", "b"])

    <ul>
        for item in items:
            <li key={item}>{item}</li>
    </ul>
"""
    probe = """
function lis() {
  return document.getElementById("app").querySelectorAll("li");
}
function texts(ns) { return ns.map(function (n) { return n.textContent; }); }
var r0 = lis();
__hook.items.set(["a", "b", "c"]);
var r1 = lis();
__hook.items.set(["a", "c"]);
var r2 = lis();
console.log(JSON.stringify({
  start: texts(r0), grown: texts(r1), shrunk: texts(r2),
  stable0: r1[0] === r0[0], stable1: r1[1] === r0[1],
  keep0: r2[0] === r0[0], keep1: r2[1] === r1[2],
}));
"""
    got = _e2e(source, ["items"], probe)
    assert got["start"] == ["a", "b"], got
    assert got["grown"] == ["a", "b", "c"], got
    assert got["shrunk"] == ["a", "c"], got
    assert got["stable0"] and got["stable1"], got
    assert got["keep0"] and got["keep1"], got


def test_e2e_generated_conditional_toggle():
    source = """from pyweb import signal

@page("/")
def togg():
    show = signal(False)

    if show:
        <p>visible</p>
    else:
        <p>hidden</p>
"""
    probe = """
var app = document.getElementById("app");
var t0 = app.textContent;
__hook.show.set(true);
var t1 = app.textContent;
__hook.show.set(false);
var t2 = app.textContent;
console.log(JSON.stringify({ t0: t0, t1: t1, t2: t2 }));
"""
    got = _e2e(source, ["show"], probe)
    assert got["t0"] == "hidden", got
    assert got["t1"] == "visible", got
    assert got["t2"] == "hidden", got
