"""``pyweb build --desktop``: a Briefcase project (and, with ``--package``, an installer).

The project in ``dist-desktop/`` holds the app's folder, a ``__main__`` that opens it with
:func:`pyweb.desktop.shell.run`, and icons in every size the operating systems ask for
(PNGs, a Windows ``.ico`` and a macOS ``.icns``, all made from one PNG). BeeWare Briefcase
then bundles Python and the dependencies: an MSI on Windows, a DMG on macOS, a system
package (``.deb``/``.rpm``) on Linux.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys

from . import config, safe_name

PNG_SIZES = (16, 32, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 32, 48, 64, 128, 256)
ICNS_TYPES = ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
              (b"ic10", 1024))
SKIP = {"__pycache__", ".git", "dist", "dist-desktop", "build", "node_modules", ".venv", "venv", ".pytest_cache"}
SKIP_FILES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".pyc", ".log")

# pywebview's GTK backend pins PyGObject 3.50, which builds against gobject-introspection 1.0.
LINUX_BUILD = ["libcairo2-dev", "libgirepository1.0-dev", "pkg-config"]
LINUX_RUNTIME = ["gir1.2-gtk-3.0", "gir1.2-webkit2-4.1", "libgirepository-1.0-1", "libcanberra-gtk3-module"]


def ico(pngs: dict) -> bytes:
    """A Windows ``.ico`` holding the PNGs (``{size: bytes}``)."""
    sizes = sorted(pngs)
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for size in sizes:
        png = pngs[size]
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return head + entries + data


def icns(pngs: dict) -> bytes:
    """A macOS ``.icns`` holding the PNGs (``{size: bytes}``)."""
    body = b""
    for kind, size in ICNS_TYPES:
        if size in pngs:
            body += kind + struct.pack(">I", 8 + len(pngs[size])) + pngs[size]
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def icons(app, app_dir, out_dir, stem):
    """Write ``<stem>-<size>.png``, ``<stem>.ico`` and ``<stem>.icns`` into ``out_dir``; returns the stem path."""
    from pyweb import pwa
    window = config(app)
    pcfg = pwa.config(app) or pwa.PWA()
    if window.icon:
        pcfg = pwa.PWA(icon=window.icon, theme_color=pcfg.theme_color)
    os.makedirs(out_dir, exist_ok=True)
    pngs = {}
    for size in sorted(set(PNG_SIZES) | set(ICO_SIZES)):
        pngs[size] = pwa.icon_png(pcfg, app_dir, size)
    for size in PNG_SIZES:
        with open(os.path.join(out_dir, f"{stem}-{size}.png"), "wb") as fh:
            fh.write(pngs[size])
    with open(os.path.join(out_dir, f"{stem}.ico"), "wb") as fh:
        fh.write(ico({s: pngs[s] for s in ICO_SIZES}))
    with open(os.path.join(out_dir, f"{stem}.icns"), "wb") as fh:
        fh.write(icns(pngs))
    return os.path.join(out_dir, stem)


def slug(name) -> str:
    """A Python package name for the app (Briefcase's app name)."""
    s = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    if not s or s[0].isdigit():
        s = "app_" + s
    return s[:40]


def _toml_str(value) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _toml_list(values) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def pyproject(*, name, formal_name, bundle, version, description, author, author_email, url, requires,
              pyweb_requirement) -> str:
    """The Briefcase ``pyproject.toml``."""
    base = [pyweb_requirement, *requires]
    return f"""# Made by `pyweb build --desktop`. Build an installer with `briefcase package`
# (or `pyweb build --desktop --package`); see https://briefcase.readthedocs.io

[tool.briefcase]
project_name = {_toml_str(formal_name)}
bundle = {_toml_str(bundle)}
version = {_toml_str(version)}
url = {_toml_str(url)}
license = "LicenseRef-Proprietary"
license-files = ["LICENSE"]
author = {_toml_str(author)}
author_email = {_toml_str(author_email)}

[tool.briefcase.app.{name}]
formal_name = {_toml_str(formal_name)}
description = {_toml_str(description)}
long_description = {_toml_str(description)}
icon = {_toml_str("icons/" + name)}
sources = {_toml_list(["src/" + name])}
requires = {_toml_list(base)}

[tool.briefcase.app.{name}.macOS]
universal_build = true

[tool.briefcase.app.{name}.linux]
requires = ["pywebview[gtk]"]

[tool.briefcase.app.{name}.linux.system.debian]
system_requires = {_toml_list(LINUX_BUILD)}
system_runtime_requires = {_toml_list(LINUX_RUNTIME)}

[tool.briefcase.app.{name}.linux.system.rhel]
system_requires = ["cairo-gobject-devel", "gobject-introspection-devel"]
system_runtime_requires = ["gobject-introspection", "gtk3", "webkit2gtk4.1", "libcanberra-gtk3"]

[tool.briefcase.app.{name}.linux.system.arch]
system_requires = ["cairo", "gobject-introspection", "gtk3", "webkit2gtk-4.1"]
system_runtime_requires = ["gtk3", "gobject-introspection-runtime", "webkit2gtk-4.1"]

[tool.briefcase.app.{name}.linux.flatpak]
flatpak_runtime = "org.gnome.Platform"
flatpak_runtime_version = "48"
flatpak_sdk = "org.gnome.Sdk"

[tool.briefcase.app.{name}.windows]
requires = []
"""


MAIN = '''"""Opens the PyWeb app in its own window (made by `pyweb build --desktop`)."""

import os
import sys

from pyweb.desktop import shell


def main():
    smoke = "--smoke" in sys.argv[1:] or os.environ.get("PYWEB_DESKTOP_SMOKE") == "1"
    shell.main(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", APP_FILE), smoke=smoke)


APP_FILE = {app_file!r}

if __name__ == "__main__":
    main()
'''


def _copy_app(app_dir, dest):
    def ignore(folder, names):
        return [n for n in names if n in SKIP or n.startswith(".") or n.endswith(SKIP_FILES)]
    shutil.copytree(app_dir, dest, ignore=ignore)


def _load_quietly(app_path, LoadedApp):
    """The app's ``App`` object, loaded in a scratch folder (a relative SQLite file lands there, not here)."""
    import tempfile
    here = os.getcwd()
    saved = os.environ.get("PYWEB_NO_AUTO_MIGRATE")
    with tempfile.TemporaryDirectory(prefix="pyweb-desktop-", ignore_cleanup_errors=True) as tmp:
        os.chdir(tmp)
        os.environ["PYWEB_NO_AUTO_MIGRATE"] = "1"
        try:
            return LoadedApp(app_path).app
        finally:
            os.chdir(here)
            if saved is None:
                os.environ.pop("PYWEB_NO_AUTO_MIGRATE", None)
            else:
                os.environ["PYWEB_NO_AUTO_MIGRATE"] = saved


def make_project(app_path, out="dist-desktop", *, bundle="com.example", version="0.1.0", author=None,
                 author_email=None, url=None, requires=(), pyweb_requirement=None):
    """Write the Briefcase project for ``app_path`` into ``out``; returns ``(out, app name)``."""
    from pyweb import __version__
    from pyweb.app_loader import LoadedApp
    from .shell import app_name
    app_path = os.path.abspath(app_path)
    app_dir = os.path.dirname(app_path)
    app = _load_quietly(app_path, LoadedApp)
    formal = (config(app).title or getattr(app, "title", None) or app_name(app_path))
    formal = safe_name(formal)
    name = slug(formal)
    if not re.fullmatch(r"[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+", bundle or ""):
        raise ValueError("--bundle must be a reversed domain name, like com.example")
    if not re.fullmatch(r"\d+(\.\d+){0,3}", version or ""):
        raise ValueError("--version must look like 1.0 or 1.2.3")
    if os.path.exists(out):
        shutil.rmtree(out)
    pkg = os.path.join(out, "src", name)
    os.makedirs(pkg)
    _copy_app(app_dir, os.path.join(pkg, "app"))
    with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as fh:
        fh.write("")
    with open(os.path.join(pkg, "__main__.py"), "w", encoding="utf-8") as fh:
        fh.write(MAIN.replace("{app_file!r}", repr(os.path.basename(app_path))))
    icons(app, app_dir, os.path.join(out, "icons"), name)
    text = pyproject(
        name=name, formal_name=formal, bundle=bundle, version=version,
        description=getattr(app, "description", None) or f"{formal}, made with PyWeb",
        author=author or os.environ.get("PYWEB_AUTHOR") or "PyWeb developer",
        author_email=author_email or os.environ.get("PYWEB_AUTHOR_EMAIL") or "developer@example.com",
        url=url or getattr(app, "base_url", None) or "https://example.com",
        requires=list(requires), pyweb_requirement=pyweb_requirement or f"pyweb-stack[desktop]=={__version__}")
    with open(os.path.join(out, "pyproject.toml"), "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(out, "LICENSE"), "w", encoding="utf-8") as fh:
        fh.write(f"Copyright {formal}. All rights reserved.\n")
    with open(os.path.join(out, "CHANGELOG"), "w", encoding="utf-8") as fh:      # Linux packages need one
        fh.write(f"{version}\n\n* Built with PyWeb {__version__}.\n")
    return out, name


def briefcase_platform():
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def package(out):
    """Run Briefcase in ``out``: create, build and package an installer for this OS."""
    exe = shutil.which("briefcase")
    if exe is None:
        raise SystemExit("packaging needs Briefcase: pip install briefcase")
    plat = briefcase_platform()
    for step in ("create", "build", "package"):
        cmd = [exe, step, plat, "--no-input"]
        if step == "package" and plat == "macOS":
            cmd.append("--adhoc-sign")          # sign with your identity for distribution (see the docs)
        print("$ " + " ".join(cmd), flush=True)
        if subprocess.call(cmd, cwd=out) != 0:
            raise SystemExit(f"briefcase {step} failed")
    made = [os.path.join(out, "dist", f) for f in sorted(os.listdir(os.path.join(out, "dist")))] \
        if os.path.isdir(os.path.join(out, "dist")) else []
    return made
