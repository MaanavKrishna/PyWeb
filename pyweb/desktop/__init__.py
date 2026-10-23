"""Desktop apps: the same app in its own window, with its data on the user's computer.

Run it with ``pyweb desktop app.pyweb``; package it with ``pyweb build --desktop``.
Inside the desktop app, server code can use the computer::

    from pyweb import desktop, server

    @server
    def export(text: str) -> str | None:
        return desktop.save_file("notes.txt", text)      # the system "Save as" dialog

Everything here runs on the server side (in the same process as the window), behind
the same checks as any server function. The page itself gets no extra powers: a page
loaded in the window can do exactly what it could do in a browser tab.

Every call raises :class:`DesktopUnavailable` outside the desktop app; check
:func:`is_desktop` (or ``pyweb.platform.desktop``) first.
"""

from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import sys

__all__ = ["Window", "DesktopUnavailable", "is_desktop", "data_dir", "open_file", "save_file", "choose_folder",
           "notify", "reveal", "open_url", "window"]


class DesktopUnavailable(RuntimeError):
    """A desktop-only function was called outside the desktop app."""


@dataclasses.dataclass
class Window:
    """The desktop window: ``App(desktop=Window(width=1200, height=800))``.

    ``title`` defaults to the app's title; ``icon`` (a square PNG, relative to the app's
    folder) to the installable app's icon, else a drawn one. ``start_url`` is the page the
    window opens on.
    """

    width: int = 1100
    height: int = 760
    min_size: tuple = (640, 480)
    resizable: bool = True
    title: str | None = None
    icon: str | None = None
    start_url: str = "/"
    maximized: bool = False

    def __post_init__(self):
        if self.width < 200 or self.height < 150:
            raise ValueError("desktop Window is too small (at least 200×150)")
        if len(tuple(self.min_size)) != 2:
            raise ValueError("desktop Window min_size is (width, height)")
        if not self.start_url.startswith("/") or self.start_url.startswith("//"):
            raise ValueError("desktop Window start_url must be a path of the app, like '/'")


def config(app) -> Window:
    """The app's :class:`Window` settings (defaults when it has none)."""
    value = getattr(app, "desktop", None)
    if value is None or value is True:
        return Window()
    if isinstance(value, Window):
        return value
    if isinstance(value, dict):
        return Window(**value)
    raise TypeError("App(desktop=...) takes pyweb.desktop.Window(...)")


# ------------------------------------------------------------------ the running shell

class _Shell:
    window = None        # the pywebview window, once it exists


SHELL = _Shell()


def is_desktop() -> bool:
    """True inside ``pyweb desktop`` or a packaged desktop app."""
    return os.environ.get("PYWEB_DESKTOP", "").strip().lower() in ("1", "true", "yes", "on")


def _need():
    if not is_desktop():
        raise DesktopUnavailable("this only works in the desktop app (pyweb desktop app.pyweb); "
                                 "check pyweb.platform.desktop first")


def _need_window():
    _need()
    if SHELL.window is None:
        raise DesktopUnavailable("the desktop window isn't open")
    return SHELL.window


def window():
    """The pywebview window (``set_title()``, ``maximize()``...)."""
    return _need_window()


# ------------------------------------------------------------------ the data folder

def safe_name(name) -> str:
    """``name`` as a folder name every OS accepts."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip(" .")
    return name[:80] or "PyWeb app"


def data_dir(name=None, *, platform=None, environ=None) -> str:
    """Where the desktop app keeps its database, uploads and settings.

    Windows: ``%APPDATA%\\<name>``; macOS: ``~/Library/Application Support/<name>``; Linux and
    others: ``$XDG_DATA_HOME/<name>`` (``~/.local/share/<name>``). ``PYWEB_DESKTOP_DATA`` wins.
    Without ``name``, the running app's folder.
    """
    env = os.environ if environ is None else environ
    if env.get("PYWEB_DESKTOP_DATA"):
        return env["PYWEB_DESKTOP_DATA"]
    if name is None:
        _need()
        name = env.get("PYWEB_DESKTOP_NAME") or "PyWeb app"
    platform = platform or sys.platform
    home = os.path.expanduser("~") if environ is None else env.get("HOME", os.path.expanduser("~"))
    name = safe_name(name)
    if platform.startswith("win"):
        base = env.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return os.path.join(base, name)
    if platform == "darwin":
        return os.path.join(home, "Library", "Application Support", name)
    base = env.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(base, name.lower().replace(" ", "-"))


# ------------------------------------------------------------------ dialogs

def _dialog(kind, **kwargs):
    win = _need_window()
    import webview
    return win.create_file_dialog(getattr(webview.FileDialog, kind), **kwargs)


def open_file(types=(), *, multiple=False, directory=""):
    """The system "Open" dialog: a path (a list with ``multiple=True``), or None when cancelled.

    ``types``: e.g. ``("Text files (*.txt;*.md)", "All files (*.*)")``.
    """
    got = _dialog("OPEN", directory=directory, allow_multiple=multiple, file_types=tuple(types))
    if not got:
        return [] if multiple else None
    got = [str(p) for p in (got if isinstance(got, (list, tuple)) else [got])]
    return got if multiple else got[0]


def save_file(filename="", data=None, *, directory="", types=()):
    """The system "Save as" dialog. Writes ``data`` (bytes or text) to the chosen file when given.
    Returns the path, or None when cancelled."""
    got = _dialog("SAVE", directory=directory, save_filename=filename, file_types=tuple(types))
    if not got:
        return None
    path = str(got[0] if isinstance(got, (list, tuple)) else got)
    if data is not None:
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        with open(path, "wb") as fh:
            fh.write(raw)
    return path


def choose_folder(directory=""):
    """The system folder picker: a path, or None when cancelled."""
    got = _dialog("FOLDER", directory=directory)
    if not got:
        return None
    return str(got[0] if isinstance(got, (list, tuple)) else got)


# ------------------------------------------------------------------ the rest of the computer

def _run(argv):
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def _applescript_string(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title, body="") -> bool:
    """A system notification (best effort: returns False when the OS offers no way to show one)."""
    _need()
    title, body = str(title)[:200], str(body)[:1000]
    if sys.platform == "darwin":
        script = f"display notification {_applescript_string(body)} with title {_applescript_string(title)}"
        return _run(["osascript", "-e", script])
    if sys.platform.startswith("win"):
        # Values travel as environment variables, so nothing in them is ever read as PowerShell.
        script = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]"
                  " > $null; $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                  "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); $x = $t.GetElementsByTagName('text');"
                  " $x.Item(0).AppendChild($t.CreateTextNode($env:PYWEB_TITLE)) > $null;"
                  " $x.Item(1).AppendChild($t.CreateTextNode($env:PYWEB_BODY)) > $null;"
                  " [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('PyWeb').Show("
                  "[Windows.UI.Notifications.ToastNotification]::new($t))")
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             env={**os.environ, "PYWEB_TITLE": title, "PYWEB_BODY": body},
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            return True
        except OSError:
            return False
    return _run(["notify-send", "--", title, body])


def reveal(path) -> bool:
    """Show ``path`` in the file manager (Explorer, Finder, or the folder on Linux)."""
    _need()
    path = os.path.abspath(str(path))
    if sys.platform == "darwin":
        return _run(["open", "-R", "--", path])
    if sys.platform.startswith("win"):
        return _run(["explorer", "/select," + path])
    return _run(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])


SAFE_SCHEMES = ("http://", "https://", "mailto:")


def open_url(url) -> bool:
    """Open a web page or email address in the user's browser or mail app (only http, https, mailto)."""
    _need()
    url = str(url or "").strip()
    if not url.lower().startswith(SAFE_SCHEMES) or any(c in url for c in "\r\n\x00"):
        raise ValueError("only http(s) and mailto links can be opened")
    import webbrowser
    return bool(webbrowser.open(url))
