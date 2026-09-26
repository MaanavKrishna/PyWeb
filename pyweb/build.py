"""Production build: hashed assets, minified JS, split bundles, CSS files.

Layout produced by :func:`build`:

```text
dist/
  static/
    runtime.<hash>.js      shared browser runtime (cached across pages)
    <page>.<hash>.js       per-route code (route splitting)
    <page>.<hash>.css      extracted page styles
    <page>.<hash>.js.map   source maps back to Python
  server/
    <page>.html            SSR shell referencing hashed assets
  manifest.json            routes, rpc, hashes, sizes
  deploy/
    Dockerfile             multi-stage standalone image
```

Why content hashes: immutable caching — a deploy only invalidates
changed pages. Why minify without a dep: the emitted JS is machine
generated (no exotic syntax), so comment/whitespace stripping is
safe; teams can plug in esbuild via ``minifier=`` for app code.
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


def minify_js(js: str, *, minifier=None) -> str:
    """Minify emitted JS. External ``minifier`` callable wins if given."""
    if minifier is not None:
        return minifier(js)
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    js = re.sub(r"(^|\s)//[^\n]*", r"\1", js)
    js = re.sub(r"[ \t]+", " ", js)
    js = re.sub(r"\s*\n\s*", "\n", js)
    js = re.sub(r"\s*([{}();,:=+\-*/<>!&|?])\s*", r"\1", js)
    return js.strip() + "\n"


def build(compiled: dict, out: str, *, minifier=None,
          extract_css=True) -> dict:
    """Write hashed production artifacts for ``compile_source`` output."""
    from pyweb.css import extract_styles
    os.makedirs(f"{out}/static", exist_ok=True)
    os.makedirs(f"{out}/server", exist_ok=True)
    os.makedirs(f"{out}/deploy", exist_ok=True)

    try:
        from pyweb.runtime.browser import __file__ as _rt  # noqa
        rt_path = os.path.join(os.path.dirname(__file__), "runtime",
                               "browser", "runtime.js")
        with open(rt_path) as fh:
            runtime_js = fh.read()
    except OSError:
        runtime_js = ""
    runtime_js = minify_js(runtime_js, minifier=minifier)
    rt_hash = content_hash(runtime_js)
    rt_name = f"runtime.{rt_hash}.js"
    with open(f"{out}/static/{rt_name}", "w") as fh:
        fh.write(runtime_js)

    manifest_pages = {}
    for name, page in compiled["pages"].items():
        body = minify_js(page.get("js", ""), minifier=minifier)
        html = page.get("html", "")
        css_text = ""
        if extract_css:
            html, css_text = extract_styles(html)
        h = content_hash(body)
        js_name = f"{name}.{h}.js"
        with open(f"{out}/static/{js_name}", "w") as fh:
            fh.write(body)
        with open(f"{out}/static/{js_name}.map", "w") as fh:
            json.dump({"page": name, "hash": h,
                       "mappings": page.get("sourcemap", [])}, fh)
        css_name = ""
        if css_text.strip():
            css_name = f"{name}.{content_hash(css_text)}.css"
            with open(f"{out}/static/{css_name}", "w") as fh:
                fh.write(css_text + "\n")
            html = html.replace("</head>",
                                f'<link rel="stylesheet" href="/static/{css_name}"></head>')
        html = html.replace("runtime.js", rt_name).replace(
            f"{name}.js", js_name)
        with open(f"{out}/server/{name}.html", "w") as fh:
            fh.write(html)
        manifest_pages[name] = {
            "route": page.get("route"), "js": js_name, "css": css_name,
            "runtime": rt_name, "bytes": len(body.encode()),
            "signals": page.get("signals", []),
            "computeds": list(page.get("computeds", []))}

    manifest = {"pages": manifest_pages, "rpc": compiled.get("rpc", []),
                "ir": compiled.get("ir_text", ""),
                "runtime": rt_name}
    with open(f"{out}/manifest.json", "w") as fh:
        json.dump(manifest, fh, indent=2)
    write_dockerfile(f"{out}/deploy/Dockerfile")
    return manifest


def write_dockerfile(path: str) -> None:
    with open(path, "w") as fh:
        fh.write(
            "FROM python:3.13-slim AS base\n"
            "WORKDIR /app\n"
            "COPY server/ ./server/\n"
            "COPY static/ ./static/\n"
            "COPY manifest.json ./\n"
            "RUN pip install --no-cache-dir pyweb\n"
            "EXPOSE 8000\n"
            'CMD ["python", "-m", "pyweb.cli", "serve", "--dir", "."]\n')
