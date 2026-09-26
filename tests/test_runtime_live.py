"""Browser runtime live regions: keyed liveList + liveIf (node harness)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

RUNTIME = Path(__file__).parent.parent / "pyweb" / "runtime" / "browser" / "runtime.js"

FAKEDOM = """
// Minimal DOM for live-region tests: elements, comments, text nodes.
function _mkNode(kind) {
  return { nodeType: kind, childNodes: [], parentNode: null,
    nextSibling: null, previousSibling: null,
    appendChild(c) { return this.insertBefore(c, null); },
    insertBefore(c, ref) {
      if (c.parentNode) c.parentNode.removeChild(c);
      const i = ref ? this.childNodes.indexOf(ref) : -1;
      if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
      c.parentNode = this; _link(this); return c;
    },
    removeChild(c) {
      const i = this.childNodes.indexOf(c);
      if (i >= 0) this.childNodes.splice(i, 1);
      c.parentNode = null; _link(this); return c;
    } };
}
function _link(p) {
  for (let i = 0; i < p.childNodes.length; i++) {
    p.childNodes[i].previousSibling = p.childNodes[i - 1] || null;
    p.childNodes[i].nextSibling = p.childNodes[i + 1] || null;
  }
}
function makeDocument() {
  const doc = { _all: [],
    createElement(tag) { const e = _mkNode(1); e.tagName = tag.toUpperCase();
      e.textContent = ""; doc._all.push(e); return e; },
    createTextNode(t) { const e = _mkNode(3); e.textContent = t; return e; },
    createComment(t) { const e = _mkNode(8); e.nodeValue = t; return e; } };
  return doc;
}
"""

CASE_LIST = """
const RT = await import(%s);
const { sig, effect, liveList } = RT;
const document = makeDocument();
const app = document.createElement("div");
const anchor = document.createComment("pw-live");
app.appendChild(anchor);
const s = sig(["a", "b"]);
let renders = 0;
function row(item) {
  renders++;
  const li = document.createElement("li");
  li.textContent = item;
  return { els: [li] };
}
liveList(anchor, () => s(), (it) => row(it), (x) => x);
const before = app.childNodes.filter((n) => n.tagName === "LI");
s(["a", "b", "c"]);
const after = app.childNodes.filter((n) => n.tagName === "LI");
console.log(JSON.stringify({
  texts: after.map((n) => n.textContent), renders,
  stable0: after[0] === before[0], stable1: after[1] === before[1] }));
"""

CASE_IF = """
const RT = await import(%s);
const { sig, liveIf } = RT;
const document = makeDocument();
const app = document.createElement("div");
const anchor = document.createComment("pw-live");
app.appendChild(anchor);
const flag = sig(true);
function mk(label) { const b = document.createElement("b"); b.textContent = label;
  return [b]; }
liveIf(anchor, () => (flag() ? 0 : 1), [() => mk("yes"), () => mk("no")]);
const first = app.childNodes.filter((n) => n.tagName === "B").map((n) => n.textContent);
flag(false);
const second = app.childNodes.filter((n) => n.tagName === "B").map((n) => n.textContent);
console.log(JSON.stringify({ first, second }));
"""


def _run(case: str) -> dict:
    url = RUNTIME.as_uri()
    prog = FAKEDOM + case % json.dumps(url)
    r = subprocess.run(["node", "--input-type=module", "-e", prog],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip())


def test_live_list_append_keeps_existing_rows():
    got = _run(CASE_LIST)
    assert got["texts"] == ["a", "b", "c"], got
    assert got["renders"] == 3, got
    assert got["stable0"] and got["stable1"], got


def test_live_if_toggle_swaps_branches():
    got = _run(CASE_IF)
    assert got["first"] == ["yes"], got
    assert got["second"] == ["no"], got
