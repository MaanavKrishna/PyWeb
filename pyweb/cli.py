"""CLI: python -m pyweb.cli build <file.pyweb> [--out dist] [--route /]."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from pyweb.compiler.pipeline import build_file


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def cmd_build(args) -> int:
    artifacts = build_file(args.file, route=args.route)
    out = args.out
    os.makedirs(out, exist_ok=True)
    _write(os.path.join(out, "app.js"), artifacts.js_with_map())
    _write(os.path.join(out, "index.html"), artifacts.html)
    if artifacts.css and artifacts.css_file:
        _write(os.path.join(out, artifacts.css_file), artifacts.css)
    # runtime.js alongside
    here = os.path.join(os.path.dirname(__file__), "runtime", "browser", "runtime.js")
    with open(here, encoding="utf-8") as f:
        _write(os.path.join(out, "runtime.js"), f.read())
    _write(os.path.join(out, "manifest.json"), json.dumps(artifacts.manifest, indent=2))
    print(f"built {args.file} -> {out}/ (route={artifacts.route} live={artifacts.live})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pyweb")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="build a .pyweb file")
    build.add_argument("file", help="input .pyweb file")
    build.add_argument("--out", default="dist", help="output directory")
    build.add_argument("--route", default="/", help="route path")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        return cmd_build(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
