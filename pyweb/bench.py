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


# ------------------------------------------------------------- whole system
#
# The numbers above are per page. These measure the system: requests per second
# over real sockets, a signed-in page with a database, start-up time and live
# query hashing. Absolute numbers depend on the machine, so CI compares two
# commits on the same machine (``--compare``) instead of against stored numbers.

# metric: (higher is better, smallest change that counts as a regression)
SYSTEM = {
    "network_rps": (True, 100.0),
    "saas_page_us": (False, 60.0),
    "import_ms": (False, 5.0),
    "cli_version_ms": (False, 8.0),
    "live_10k_ms": (False, 2.0),
}


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


_LOAD = """
import asyncio, sys, time
async def one(port, path, end, n):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    req = ("GET " + path + " HTTP/1.1\\r\\nHost: bench\\r\\n\\r\\n").encode()
    while time.monotonic() < end:
        w.write(req)
        head = await r.readuntil(b"\\r\\n\\r\\n")
        size = next(int(h.split(b":")[1]) for h in head.split(b"\\r\\n") if h.lower().startswith(b"content-length"))
        await r.readexactly(size)
        n[0] += 1
    w.close()
async def main(port, conns, secs):
    n = [0]
    end = time.monotonic() + secs
    await asyncio.gather(*(one(port, "/", end, n) for _ in range(conns)))
    print(n[0] / secs)
asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])))
"""


def network_rps(seconds=2.0, connections=32):
    """Requests per second for the todo example over keep-alive sockets (the client is another process)."""
    import os
    import subprocess
    import sys
    from pyweb.hosting import Site
    from pyweb.net.server import Running
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = os.path.join(root, "bench", "apps", "todo.pyweb")      # a fixed copy, so redesigning the example
    if not os.path.exists(app):                                  # doesn't read as a speed change
        app = os.path.join(root, "examples", "todo", "app.pyweb")
    if not os.path.exists(app):
        return None                     # installed without the examples
    running = Running(Site(app))
    try:
        subprocess.run([sys.executable, "-c", _LOAD, str(running.port), "4", "0.3"], check=True,
                       capture_output=True)   # warm up
        done = subprocess.run([sys.executable, "-c", _LOAD, str(running.port), str(connections), str(seconds)],
                              check=True, capture_output=True, text=True)
        return round(float(done.stdout.strip()))
    finally:
        running.stop(timeout=2.0)


def saas_page_us(repeat=300):
    """A signed-in project page with 23 tasks (a fixed copy of the 0.5 saas template on SQLite), in microseconds."""
    import os
    import shutil
    import tempfile
    from pyweb.mcp import scaffold
    from pyweb.testing import TestClient
    old = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)
            fixed = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench", "apps", "saas")
            if os.path.isdir(fixed):
                shutil.copytree(fixed, os.path.join(tmp, "team"))
            else:
                scaffold(os.path.join(tmp, "team"), template="saas")
            os.chdir(os.path.join(tmp, "team"))
            client = TestClient("app.pyweb")
            client.login("bench@example.com")
            project = client.rpc("create_project", project={"name": "Bench"})
            pid = project["id"] if isinstance(project, dict) else project.id
            for i in range(23 - _task_count(client, pid)):
                client.rpc("add_task", project_id=pid, title=f"task {i}")
            path = f"/projects/{pid}"
            assert client.get(path).status == 200
            for _ in range(30):
                client.get(path)
            runs = []
            for _ in range(5):
                t0 = time.perf_counter()
                for _ in range(repeat // 5):
                    client.get(path)
                runs.append((time.perf_counter() - t0) * 1e6 / (repeat // 5))
            return round(_median(runs))
        finally:
            os.chdir(old)


def _task_count(client, pid):
    import re
    return len(re.findall(r"<li\b", client.get(f"/projects/{pid}").text))


def startup_ms(rounds=7):
    """``import pyweb`` and ``pyweb --version``, minus a bare interpreter start, in milliseconds."""
    import subprocess
    import sys

    def wall(args):
        times = []
        for _ in range(rounds):
            t0 = time.perf_counter()
            subprocess.run([sys.executable, *args], check=True, capture_output=True)
            times.append((time.perf_counter() - t0) * 1000)
        return _median(times)
    floor = wall(["-c", "pass"])
    return (round(max(0.0, wall(["-c", "import pyweb"]) - floor), 1),
            round(max(0.0, wall(["-m", "pyweb.cli", "--version"]) - floor), 1))


def live_10k_ms():
    """Version hash plus row diff for a 10,000-row live query, in milliseconds."""
    from pyweb.livedata import _version, diff
    old = [{"id": i, "title": f"row {i}", "done": i % 3 == 0, "n": i * 1.5} for i in range(10_000)]
    new = [dict(r, done=not r["done"]) if r["id"] % 100 == 0 else r for r in old]
    runs = []
    for _ in range(5):
        t0 = time.perf_counter()
        _version(new)
        diff(old, new, "id")
        runs.append((time.perf_counter() - t0) * 1000)
    return round(_median(runs), 2)


def system():
    """Whole-system numbers on this machine (see :data:`SYSTEM`)."""
    import os
    os.environ.setdefault("PYWEB_WORKER", "0")
    imp, cli = startup_ms()
    return {"network_rps": network_rps(), "saas_page_us": saas_page_us(), "import_ms": imp,
            "cli_version_ms": cli, "live_10k_ms": live_10k_ms()}


def regressions(base, head, *, tolerance=0.15):
    """Lines describing every metric where ``head`` is worse than ``base`` by more than ``tolerance``."""
    out = []
    for name, (higher, floor) in SYSTEM.items():
        b, h = base.get(name), head.get(name)
        if b is None or h is None or b == 0:
            continue
        worse = (b - h) if higher else (h - b)
        if worse > floor and worse / b > tolerance:
            out.append(f"{name}: {b} -> {h} ({worse / b:+.0%} worse)")
    return out


def compare(base_root, *, rounds=5):
    """Run :func:`system` for the checkout at ``base_root`` and for this one, alternately, ``rounds`` times
    each on this machine; returns ``(base medians, head medians)``."""
    import os
    import subprocess
    import sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    runs = {"base": [], "head": []}

    def once(root):
        env = {**os.environ, "PYTHONPATH": root, "PYWEB_WORKER": "0"}
        done = subprocess.run([sys.executable, "-m", "pyweb.bench", "--system", "--json"], cwd=root, env=env,
                              capture_output=True, text=True, timeout=600)
        if done.returncode != 0:
            return None
        return json.loads(done.stdout)
    for _ in range(rounds):
        for label, root in (("base", base_root), ("head", here)):
            got = once(root)
            if got is not None:
                runs[label].append(got)
    medians = {}
    for label, results in runs.items():
        medians[label] = {name: _median([r[name] for r in results if r.get(name) is not None])
                          for name in SYSTEM if any(r.get(name) is not None for r in results)}
    return medians["base"], medians["head"]


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="python -m pyweb.bench", description="Measure compile time, "
                                 "shipped JavaScript and server render time.")
    ap.add_argument("--json", action="store_true", help="print only JSON")
    ap.add_argument("--max-ssr-ms", type=float, help="fail if any server render is slower")
    ap.add_argument("--max-compile-ms", type=float, help="fail if any compile is slower")
    ap.add_argument("--system", action="store_true",
                    help="whole-system numbers: requests/s over sockets, a signed-in page, start-up, live queries")
    ap.add_argument("--compare", metavar="DIR",
                    help="compare the system numbers with another checkout (e.g. the previous commit) on this "
                    "machine; fail on a regression over 15%%")
    ap.add_argument("--rounds", type=int, default=5, help="runs per side with --compare (median wins)")
    args = ap.parse_args(argv)
    if args.system:
        print(json.dumps(system(), indent=None if args.json else 2))
        return 0
    if args.compare:
        base, head = compare(args.compare, rounds=args.rounds)
        print(json.dumps({"base": base, "head": head}, indent=2))
        if not base:
            print("the base checkout has no system benchmark: nothing to compare", file=sys.stderr)
            return 0
        if not head:
            print("the system benchmark failed on this checkout", file=sys.stderr)
            return 1
        failed = regressions(base, head)
        for line in failed:
            print("slower:", line, file=sys.stderr)
        return 1 if failed else 0
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
