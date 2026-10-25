"""Open a file at a line in your editor (``pyweb dev``: Alt-click an element, or a link on the error page).

The editor is ``$PYWEB_EDITOR`` when set, a command with ``{file}`` and ``{line}`` in it (for example
``code -g {file}:{line}`` or ``idea --line {line} {file}``); otherwise the first of VS Code, Cursor,
Windsurf, Zed or Sublime Text found on ``PATH``. Without any of them the page gets a ``vscode://``
link to open instead. Only files inside the app's folder or PyWeb's UI kit can be opened.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

from .compiler.pipeline import KIT_DIR

EDITORS = [
    ("code", ["code", "-g", "{file}:{line}"]),
    ("cursor", ["cursor", "-g", "{file}:{line}"]),
    ("windsurf", ["windsurf", "-g", "{file}:{line}"]),
    ("zed", ["zed", "{file}:{line}"]),
    ("subl", ["subl", "{file}:{line}"]),
]


def _inside(path, folder):
    folder = os.path.realpath(folder)
    return os.path.commonpath([os.path.realpath(path), folder]) == folder


def resolve(src, app_dir):
    """``"file:line"`` (as ``data-pw-src`` has it) -> (absolute file, line, in the app?), or None when the
    file isn't the app's or the kit's."""
    if not isinstance(src, str) or ":" not in src:
        return None
    file, _, line = src.rpartition(":")
    try:
        line = max(1, int(line))
    except ValueError:
        return None
    path = file if os.path.isabs(file) else os.path.join(app_dir, file)
    path = os.path.realpath(path)
    if not os.path.isfile(path) or not path.endswith(".pyweb"):
        return None
    if _inside(path, app_dir):
        return path, line, True
    if _inside(path, KIT_DIR):
        return path, line, False
    return None


def pick(chain, app_dir):
    """The first place in ``chain`` (innermost element first) that is in the app, else the first in the kit."""
    found = [r for r in (resolve(s, app_dir) for s in (chain or [])[:64]) if r]
    mine = [r for r in found if r[2]]
    return (mine or found or [None])[0]


def command(file, line):
    """The editor command for ``file:line``, or None when there's no editor to run."""
    template = os.environ.get("PYWEB_EDITOR")
    if template:
        parts = shlex.split(template)
        if not any("{file}" in p for p in parts):
            parts.append("{file}")
    else:
        parts = next((cmd for exe, cmd in EDITORS if shutil.which(exe)), None)
        if parts is None:
            return None
    return [p.replace("{file}", file).replace("{line}", str(line)) for p in parts]


def open_source(chain, app_dir):
    """Open the best place in ``chain``. Returns what the page shows: the file, line, whether an editor
    was started, and a ``vscode://`` link to fall back on."""
    hit = pick(chain, app_dir)
    if hit is None:
        return {"error": "that element has no source in this app"}
    file, line, mine = hit
    out = {"file": os.path.relpath(file, app_dir) if mine else file, "line": line, "in_app": mine,
           "url": f"vscode://file{file if file.startswith('/') else '/' + file.replace(os.sep, '/')}:{line}",
           "opened": False}
    cmd = command(file, line)
    if cmd:
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            out["opened"] = True
        except OSError as exc:
            out["error"] = f"could not start {cmd[0]}: {exc.strerror or exc}"
    return out
