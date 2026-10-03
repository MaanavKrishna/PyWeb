"""Build: a self-contained, deployable ``dist/`` directory.

```text
dist/
  app.pyweb                the app source (server functions + page bodies)
  manifest.json            routes, assets, RPC table, byte sizes
  Dockerfile               `pyweb serve` image
  static/
    runtime.<hash>.js      shared browser runtime (cached across pages)
    <page>.<hash>.js       per-page code (pages without interactivity ship none)
    <page>.<hash>.js.map   handler/signal → .pyweb line mapping
    ...                    your app's own static/ folder, copied as-is
  server/
    <page>.html            static prerender (literal state) for static hosting
```

Content hashes give immutable caching: a deploy only invalidates what
changed. The built-in minifier is token-aware (strings, templates and
regex literals are never touched); plug in esbuild via ``minifier=``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

__all__ = ["build", "minify_js", "content_hash", "write_dockerfile"]


def content_hash(text) -> str:
    if isinstance(text, str):
        text = text.encode()
    return hashlib.sha256(text).hexdigest()[:12]


_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^") | {""}
_REGEX_KEYWORDS = {"return", "typeof", "case", "do", "else", "in", "of", "void", "throw", "new", "delete"}
_PUNCT = set("{}()[];,:=<>!&|?+-*/%^~.")


def minify_js(js: str, *, minifier=None) -> str:
    """Minify JS safely: strip comments and collapse whitespace.

    Token-aware: string literals, template literals and regex literals are
    copied verbatim, so ``"Count: "`` or ``"https://x"`` are never altered.
    Newlines are kept where removing one could change meaning (ASI).
    An external ``minifier`` callable (e.g. esbuild) wins if given.
    """
    if minifier is not None:
        return minifier(js)
    out = []
    i, n = 0, len(js)
    last = ""          # last significant token text
    pending_space = False
    pending_newline = False

    def emit(tok):
        nonlocal last, pending_space, pending_newline
        if out:
            prev = out[-1][-1]
            if pending_newline and not (prev in "{(,;[" or tok[0] in "}),;]." or tok[0] in "=:?"):
                out.append("\n")
            elif pending_space and (prev.isalnum() or prev in "_$") and (tok[0].isalnum() or tok[0] in "_$"):
                out.append(" ")
            elif pending_space and prev in "+-" and tok[0] == prev:
                out.append(" ")
        out.append(tok)
        last = tok
        pending_space = pending_newline = False

    while i < n:
        c = js[i]
        if c in " \t\r":
            pending_space = True
            i += 1
            continue
        if c == "\n":
            pending_newline = True
            i += 1
            continue
        if js.startswith("//", i):
            j = js.find("\n", i)
            i = n if j < 0 else j
            continue
        if js.startswith("/*", i):
            j = js.find("*/", i + 2)
            i = n if j < 0 else j + 2
            pending_space = True
            continue
        if c in "\"'":
            j = i + 1
            while j < n and js[j] != c:
                j += 2 if js[j] == "\\" else 1
            emit(js[i:j + 1])
            i = j + 1
            continue
        if c == "`":
            j = i + 1
            depth = 0
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js.startswith("${", j):
                    depth += 1
                    j += 2
                    continue
                if js[j] == "}" and depth:
                    depth -= 1
                elif js[j] == "`" and not depth:
                    break
                j += 1
            emit(js[i:j + 1])
            i = j + 1
            continue
        if c == "/" and (last[-1:] in _REGEX_PREV or last in _REGEX_KEYWORDS):
            j = i + 1
            in_class = False
            while j < n:
                ch = js[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                j += 1
            j += 1
            while j < n and (js[j].isalpha()):
                j += 1
            emit(js[i:j])
            i = j
            continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (js[j].isalnum() or js[j] in "_$"):
                j += 1
            emit(js[i:j])
            i = j
            continue
        emit(c)
        i += 1
    return "".join(out).strip() + "\n"


def _gzip_size(text) -> int:
    import gzip
    return len(gzip.compress(text.encode() if isinstance(text, str) else text, 9))


def build(compiled: dict, out: str, *, minifier=None, extract_css=True,
          source=None, app_dir=None, production=True) -> dict:
    """Write a deployable ``dist/`` for ``compile_source`` output.

    ``production=True`` minifies and content-hashes JS; otherwise files
    keep plain names (``Home.js``) and pages reference ``?v=<hash>``.
    ``source`` (the `.pyweb` text) is copied to ``dist/app.pyweb`` so
    ``pyweb serve dist`` can run ``@server`` functions and render pages.
    A ``static/`` folder next to the app (``app_dir``) is copied as-is.
    """
    import shutil
    from pyweb.css import extract_styles
    os.makedirs(f"{out}/static", exist_ok=True)
    os.makedirs(f"{out}/server", exist_ok=True)

    if app_dir and os.path.isdir(os.path.join(app_dir, "static")):
        shutil.copytree(os.path.join(app_dir, "static"), f"{out}/static", dirs_exist_ok=True)

    rt_path = os.path.join(os.path.dirname(__file__), "runtime", "browser", "runtime.js")
    with open(rt_path, encoding="utf-8") as fh:
        runtime_js = fh.read()
    if production:
        runtime_js = minify_js(runtime_js, minifier=minifier)
        rt_name = f"runtime.{content_hash(runtime_js)}.js"
    else:
        rt_name = "runtime.js"
    with open(f"{out}/static/{rt_name}", "w", encoding="utf-8") as fh:
        fh.write(runtime_js)

    manifest_pages = {}
    for name, page in compiled["pages"].items():
        js = page.get("js", "")
        html = page.get("html", "")
        js_name = ""
        body = ""
        if js:
            body = js.replace('from "./runtime.js"', f'from "./{rt_name}"')
            if production:
                body = minify_js(body, minifier=minifier)
                js_name = f"{name}.{content_hash(body)}.js"
                url = f"/static/{js_name}"
            else:
                js_name = f"{name}.js"
                url = f"/static/{js_name}?v={content_hash(body)[:10]}"
            with open(f"{out}/static/{js_name}", "w", encoding="utf-8") as fh:
                fh.write(body)
            with open(f"{out}/static/{js_name}.map", "w", encoding="utf-8") as fh:
                json.dump({"page": name, "mappings": page.get("sourcemap", [])}, fh)
            html = re.sub(r'src="/static/' + re.escape(name) + r'\.js\?v=[0-9a-f]+"', f'src="{url}"', html)
        css_text = ""
        if extract_css:
            html, css_text = extract_styles(html)
        css_name = ""
        if css_text.strip():
            css_name = f"{name}.{content_hash(css_text)}.css"
            with open(f"{out}/static/{css_name}", "w", encoding="utf-8") as fh:
                fh.write(css_text + "\n")
            html = html.replace("</head>", f'<link rel="stylesheet" href="/static/{css_name}"></head>')
        with open(f"{out}/server/{name}.html", "w", encoding="utf-8") as fh:
            fh.write(html)
        manifest_pages[name] = {
            "route": page.get("route"), "js": js_name, "css": css_name,
            "runtime": rt_name if js else "", "bytes": len(body.encode()),
            "gzip_bytes": _gzip_size(body) if body else 0,
            "dynamic": bool(page.get("dynamic")),
            "signals": page.get("signals", []),
            "computeds": list(page.get("computeds", []))}

    if source is not None:
        with open(f"{out}/app.pyweb", "w", encoding="utf-8") as fh:
            fh.write(source)
        if app_dir and os.path.isfile(os.path.join(app_dir, "pyweb.lock")):  # npm packages (files are in static/)
            shutil.copyfile(os.path.join(app_dir, "pyweb.lock"), os.path.join(out, "pyweb.lock"))
        # Other .pyweb files the app imports, at the same relative paths.
        for lib in compiled.get("libraries", []):
            rel = os.path.relpath(lib.path, app_dir) if app_dir else os.path.basename(lib.path)
            if rel.startswith(".."):
                rel = os.path.basename(lib.path)
            os.makedirs(os.path.dirname(os.path.join(out, rel)) or out, exist_ok=True)
            shutil.copyfile(lib.path, os.path.join(out, rel))
    try:
        from pyweb import __version__ as pyweb_version
    except ImportError:  # pragma: no cover
        pyweb_version = "unknown"
    manifest = {"pyweb": pyweb_version, "pages": manifest_pages,
                "rpc": compiled.get("rpc", []), "ir": compiled.get("ir_text", ""),
                "runtime": rt_name, "runtime_bytes": len(runtime_js.encode()),
                "runtime_gzip_bytes": _gzip_size(runtime_js),
                "app": "app.pyweb" if source is not None else None,
                "npm": compiled.get("importmap") or {}}
    with open(f"{out}/manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    write_dockerfile(f"{out}/Dockerfile", version=pyweb_version)
    return manifest


def write_dockerfile(path: str, version: str = "") -> None:
    """A minimal image that serves this dist with ``pyweb serve``."""
    pin = f"=={version}" if version and version[0].isdigit() and "+" not in version else ""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            f"RUN pip install --no-cache-dir pyweb-stack{pin}\n"
            "COPY . /app/\n"
            "RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi\n"
            "ENV PYWEB_ENV=production\n"
            "EXPOSE 8000\n"
            "HEALTHCHECK CMD python -c \"import urllib.request; "
            "urllib.request.urlopen('http://127.0.0.1:8000/healthz')\"\n"
            'CMD ["pyweb", "serve", ".", "--host", "0.0.0.0", "--port", "8000"]\n')
