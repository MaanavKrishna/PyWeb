# Desktop apps

The same `.pyweb` app can run as a desktop app: its own window, its data
on the user's computer, the system's file dialogs, and an installer for
Windows, macOS and Linux.

```bash
pip install "pyweb-stack[desktop]"
pyweb desktop app.pyweb
```

The window uses the system's web view (WebView2 on Windows, WKWebView on
macOS, WebKitGTK or Qt WebEngine on Linux), through
[pywebview](https://pywebview.flowrl.com). Nothing about the app
changes: pages, server functions, Models, live updates and background
jobs all work as they do on a server.

`pyweb desktop app.pyweb --dev` reloads the window when you save a file.

## The window

```python
from pyweb import App
from pyweb.desktop import Window

app = App(title="Field Notes", database="sqlite:///notes.db",
          desktop=Window(width=1200, height=800, min_size=(720, 480)))
```

| Setting | Default | |
|---|---|---|
| `width`, `height` | 1100 × 760 | the window's size when it opens |
| `min_size` | (640, 480) | how small it can be made |
| `resizable`, `maximized` | True, False | |
| `title` | the app's `title` | the window's title |
| `icon` | the [installable app's](31-pwa-push.md) icon, else a drawn one | a square PNG |
| `start_url` | `"/"` | the page the window opens on |

## Where the data goes

Each installation keeps its data in its own folder:

| | |
|---|---|
| Windows | `%APPDATA%\Field Notes` |
| macOS | `~/Library/Application Support/Field Notes` |
| Linux | `~/.local/share/field-notes` (or `$XDG_DATA_HOME`) |

The app runs inside that folder, so `sqlite:///notes.db` and uploaded
files are kept there. On first launch PyWeb also makes the
installation's own `PYWEB_AUTH_SECRET` and `PYWEB_DATA_KEY` (for
[encrypted fields](09-data.md)) and keeps them in `secrets.json`, which
only the user can read. The database is brought up to date when the app
starts (pending migrations applied, or tables created).
`PYWEB_DESKTOP_DATA` picks another folder.

## Using the computer

`pyweb.desktop` works from server code (server functions, pages, jobs),
in the same process as the window:

```python
from pyweb import desktop, platform, server


@server
def export(text: str) -> str | None:
    return desktop.save_file("notes.txt", text)     # "Save as"; the path, or None if cancelled


@server
def import_notes() -> str:
    path = desktop.open_file(("Text files (*.txt;*.md)", "All files (*.*)"))
    return open(path, encoding="utf-8").read() if path else ""
```

| Function | |
|---|---|
| `open_file(types=(), multiple=False)` | the "Open" dialog: a path (a list with `multiple=True`), or None |
| `save_file(filename, data=None)` | the "Save as" dialog; writes `data` (text or bytes) when given |
| `choose_folder()` | the folder picker |
| `notify(title, body)` | a system notification |
| `reveal(path)` | show a file in Explorer, Finder or the file manager |
| `open_url(url)` | open an `http(s)` or `mailto` link in the user's browser |
| `data_dir()` | the data folder |
| `window()` | the pywebview window (title, size...) |

Outside the desktop app they raise `DesktopUnavailable`, so check first:

```pyweb
from pyweb import App, platform

app = App(title="Field Notes")


@app.page("/")
def Home():
    if platform.desktop:
        <p>Saved on this computer.</p>
    else:
        <p>Saved in your account.</p>
```

`pyweb.platform` tells pages where they run:

| | |
|---|---|
| `platform.desktop` | in the desktop app |
| `platform.pwa` | in the [installed web app](31-pwa-push.md) (opened from the home screen or dock). The browser reports this, so use it for layout, never for security |
| `platform.installed` | either of those |
| `platform.web` | not the desktop app |

## Security

The app's server listens on `127.0.0.1` on a random port, where other
programs on the computer could reach it, and so could websites open in
the user's browser. So only the app's own window gets in:

- **A cookie only the window has.** The window opens the app through a
  one-time launch link, which sets an `HttpOnly`, `SameSite=Strict`
  cookie. Every request and WebSocket needs it; anything else gets a
  403 page.
- **The exact host.** Requests must say `Host: 127.0.0.1:<port>`; a
  website trying DNS rebinding sends its own name and is refused.
- **No other origins.** Requests from other sites (`Origin`,
  `Sec-Fetch-Site: cross-site`) are refused, and the app sends no CORS
  headers.
- **The window shows only the app.** Links to other sites open in the
  user's browser (`http`, `https` and `mailto` only); `file://` URLs
  don't load.
- **No bridge into the page.** Everything in `pyweb.desktop` runs on the
  server side, behind the same checks as any server function. A page in
  the window can do no more than it could in a browser tab, and the
  usual [security policy](13-security.md) (scripts from the app only,
  Trusted Types) stays on.

## Installers

```bash
pip install briefcase
pyweb build --desktop app.pyweb --bundle com.fieldnotes --app-version 1.0.0
cd dist-desktop && briefcase package
```

`pyweb build --desktop` writes a [Briefcase](https://briefcase.readthedocs.io)
project to `dist-desktop/`: the app's folder (without local databases),
a launcher, and icons for every OS (PNGs, a Windows `.ico` and a macOS
`.icns`, made from one PNG). `--package` also runs Briefcase to make the
installer for the OS you're on: an MSI on Windows, a DMG on macOS, a
`.deb`/`.rpm` on Linux. Build each OS's installer on that OS (CI can do
all three).

| Flag | |
|---|---|
| `--bundle com.example` | your reversed domain: with the app's name, its identity on the OS |
| `--app-version 1.0.0` | the installer's version |
| `--author`, `--author-email` | shown by installers |
| `--requires PACKAGE` | another package the app needs (repeatable) |
| `--pyweb SPEC` | the PyWeb requirement (default: this version) |
| `--package` | make the installer now |

The installed app checks it opens with `PYWEB_DESKTOP_SMOKE=1` (or
`--smoke`): it opens the window, waits for the first page, prints
`pyweb desktop smoke: ok` and quits. PyWeb's CI builds and opens the
todo example this way.

### Signing

Unsigned apps work, but the OS warns people before opening them.

- **Windows:** sign the MSI with a code-signing certificate (`signtool`,
  or Azure Trusted Signing):
  `briefcase package windows --identity <certificate thumbprint>`.
- **macOS:** with an Apple Developer ID,
  `briefcase package macOS --identity "Developer ID Application: …"`
  signs and notarizes the DMG. Without one, `--adhoc-sign` (what
  `--package` uses) runs on your own Mac only.
- **Linux:** packages aren't signed; distribute them through your
  repository or as downloads with checksums.

## Limits

- One window per app.
- Push notifications ([Web Push](31-pwa-push.md)) are for installable web
  apps; desktop apps use `desktop.notify()`.
- On Linux, pywebview needs WebKitGTK (`gir1.2-webkit2-4.1`) or Qt
  WebEngine (`pip install "pywebview[qt6]"`).
