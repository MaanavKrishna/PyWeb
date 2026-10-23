"""``pyweb ui list | add NAME... | diff NAME``: own a kit component's code.

``add`` copies the ``.pyweb`` file that defines a component (with the others in the same file)
into your app's ``components/`` folder. Change it freely, then import it from there::

    from components.button import Button      # was: from pyweb.ui import Button

``diff`` shows how your copy differs from the kit's current version (after a PyWeb upgrade).
"""

from __future__ import annotations

import difflib
import os
import re
import shutil


def _index():
    from pyweb.compiler.pipeline import kit_index
    return kit_index()


def files():
    """``{file stem: [component names]}``."""
    out = {}
    for name, path in sorted(_index().items()):
        out.setdefault(os.path.splitext(os.path.basename(path))[0], []).append(name)
    return out


def _resolve(name):
    index = _index()
    if name in index:
        return index[name]
    by_stem = {os.path.splitext(os.path.basename(p))[0]: p for p in index.values()}
    if name.lower() in by_stem:
        return by_stem[name.lower()]
    raise SystemExit(f"no kit component or file {name!r} (see `pyweb ui list`)")


def add(names, app_dir=".", *, force=False):
    """Copy the files defining ``names`` into ``<app_dir>/components/``; returns the written paths."""
    dest_dir = os.path.join(app_dir, "components")
    os.makedirs(dest_dir, exist_ok=True)
    written = []
    for name in names:
        src = _resolve(name)
        dest = os.path.join(dest_dir, os.path.basename(src))
        if os.path.exists(dest) and not force:
            raise SystemExit(f"{dest} exists already (pass --force to overwrite it)")
        shutil.copyfile(src, dest)
        written.append(dest)
    return written


def diff(name, app_dir="."):
    src = _resolve(name)
    mine = os.path.join(app_dir, "components", os.path.basename(src))
    if not os.path.exists(mine):
        raise SystemExit(f"{mine} doesn't exist (copy it with `pyweb ui add {name}`)")
    with open(src, encoding="utf-8") as fh:
        upstream = fh.readlines()
    with open(mine, encoding="utf-8") as fh:
        local = fh.readlines()
    return "".join(difflib.unified_diff(upstream, local, f"pyweb.ui/{os.path.basename(src)}", mine))


def main(args):
    if args.ui_action == "list":
        for stem, names in files().items():
            print(f"{stem:12} {', '.join(names)}")
        return
    if args.ui_action == "add":
        if not args.names:
            raise SystemExit("name the components to copy, e.g. pyweb ui add button dialog")
        for path in add(args.names, args.dir, force=args.force):
            stem = os.path.splitext(os.path.basename(path))[0]
            with open(path, encoding="utf-8") as fh:
                names = re.findall(r"^def ([A-Z]\w*)\(", fh.read(), re.M)
            print(f"wrote {path}: import from it with `from components.{stem} import {', '.join(names)}`")
        return
    if args.ui_action == "diff":
        if len(args.names) != 1:
            raise SystemExit("pyweb ui diff takes one component or file name")
        print(diff(args.names[0], args.dir) or "no differences")
