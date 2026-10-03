"""Benchmarks: compile time per example and server-render time per request.

    python benchmarks/bench.py                 # print a table
    python benchmarks/bench.py --json          # machine-readable
    python benchmarks/bench.py --max-request-us 2000 --max-compile-ms 50

The limits are deliberately loose (shared CI runners are noisy); they catch
order-of-magnitude regressions such as re-parsing templates per request.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyweb.compiler import compile_source  # noqa: E402
from pyweb.testing import TestClient  # noqa: E402

EXAMPLES = ["counter", "todo", "blog", "auth", "chat", "showcase"]
PAGES = {"counter": ["/"], "todo": ["/"], "blog": ["/", "/posts/1"], "showcase": ["/"]}


def best_of(fn, repeat, number):
    """Best average over ``repeat`` runs of ``number`` calls, in seconds."""
    best = float("inf")
    for _ in range(repeat):
        t = time.perf_counter()
        for _ in range(number):
            fn()
        best = min(best, (time.perf_counter() - t) / number)
    return best


def run():
    results = {"compile_ms": {}, "request_us": {}}
    for name in EXAMPLES:
        source = (ROOT / "examples" / name / "app.pyweb").read_text()
        results["compile_ms"][name] = round(best_of(lambda s=source: compile_source(s, filename="app.pyweb"), 3, 10) * 1e3, 2)
    with tempfile.TemporaryDirectory() as tmp:  # examples write their SQLite files to the working directory
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            for name, urls in PAGES.items():
                client = TestClient(str(ROOT / "examples" / name / "app.pyweb"))
                if name == "blog":
                    client.rpc("publish", title="Hello", body="First post")
                for url in urls:
                    assert client.get(url).status == 200, (name, url)
                    results["request_us"][f"{name} {url}"] = round(best_of(lambda u=url: client.get(u), 3, 200) * 1e6)
        finally:
            os.chdir(cwd)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-request-us", type=float)
    ap.add_argument("--max-compile-ms", type=float)
    args = ap.parse_args(argv)
    results = run()
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for name, ms in results["compile_ms"].items():
            print(f"compile  {name:22} {ms:8.2f} ms")
        for page, us in results["request_us"].items():
            print(f"request  {page:22} {us:8.0f} us")
    failed = [f"compile {k}: {v} ms" for k, v in results["compile_ms"].items()
              if args.max_compile_ms and v > args.max_compile_ms]
    failed += [f"request {k}: {v} us" for k, v in results["request_us"].items()
               if args.max_request_us and v > args.max_request_us]
    for line in failed:
        print("over budget:", line, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
