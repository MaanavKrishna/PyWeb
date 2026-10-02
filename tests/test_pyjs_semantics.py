"""Differential test: translated JS must agree with CPython.

Each expression is evaluated by Python and by Node (translated with
pyweb.compiler.pyjs, executed with the real browser runtime's ``$py``).
"""

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from pyweb.compiler.errors import CompileError
from pyweb.compiler.pyjs import CONST, ModuleContext, Translator

RUNTIME = Path(__file__).parent.parent / "pyweb" / "runtime" / "browser" / "runtime.js"

ENV = {
    "xs": [3, 1, 2], "s": "Hello, World", "d": {"a": 1, "b": 2}, "n": 7, "f": 2.5,
    "empty": [], "words": ["pear", "Apple", "fig"], "rows": [{"n": "b", "v": 2}, {"n": "a", "v": 1}],
    "none": None, "neg": -7,
}

CASES = [
    "len(xs)", "len(s)", "len(d)", "xs[0]", "xs[-1]", "xs[1:]", "xs[::-1]", "s[:5]", "s[-5:]",
    "sorted(xs)", "sorted(xs, reverse=True)", "sorted(words, key=len)", "sorted(rows, key=lambda r: r['v'])",
    "min(xs)", "max(xs)", "max(words, key=len)", "sum(xs)", "sum([0.5, 0.25])",
    "n // 2", "neg // 2", "n % 3", "neg % 3", "n / 2", "2 ** 10", "n * 2 + 1",
    "'ab' * 3", "[0] * 3", "xs + [9]", "s + '!'",
    "s.upper()", "s.lower()", "s.split(', ')", "'  x '.strip()", "'-'.join(words)", "s.replace('l', 'L')",
    "s.startswith('Hell')", "s.endswith(('x', 'd'))", "s.find('W')", "s.count('l')", "'hello world'.title()",
    "'42'.isdigit()", "'7'.zfill(3)", "'a,b,,c'.split(',')", "'a b  c'.split()",
    "d.get('a')", "d.get('z', 0)", "list(d.keys())", "list(d.values())", "list(d.items())", "'a' in d", "'z' not in d",
    "2 in xs", "5 in xs", "'World' in s", "[1, 2] == [1, 2]", "{'a': 1} == {'a': 1}", "xs == [3, 1, 2]", "xs != xs[:]",
    "1 < n < 10", "n > 10 or n < 0", "not empty", "bool(empty)", "bool(xs)", "empty or 'fallback'", "xs and 'yes'",
    "none is None", "none or 5", "'yes' if xs else 'no'", "'yes' if empty else 'no'",
    "[x * 2 for x in xs]", "[x for x in xs if x > 1]", "{k: v * 10 for k, v in d.items()}",
    "[(i, w) for i, w in enumerate(words)]", "list(zip(xs, words))", "[a + b for a in [1, 2] for b in [10, 20]]",
    "list(range(5))", "list(range(2, 10, 3))", "list(range(5, 0, -2))", "any(x > 2 for x in xs)", "all(x > 0 for x in xs)",
    "int('12')", "int(3.9)", "int(-3.9)", "float('2.5')", "str(5)", "str(None)", "str(True)", "str([1, 'a'])",
    "abs(neg)", "round(2.5)", "round(3.5)", "round(2.675, 1)", "f'{n} items'", "f'{f:.2f}'", "f'{1234567:,}'",
    "f'{s!r}'", "f'{n:>4}'", "f'{0.256:.1%}'", "'%s=%d' % ('n', n)", "'{} and {}'.format(1, 'b')",
    "dict(a=1, b=2)", "isinstance(xs, list)", "isinstance(s, (int, str))", "isinstance(n, float)",
    "isinstance(f, float)", "isinstance(d, dict)", "isinstance(none, str)", "isinstance(True, int)", "list(reversed(xs))", "chr(65)", "ord('A')", "len({1, 2, 2})", "[w.lower() for w in words if 'p' in w.lower()]",
]


def translate(expr):
    ctx = ModuleContext("<test>")
    scope = ctx.module_scope().child({k: CONST for k in ENV})
    return Translator(ctx).expr(ast.parse(expr, mode="eval").body, scope)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_translated_expressions_match_python(tmp_path):
    js_lines = [f"import {{ py as $py }} from {json.dumps(RUNTIME.as_uri())};"]
    for k, v in ENV.items():
        js_lines.append(f"const {k} = {json.dumps(v)};")
    js_lines.append("const out = [];")
    for expr in CASES:
        js = translate(expr)
        js_lines.append(f"try {{ out.push({{ok: true, v: {js}}}); }} catch (e) {{ out.push({{ok: false, e: String(e)}}); }}")
    js_lines.append("console.log(JSON.stringify(out));")
    mod = tmp_path / "t.mjs"
    mod.write_text("\n".join(js_lines))
    res = subprocess.run(["node", str(mod)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    results = json.loads(res.stdout)
    mismatches = []
    for expr, got in zip(CASES, results):
        want = eval(expr, {}, dict(ENV))  # noqa: S307 - fixed test corpus
        want = json.loads(json.dumps(want if not isinstance(want, set) else sorted(want)))
        if not got["ok"] or got["v"] != want:
            mismatches.append((expr, want, got))
    assert not mismatches, "\n".join(f"{e}: python={w!r} js={g!r}" for e, w, g in mismatches)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_python_errors_surface_in_js(tmp_path):
    cases = {"xs[10]": "IndexError", "d['zz']": "KeyError", "int('x')": "ValueError",
             "s + 1": "TypeError", "n // 0": "ZeroDivisionError", "xs.index(99)": "ValueError"}
    lines = [f"import {{ py as $py }} from {json.dumps(RUNTIME.as_uri())};"]
    lines += [f"const {k} = {json.dumps(v)};" for k, v in ENV.items()]
    lines.append("const out = {};")
    for expr in cases:
        lines.append(f"try {{ {translate(expr)}; out[{json.dumps(expr)}] = null; }} "
                     f"catch (e) {{ out[{json.dumps(expr)}] = e.type || e.name; }}")
    lines.append("console.log(JSON.stringify(out));")
    mod = tmp_path / "e.mjs"
    mod.write_text("\n".join(lines))
    res = subprocess.run(["node", str(mod)], capture_output=True, text=True, timeout=60)
    assert json.loads(res.stdout) == cases


@pytest.mark.parametrize("src, msg", [
    ("open('x')", "not defined in browser code"),
    ("(y := 1)", "walrus"),
    ("[*xs] if xs else 1", None),
])
def test_unsupported_constructs_are_compile_errors(src, msg):
    if msg is None:
        translate(src)
        return
    with pytest.raises(CompileError) as e:
        translate(src)
    assert msg in str(e.value)
