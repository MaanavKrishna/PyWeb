"""Reactive core of the browser runtime (Node, no DOM needed)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RUNTIME = (Path(__file__).parent.parent / "pyweb" / "runtime" / "browser" / "runtime.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def run(js, tmp_path):
    mod = tmp_path / "t.mjs"
    mod.write_text(f"import * as R from {json.dumps(RUNTIME)};\nconst out = {{}};\n{js}\n"
                   "console.log(JSON.stringify(out));")
    res = subprocess.run(["node", str(mod)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_effect_reruns_only_for_its_own_signals(tmp_path):
    out = run("""
      const a = R.signal(1), b = R.signal(1);
      let ra = 0, rb = 0;
      R.effect(() => { a(); ra++; });
      R.effect(() => { b(); rb++; });
      a(2); a(3); b(2);
      out.ra = ra; out.rb = rb;
    """, tmp_path)
    assert out == {"ra": 3, "rb": 2}


def test_same_value_write_does_not_notify(tmp_path):
    out = run("""
      const a = R.signal(1); let n = 0;
      R.effect(() => { a(); n++; });
      a(1); a(1);
      out.n = n;
    """, tmp_path)
    assert out == {"n": 1}


def test_computed_is_lazy_and_cached(tmp_path):
    out = run("""
      const a = R.signal(2); let calls = 0;
      const c = R.computed(() => { calls++; return a() * 10; });
      out.before = calls;
      out.v1 = c(); out.v2 = c();
      out.mid = calls;
      a(3);
      out.v3 = c();
      out.after = calls;
    """, tmp_path)
    assert out == {"before": 0, "v1": 20, "v2": 20, "mid": 1, "v3": 30, "after": 2}


def test_batch_coalesces_updates(tmp_path):
    out = run("""
      const a = R.signal(0), b = R.signal(0); let n = 0, seen = [];
      R.effect(() => { seen.push(a() + b()); n++; });
      R.batch(() => { a(1); b(2); a(3); });
      out.n = n; out.seen = seen;
    """, tmp_path)
    assert out == {"n": 2, "seen": [0, 5]}


def test_disposed_effects_stop_running(tmp_path):
    out = run("""
      const a = R.signal(0); let outer = 0, inner = 0;
      const stop = R.effect(() => {
        outer++;
        R.effect(() => { a(); inner++; });
      });
      a(1);
      stop();
      a(2);
      out.outer = outer; out.inner = inner;
    """, tmp_path)
    assert out == {"outer": 1, "inner": 2}


def test_dynamic_dependencies_are_retracked(tmp_path):
    out = run("""
      const flag = R.signal(true), x = R.signal(1), y = R.signal(1); let n = 0;
      R.effect(() => { n++; flag() ? x() : y(); });
      y(2);            // not a dependency yet
      flag(false);     // now depends on y, not x
      x(5);            // ignored
      y(3);
      out.n = n;
    """, tmp_path)
    assert out == {"n": 3}


def test_copy_on_write_state_mutation(tmp_path):
    out = run("""
      const todos = R.signal([{t: "a", done: false}, {t: "b", done: false}]);
      const before = todos.peek(); const first = before[0], second = before[1];
      let runs = 0; R.effect(() => { todos(); runs++; });
      R.py.setp(todos, [0, "done"], true);
      const after = todos.peek();
      out.newList = after !== before;
      out.newItem = after[0] !== first;
      out.sharedItem = after[1] === second;
      out.oldUntouched = before[0].done === false;
      R.py.mut(todos, [], (v) => R.py.m(v, "append", {t: "c", done: false}));
      out.len = todos.peek().length; out.runs = runs;
    """, tmp_path)
    assert out == {"newList": True, "newItem": True, "sharedItem": True, "oldUntouched": True,
                   "len": 3, "runs": 3}
