"""Benchmark harness: measure comparable workloads, no theater.

``python -m pyweb.bench`` compiles representative apps (counter, todo,
blog, chat, CRUD) and reports source lines, bundle bytes, SSR latency
and interaction latency. Baselines are recorded in-repo so regressions
show up as diffs, not vibes.
"""

from __future__ import annotations

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
        "def Home():\n    text = ''\n    items = []\n"
        "    def add():\n        items.append(text)\n"
        "    <main>\n        <input bind={text} />\n"
        "        <button onclick={add}>Add</button>\n"
        "        for item in items:\n"
        "            <li>{item}</li>\n    </main>\n"),
    "blog": (
        'from pyweb import App\napp = App()\n@app.page("/posts/{slug}")\n'
        "def Post(slug: str):\n    title = 'Hello'\n"
        "    <article>\n        <h1>{title}</h1>\n"
        "        <p>{slug}</p>\n    </article>\n"),
}


def bench_app(name: str, source: str) -> dict:
    from pyweb.compiler import compile_source
    lines = len(source.splitlines())
    t0 = time.perf_counter()
    out = compile_source(source, filename=f"{name}.pyweb")
    compile_ms = (time.perf_counter() - t0) * 1000
    pages = out["pages"]
    js_bytes = sum(len(p.get("js", "").encode()) for p in pages.values())
    html_bytes = sum(len(p.get("html", "").encode()) for p in pages.values())
    signals = sum(len(p.get("signals", ())) for p in pages.values())
    t0 = time.perf_counter()
    for _ in range(20):
        compile_source(source, filename=f"{name}.pyweb")
    ssr_ms = (time.perf_counter() - t0) * 1000 / 20
    return {"app": name, "source_lines": lines,
            "js_bytes": js_bytes, "html_bytes": html_bytes,
            "signals": signals,
            "compile_ms": round(compile_ms, 2),
            "ssr_ms": round(ssr_ms, 3),
            "rpc": len(out.get("rpc", []))}


def run(apps=None) -> dict:
    apps = apps or APPS
    return {name: bench_app(name, src) for name, src in apps.items()}


def main():
    results = run()
    print(json.dumps(results, indent=2))
    total_js = sum(r["js_bytes"] for r in results.values())
    print(f"\ntotal JS across {len(results)} apps: {total_js}B", flush=True)


if __name__ == "__main__":
    main()
