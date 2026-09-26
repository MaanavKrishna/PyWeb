"""`pyweb` command-line interface (Track D owns this file)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .._graph import AppGraph, discover_app_graph
from ..observability import Tracer


def _print_tree(graph: AppGraph) -> str:
    lines = [f"app ({graph.root})"]

    def section(title: str, items: list[str]) -> None:
        lines.append(f"├── {title} ({len(items)})")
        for i, item in enumerate(items):
            tick = "└──" if i == len(items) - 1 else "├──"
            lines.append(f"│   {tick} {item}")

    section("routes", [f"{r.path} -> {r.handler} ({r.file}:{r.line})" for r in graph.routes])
    section(
        "components",
        [
            f"{c.name} [{c.kind}/{c.placement.decision}] {c.file}:{c.line}"
            + (f" — {c.placement.reason}" if c.placement.reason else "")
            + (f" props={','.join(c.props)}" if c.props else "")
            for c in graph.components
        ],
    )
    section("signals", [f"{s.name} ({s.file}:{s.line})" for s in graph.signals])
    section("rpc", [f"{r.name} ({r.file}:{r.line})" for r in graph.rpcs])
    if graph.rpc_edges:
        section("rpc-edges", [f"{e['from']} --{e['via']}--> {e['to']}" for e in graph.rpc_edges])
    return "\n".join(lines)


def cmd_inspect(args: argparse.Namespace) -> int:
    tracer = Tracer()
    with tracer.span("inspect.discover", root=args.root):
        graph = discover_app_graph(args.root)
    if args.json:
        print(json.dumps(graph.to_dict(), indent=2))
    else:
        print(_print_tree(graph))
    if args.verbose:
        print(f"\n[{tracer.summary()}]", file=sys.stderr)
    return 0


def _parse_budget(spec: str) -> int:
    spec = spec.strip().lower()
    for suffix, mult in (("kb", 1024), ("k", 1024), ("mb", 1024 * 1024), ("m", 1024 * 1024), ("b", 1)):
        if spec.endswith(suffix):
            return int(float(spec[: -len(suffix)]) * mult)
    return int(float(spec))


def cmd_build(args: argparse.Namespace) -> int:
    from ..observability import Timer

    tracer = Tracer()
    out = Path(args.out)
    with tracer.span("build.compile"):
        out.mkdir(parents=True, exist_ok=True)
        runtime = out / "runtime.js"
        if not runtime.exists():
            # Minimal runtime stub so budgets have something to measure
            # until the real pipeline lands.
            runtime.write_text(
                '"use strict";\nwindow.__pyweb={version:1};\n', encoding="utf-8"
            )
    with Timer() as t:
        total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    breaches: list[str] = []
    for spec in args.budget or []:
        name, _, limit = spec.partition("=")
        limit_b = _parse_budget(limit) if limit else _parse_budget(name)
        target = out / name if limit else None
        size = target.stat().st_size if target and target.exists() else total
        label = name if limit else "total"
        if size > limit_b:
            breaches.append(f"budget breach: {label} is {size}B > {limit_b}B")
    print(f"build: wrote {out} ({total}B in {t.elapsed_s * 1000:.1f}ms)")
    for b in breaches:
        print(f"build: {b}", file=sys.stderr)
    if breaches:
        return 2
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    from ..dev import DevOptions, DevServer

    server = DevServer(DevOptions(root=Path(args.root), port=args.port,
                                  out_dir=Path(args.out) if args.out else None))
    server.serve_forever(block=True)
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    dest = Path(args.name)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "app.py").write_text(
        '"""PyWeb app scaffold."""\n\n\ndef index():\n    return "<h1>Hello, PyWeb</h1>"\n',
        encoding="utf-8",
    )
    (dest / "index.html").write_text(
        "<!doctype html><html><body><h1>Hello, PyWeb</h1></body></html>\n",
        encoding="utf-8",
    )
    print(f"new: scaffolded {dest}")
    return 0


def cmd_stub(args: argparse.Namespace) -> int:
    print("test/check/fmt/lint/db/deploy: not yet implemented in Track D scaffold")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pyweb", description="PyWeb developer CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="Scaffold a new app")
    p.add_argument("name")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("dev", help="Start the dev server with live-reload")
    p.add_argument("--root", default=".")
    p.add_argument("--port", type=int, default=5173)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_dev)

    p = sub.add_parser("build", help="Build the app")
    p.add_argument("--out", default="dist")
    p.add_argument("--root", default=".")
    p.add_argument("--budget", action="append", default=[],
                   help="Budget like 'runtime.js=8KB' or '2KB' (total). Repeatable.")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("inspect", help="Show the app graph")
    p.add_argument("--root", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_inspect)

    for name in ("test", "check", "fmt", "lint", "db", "deploy"):
        p = sub.add_parser(name, help=f"{name} (stub)")
        p.set_defaults(func=cmd_stub)

    p = sub.add_parser("npm", help="Generate Python stubs from .d.ts")
    p.add_argument("dts")
    p.add_argument("-o", "--out", default=None)
    p.set_defaults(func=_cmd_npm)
    return ap


def _cmd_npm(args: argparse.Namespace) -> int:
    from ..npm import main as npm_main

    argv = [args.dts]
    if args.out:
        argv += ["-o", args.out]
    return npm_main(argv)


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())
