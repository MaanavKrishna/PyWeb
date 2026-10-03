"""Benchmark harness: measure what actually ships and how fast it renders.

``python -m pyweb.bench`` compiles small reference apps and reports, per
app: source lines, compile time, shipped JavaScript (minified and gzip,
including the shared runtime only when the app ships any JS), HTML size
and real per-request server render time. No theater: numbers come from
the same code paths ``pyweb build`` and ``pyweb serve`` use.
"""

from __future__ import annotations

import gzip
import json
import time

APPS = {
    "counter": (
        'from pyweb import App\napp = App()\n@app.page("/")\n'
        "def Home():\n    count = 0\n"
        "    def increment():\n        count += 1\n"
        "    <main>\n        <button onclick={increment}>\n"
        "            Count: {count}\n        </button>\n    </main>\n"),
    "todo": (
        'from pyweb import App\napp = App()\n@app.page("/")\n'
        "def Home():\n    text = ''\n    items = []\n    left = len(items)\n"
        "    def add():\n        if text.strip():\n            items.append(text.strip())\n        text = ''\n"
        "    def remove(item):\n        items.remove(item)\n"
        "    <main>\n        <input bind={text} />\n"
        "        <button onclick={add}>Add</button>\n        <p>{left} left</p>\n"
        "        <ul>\n            for item in items:\n"
        "                <li>{item} <button onclick={remove(item)}>x</button></li>\n        </ul>\n    </main>\n"),
    "blog": (
        'from pyweb import App\napp = App()\n@app.page("/posts/{slug}")\n'
        "def Post(slug: str):\n    title = slug.replace('-', ' ').title()\n"
        "    <article>\n        <h1>{title}</h1>\n"
        "        <p>Static page: no JavaScript is shipped.</p>\n    </article>\n"),
}


def _gz(data: str) -> int:
    return len(gzip.compress(data.encode(), 9))


def runtime_sizes() -> dict:
    """Minified and gzip size of the shared browser runtime."""
    import os
    from pyweb.build import minify_js
    path = os.path.join(os.path.dirname(__file__), "runtime", "browser", "runtime.js")
    with open(path, encoding="utf-8") as fh:
        js = minify_js(fh.read())
    return {"bytes": len(js.encode()), "gzip_bytes": _gz(js)}


def bench_app(name: str, source: str, *, repeat: int = 50) -> dict:
    from pyweb.app_loader import LoadedApp
    from pyweb.build import minify_js
    from pyweb.compiler import compile_source
    t0 = time.perf_counter()
    out = compile_source(source, filename=f"{name}.pyweb")
    compile_ms = (time.perf_counter() - t0) * 1000
    pages = out["pages"]
    page_js = [minify_js(p["js"]) for p in pages.values() if p.get("js")]
    rt = runtime_sizes() if page_js else {"bytes": 0, "gzip_bytes": 0}
    app = LoadedApp(source=source, filename=f"{name}.pyweb")
    page_name, page = next(iter(pages.items()))
    params = {p: "hello-world" for p in page["params"]}
    html = app.render(page_name, params)
    t0 = time.perf_counter()
    for _ in range(repeat):
        app.render(page_name, params)
    ssr_ms = (time.perf_counter() - t0) * 1000 / repeat
    page_bytes = sum(len(j.encode()) for j in page_js)
    page_gz = sum(_gz(j) for j in page_js)
    return {"app": name, "source_lines": len(source.splitlines()),
            "compile_ms": round(compile_ms, 2),
            "page_js_bytes": page_bytes, "page_js_gzip": page_gz,
            "runtime_js_bytes": rt["bytes"], "runtime_js_gzip": rt["gzip_bytes"],
            "js_bytes": page_bytes + rt["bytes"], "js_gzip": page_gz + rt["gzip_bytes"],
            "html_bytes": len(html.encode()),
            "signals": sum(len(p.get("signals", ())) for p in pages.values()),
            "ssr_ms": round(ssr_ms, 3),
            "rpc": len(out.get("rpc", []))}


def run(apps=None) -> dict:
    apps = apps or APPS
    return {name: bench_app(name, src) for name, src in apps.items()}


def over_budget(results, *, max_ssr_ms=None, max_compile_ms=None) -> list:
    """Lines describing every app that exceeds a budget."""
    out = []
    for r in results.values():
        if max_ssr_ms is not None and r["ssr_ms"] > max_ssr_ms:
            out.append(f"{r['app']}: server render {r['ssr_ms']} ms > {max_ssr_ms} ms")
        if max_compile_ms is not None and r["compile_ms"] > max_compile_ms:
            out.append(f"{r['app']}: compile {r['compile_ms']} ms > {max_compile_ms} ms")
    return out


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="python -m pyweb.bench", description="Measure compile time, "
                                 "shipped JavaScript and server render time.")
    ap.add_argument("--json", action="store_true", help="print only JSON")
    ap.add_argument("--max-ssr-ms", type=float, help="fail if any server render is slower")
    ap.add_argument("--max-compile-ms", type=float, help="fail if any compile is slower")
    args = ap.parse_args(argv)
    results = run()
    print(json.dumps(results, indent=2))
    if not args.json:
        rt = runtime_sizes()
        print(f"\nshared runtime: {rt['bytes']} B minified, {rt['gzip_bytes']} B gzip "
              "(downloaded once, cached across pages)")
        for r in results.values():
            print(f"{r['app']:>8}: page JS {r['page_js_gzip']} B gzip, server render {r['ssr_ms']} ms")
    failed = over_budget(results, max_ssr_ms=args.max_ssr_ms, max_compile_ms=args.max_compile_ms)
    for line in failed:
        print("over budget:", line, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
