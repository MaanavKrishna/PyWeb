"""Build the PyWeb website from the docs, the examples and the real compiler.

    python website/build.py            # -> website/dist/

Everything on the site is generated: guide pages come from ``docs/*.md``;
every ``pyweb`` code block gets a panel with what the compiler actually
produced for it; example pages show source, compiled JavaScript and the
placement report; the counter and todo demos are the examples compiled by
PyWeb and run in an iframe; benchmark numbers come from ``pyweb.bench``.
Standard library only.
"""

from __future__ import annotations

import gzip
import html
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "dist")
SITE = "https://maanavkrishna.github.io/PyWeb"
REPO = "https://github.com/MaanavKrishna/PyWeb"
sys.path.insert(0, ROOT)

from pyweb import __version__  # noqa: E402
from pyweb.build import minify_js  # noqa: E402
from pyweb.compiler import compile_source  # noqa: E402

# (file in docs/, page slug, nav title, group)
DOCS = [
    ("01-introduction.md", "introduction", "Introduction", "Start"),
    ("02-quickstart.md", "quickstart", "Quickstart", "Start"),
    ("03-tutorial.md", "tutorial", "Tutorial", "Start"),
    ("04-pyweb-files.md", "language", "The .pyweb language", "Language"),
    ("05-reactivity.md", "reactivity", "State & reactivity", "Language"),
    ("07-browser-python.md", "browser-python", "Python in the browser", "Language"),
    ("06-server-functions.md", "server-functions", "Server functions & RPC", "Server"),
    ("08-pages-routing-assets.md", "routing", "Pages, routing & assets", "Server"),
    ("09-data.md", "data", "Data & databases", "Server"),
    ("10-auth.md", "auth", "Authentication", "Server"),
    ("11-testing.md", "testing", "Testing", "Ship"),
    ("12-deployment.md", "deployment", "Deployment", "Ship"),
    ("13-security.md", "security", "Security model", "Ship"),
    ("14-cli.md", "cli", "Command line", "Ship"),
    ("15-toolkit.md", "toolkit", "Toolkit & stability", "Reference"),
    ("16-limitations-roadmap.md", "roadmap", "Limitations & roadmap", "Reference"),
]
DOC_SLUG = {f: s for f, s, _, _ in DOCS}
GROUPS = ["Start", "Language", "Server", "Ship", "Reference"]

EXAMPLES = [
    ("counter", "Counter", "Signals, a computed value and a bound number input.", True),
    ("todo", "Todos", "Components, list mutation, filters and keyed lists.", True),
    ("blog", "Blog", "SQL database, server functions, route parameters, 404s and validation errors.", False),
    ("auth", "Accounts", "Registration, password hashing, sessions and protected pages.", False),
    ("chat", "Chat rooms", "Route parameters, shared server state and polling with on_mount.", False),
    ("showcase", "Showcase", "Reactive state, derived values and a server-backed search on one page.", False),
]


def esc(s):
    return html.escape(str(s), quote=True)


# ------------------------------------------------------------- highlighting

PY_KW = {"False", "None", "True", "and", "as", "assert", "async", "await", "break", "class", "continue",
         "def", "del", "elif", "else", "except", "finally", "for", "from", "global", "if", "import", "in",
         "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield"}
PY_BUILTIN = {"len", "str", "int", "float", "bool", "list", "dict", "set", "tuple", "range", "print",
              "enumerate", "zip", "sorted", "min", "max", "sum", "any", "all", "isinstance", "abs", "round"}
JS_KW = {"const", "let", "var", "function", "return", "if", "else", "for", "of", "in", "while", "async",
         "await", "import", "from", "export", "new", "try", "catch", "finally", "throw", "true", "false",
         "null", "undefined", "break", "continue", "typeof"}

_TOKEN = re.compile(
    r"(?P<comment>#[^\n]*|//[^\n]*)"
    r"|(?P<string>[rbfRBF]?(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`))"
    r"|(?P<tag></?[A-Za-z][\w.-]*|/?>)"
    r"|(?P<deco>@[\w.]+)"
    r"|(?P<number>\b\d[\d_]*(?:\.\d+)?\b)"
    r"|(?P<name>[A-Za-z_$][\w$]*)"
    r"|(?P<other>[\s\S])"
)


def highlight(code, lang):
    if lang in ("text", "", None, "html"):
        return esc(code)
    if lang == "bash":
        out = []
        for line in code.split("\n"):
            m = re.match(r"^(\s*)(.*?)(\s+#.*)?$", line)
            body, comment = m.group(2), m.group(3) or ""
            body = re.sub(r"^(\$ )?(pyweb|pip|python|uvicorn|docker|cd|git|PYWEB_\w+=\S+)",
                          lambda x: x.group(0), body)
            out.append(esc(m.group(1)) + _bash(body) + (f'<span class="c">{esc(comment)}</span>' if comment else ""))
        return "\n".join(out)
    js = lang == "js"
    out = []
    for m in _TOKEN.finditer(code):
        kind = m.lastgroup
        text = m.group(0)
        if kind == "comment":
            if text.startswith("//") and not js:
                out.append(esc(text))
                continue
            if text.startswith("#") and js:
                out.append(esc(text))
                continue
            out.append(f'<span class="c">{esc(text)}</span>')
        elif kind == "string":
            out.append(f'<span class="s">{esc(text)}</span>')
        elif kind == "tag" and not js:
            out.append(f'<span class="t">{esc(text)}</span>')
        elif kind == "deco" and not js:
            out.append(f'<span class="d">{esc(text)}</span>')
        elif kind == "number":
            out.append(f'<span class="n">{esc(text)}</span>')
        elif kind == "name":
            if text in (JS_KW if js else PY_KW):
                out.append(f'<span class="k">{esc(text)}</span>')
            elif not js and text in PY_BUILTIN:
                out.append(f'<span class="b">{esc(text)}</span>')
            else:
                out.append(esc(text))
        else:
            out.append(esc(text))
    return "".join(out)


def _bash(body):
    parts = re.split(r"(\"[^\"]*\"|'[^']*')", body)
    out = []
    for i, p in enumerate(parts):
        if i % 2:
            out.append(f'<span class="s">{esc(p)}</span>')
        else:
            out.append(re.sub(r"^(\s*)(pyweb|pip|python|uvicorn|docker|cd|git)\b",
                              lambda m: m.group(1) + f'<span class="k">{m.group(2)}</span>', esc(p)))
    return "".join(out)


def code_block(code, lang, title=None):
    label = {"pyweb": "app.pyweb", "python": "Python", "bash": "Terminal", "js": "JavaScript",
             "text": "Output", "html": "HTML"}.get(lang, lang or "")
    head = f'<div class="code-head"><span>{esc(title or label)}</span>' \
           f'<button class="copy" type="button" aria-label="Copy code">Copy</button></div>'
    return f'<div class="code">{head}<pre><code class="lang-{esc(lang)}">{highlight(code, lang)}</code></pre></div>'


# ----------------------------------------------------- compiled-output panels

def compiled_panel(source):
    """Tabs showing what the compiler produced for a pyweb snippet."""
    try:
        out = compile_source(source, filename="app.pyweb")
    except Exception as exc:  # noqa: BLE001 - shown, and the docs test fails anyway
        return f'<p class="warn">Compile error: {esc(exc)}</p>'
    tabs = []
    pages = out["pages"]
    js_parts = [f"// {name}.js\n{p['js']}" for name, p in pages.items() if p["js"]]
    if js_parts:
        js = "\n".join(js_parts).replace(
            'import { h as $h, list as $list, when as $when, signal as $signal, computed as $computed, '
            'mount as $mount, onMount as $onMount, py as $py, rpc as $rpc } from "./runtime.js";\n', "")
        size = sum(len(gzip.compress(minify_js(p["js"]).encode(), 9)) for p in pages.values() if p["js"])
        tabs.append(("Browser JS", f"{size} B gzip", code_block(js.strip(), "js")))
    else:
        tabs.append(("Browser JS", "none", '<p class="muted">No JavaScript: nothing on this page changes after it loads.</p>'))
    place = []
    for name, p in pages.items():
        place.append(f"page {name}  route={p['route']}")
        for sym, (loc, why) in p["placement"].items():
            if sym != "__page__":
                place.append(f"  {loc:<8} {sym:<14} {why}")
    for spec in out["rpc"]:
        args = ", ".join(f"{a['name']}: {a['type']}" for a in spec["args"])
        place.append(f"rpc POST /__pyweb/rpc/{spec['name']}  ({args}) -> {spec['returns']}")
    tabs.append(("Placement", "pyweb inspect", code_block("\n".join(place), "text")))
    body = next(iter(pages.values()))["html_body"] if pages else ""
    if body:
        pretty = re.sub(r"(<(?:main|section|form|ul|footer|div|article)[^>]*>|</li>|</p>|</h\d>|</button>)", r"\1\n", body)
        tabs.append(("Server HTML", "first paint", code_block(pretty.strip(), "html")))
    head = "".join(f'<button type="button" role="tab" aria-selected="{"true" if i == 0 else "false"}">'
                   f'{esc(t)} <small>{esc(s)}</small></button>' for i, (t, s, _) in enumerate(tabs))
    panes = "".join(f'<div class="pane"{"" if i == 0 else " hidden"}>{c}</div>' for i, (_, _, c) in enumerate(tabs))
    return (f'<details class="compiled"><summary>What this compiles to</summary>'
            f'<div class="tabs"><div role="tablist">{head}</div>{panes}</div></details>')


# ---------------------------------------------------------------- markdown

def slugify(text):
    s = re.sub(r"<[^>]+>", "", text)
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    return re.sub(r"[\s_]+", "-", s)


def inline(text):
    codes = []

    def keep_code(m):
        codes.append(f"<code>{esc(m.group(1) or m.group(2))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"``\s?(.+?)\s?``|`([^`]+)`", keep_code, text)
    text = esc(text).replace("&#x27;", "'").replace("&quot;", '"')
    text = re.sub(r"&lt;(https?://[^&\s]+)&gt;", r'<a href="\1">\1</a>', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{link(m.group(2))}">{m.group(1)}</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w])_(?!\s)([^_]+?)(?<!\s)_(?![\w])", r"<em>\1</em>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def link(url):
    if re.match(r"^[a-z]+://", url) or url.startswith("#"):
        return url
    path, _, frag = url.partition("#")
    name = os.path.basename(path)
    if name in DOC_SLUG:
        return f"{DOC_SLUG[name]}.html" + (f"#{frag}" if frag else "")
    if path.startswith("../") or path.startswith("examples/") or path.endswith(".md"):
        clean = path.replace("../", "")
        return f"{REPO}/blob/main/{clean}" + (f"#{frag}" if frag else "")
    return url


def markdown(text, *, compile_pyweb=True):
    """Render the Markdown subset used in docs/. Returns (html, title, toc)."""
    lines = text.split("\n")
    out, toc, title = [], [], ""
    i = 0
    para = []

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(p.strip() for p in para))}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            lang = stripped[3:].strip()
            j = i + 1
            buf = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                buf.append(lines[j])
                j += 1
            code = "\n".join(buf)
            indent = len(line) - len(line.lstrip())
            if indent:
                code = "\n".join(c[indent:] if c[:indent].strip() == "" else c for c in buf)
            out.append(code_block(code, lang))
            if lang == "pyweb" and compile_pyweb:
                out.append(compiled_panel(code))
            i = j + 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush()
            level = len(m.group(1))
            content = inline(m.group(2))
            if level == 1:
                title = re.sub(r"<[^>]+>", "", content)
                i += 1
                continue
            anchor = slugify(m.group(2))
            if level <= 3:
                toc.append((level, anchor, re.sub(r"<[^>]+>", "", content)))
            out.append(f'<h{level} id="{anchor}"><a class="anchor" href="#{anchor}">#</a>{content}</h{level}>')
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            flush()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in re.split(r"(?<!\\)\|", lines[j].strip().strip("|"))])
                j += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in header)
            trs = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f'<div class="table"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>')
            i = j
            continue
        if re.match(r"^(\s*)([-*]|\d+\.)\s+", line):
            flush()
            ordered = bool(re.match(r"^\s*\d+\.", line))
            items = []
            j = i
            while j < len(lines):
                lm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[j])
                if lm and len(lm.group(1)) == 0:
                    items.append([lm.group(3)])
                elif lines[j].startswith("  ") and lines[j].strip() and items:
                    if lines[j].strip().startswith("```"):
                        # fenced block inside a list item
                        k = j + 1
                        while k < len(lines) and not lines[k].strip().startswith("```"):
                            k += 1
                        items[-1].append("\x01" + "\n".join(lines[j:k + 1]))
                        j = k
                    else:
                        items[-1].append(lines[j].strip())
                elif not lines[j].strip() and j + 1 < len(lines) and (
                        re.match(r"^([-*]|\d+\.)\s+", lines[j + 1]) or lines[j + 1].startswith("  ")):
                    pass
                else:
                    break
                j += 1
            tag = "ol" if ordered else "ul"
            lis = []
            for item in items:
                text_parts, blocks = [], []
                for part in item:
                    if part.startswith("\x01"):
                        inner, _, _ = markdown(part[1:], compile_pyweb=False)
                        blocks.append(inner)
                    else:
                        text_parts.append(part)
                lis.append(f"<li>{inline(' '.join(text_parts))}{''.join(blocks)}</li>")
            out.append(f"<{tag}>{''.join(lis)}</{tag}>")
            i = j
            continue
        if stripped.startswith(">"):
            flush()
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(buf))}</p></blockquote>")
            continue
        if not stripped:
            flush()
            i += 1
            continue
        para.append(line)
        i += 1
    flush()
    return "\n".join(out), title, toc


# ------------------------------------------------------------------ layout

CSS_VERSION = "1"


def nav(active):
    items = [("introduction.html", "Docs", "docs"), ("examples.html", "Examples", "examples"),
             ("benchmarks.html", "Benchmarks", "benchmarks")]
    links = "".join(f'<a href="{h}"{" aria-current=page" if a == active else ""}>{t}</a>' for h, t, a in items)
    return f"""<header class="top">
  <a class="logo" href="index.html" aria-label="PyWeb home"><span class="mark">py</span>web<small>{__version__}</small></a>
  <nav class="main-nav">{links}</nav>
  <div class="search"><input id="search" type="search" placeholder="Search docs" aria-label="Search documentation" autocomplete="off"><div id="results" class="results" hidden></div></div>
  <button id="theme" class="icon" type="button" aria-label="Toggle dark mode">◐</button>
  <a class="icon gh" href="{REPO}" aria-label="GitHub repository">GitHub</a>
  <button id="menu" class="icon menu" type="button" aria-label="Open navigation">☰</button>
</header>"""


def sidebar(active):
    out = []
    for g in GROUPS:
        links = "".join(
            f'<a href="{slug}.html"{" aria-current=page" if slug == active else ""}>{esc(title)}</a>'
            for _f, slug, title, grp in DOCS if grp == g)
        out.append(f"<div class=group><h4>{g}</h4>{links}</div>")
    ex = "".join(f'<a href="example-{n}.html"{" aria-current=page" if active == "example-" + n else ""}>{esc(t)}</a>'
                 for n, t, _, _ in EXAMPLES)
    out.append(f"<div class=group><h4>Examples</h4>{ex}</div>")
    return f'<aside class="side" id="side">{"".join(out)}</aside>'


def footer():
    return f"""<footer class="foot">
  <div><span class="logo small"><span class="mark">py</span>web</span> {__version__} · MIT licensed</div>
  <div><a href="{REPO}">GitHub</a> · <a href="{REPO}/blob/main/CHANGELOG.md">Changelog</a> · <a href="security.html">Security</a> · <a href="roadmap.html">Roadmap</a></div>
</footer>"""


def shell(name, title, desc, body, *, active="", layout="doc"):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<link rel="canonical" href="{SITE}/{name}">
<link rel="icon" href="assets/icon.svg" type="image/svg+xml">
<link rel="stylesheet" href="assets/style.css?v={CSS_VERSION}">
<script>try{{const t=localStorage.getItem("pyweb-theme");if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
</head>
<body class="{layout}">
{nav(active)}
{body}
{footer()}
<script src="assets/site.js?v={CSS_VERSION}" defer></script>
</body>
</html>
"""


def doc_page(slug, title, toc, content, prev, nxt, source_file):
    toc_html = "".join(f'<a class="l{lvl}" href="#{a}">{esc(t)}</a>' for lvl, a, t in toc)
    pager = '<nav class="pager">'
    if prev:
        pager += f'<a class="prev" href="{prev[0]}.html"><small>Previous</small>{esc(prev[1])}</a>'
    if nxt:
        pager += f'<a class="next" href="{nxt[0]}.html"><small>Next</small>{esc(nxt[1])}</a>'
    pager += "</nav>"
    edit = f'<a class="edit" href="{REPO}/edit/main/docs/{source_file}">Edit this page</a>'
    body = f"""<div class="layout">
{sidebar(slug)}
<main class="content" id="content"><article class="prose"><h1>{esc(title)}</h1>
{content}
</article>{edit}{pager}</main>
<nav class="toc" aria-label="On this page">{"<h4>On this page</h4>" + toc_html if toc else ""}</nav>
</div>"""
    return body


# ----------------------------------------------------------------- examples

def build_demo(name):
    """Compile an example and write a static, self-contained copy under demos/."""
    src_path = os.path.join(ROOT, "examples", name, "app.pyweb")
    with open(src_path, encoding="utf-8") as fh:
        source = fh.read()
    out = compile_source(source, filename="app.pyweb")
    page = next(iter(out["pages"].values()))
    d = os.path.join(OUT, "demos", name)
    os.makedirs(os.path.join(d, "static"), exist_ok=True)
    page_html = page["html"]
    page_html = re.sub(r'(src|href)="/static/', r'\1="static/', page_html)
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page_html)
    with open(os.path.join(d, "static", f"{next(iter(out['pages']))}.js"), "w", encoding="utf-8") as fh:
        fh.write(minify_js(page["js"]))
    with open(os.path.join(ROOT, "pyweb", "runtime", "browser", "runtime.js"), encoding="utf-8") as fh:
        rt = minify_js(fh.read())
    with open(os.path.join(d, "static", "runtime.js"), "w", encoding="utf-8") as fh:
        fh.write(rt)
    static_src = os.path.join(ROOT, "examples", name, "static")
    if os.path.isdir(static_src):
        shutil.copytree(static_src, os.path.join(d, "static"), dirs_exist_ok=True)
    return out


def example_page(name, title, blurb, live):
    with open(os.path.join(ROOT, "examples", name, "app.pyweb"), encoding="utf-8") as fh:
        source = fh.read()
    out = build_demo(name) if live else compile_source(source, filename="app.pyweb")
    pages = out["pages"]
    js = "\n\n".join(f"// {n}.js\n{p['js']}" for n, p in pages.items() if p["js"])
    js_gz = sum(len(gzip.compress(minify_js(p["js"]).encode(), 9)) for p in pages.values() if p["js"])
    place = []
    for pname, p in pages.items():
        place.append(f"page {pname}  route={p['route']}")
        for sym, (loc, why) in p["placement"].items():
            if sym != "__page__":
                place.append(f"  {loc:<8} {sym:<14} {why}")
    for spec in out["rpc"]:
        args = ", ".join(f"{a['name']}: {a['type']}" for a in spec["args"])
        place.append(f"rpc POST /__pyweb/rpc/{spec['name']}  ({args}) -> {spec['returns']}")
    demo = ""
    if live:
        demo = (f'<div class="demo"><div class="demo-bar"><span>Live: this is the example compiled by PyWeb '
                f'({js_gz} B gzip of page code)</span><a href="demos/{name}/index.html" target="_blank" rel="noopener">Open ↗</a></div>'
                f'<iframe src="demos/{name}/index.html" title="{esc(title)} demo" loading="lazy"></iframe></div>')
    else:
        demo = (f'<div class="callout">This example needs its server (database, sessions or server functions). '
                f'Run it locally: {code_block(f"pyweb dev examples/{name}/app.pyweb", "bash")}</div>')
    lines = len(source.splitlines())
    content = f"""<p class="lead">{esc(blurb)}</p>
<ul class="facts"><li><b>{lines}</b> lines of Python</li><li><b>{len(pages)}</b> page{'s' if len(pages) != 1 else ''}</li>
<li><b>{len(out['rpc'])}</b> server function{'s' if len(out['rpc']) != 1 else ''}</li><li><b>{js_gz} B</b> page JS (gzip)</li></ul>
{demo}
<h2 id="source"><a class="anchor" href="#source">#</a>Source</h2>
{code_block(source, "pyweb", f"examples/{name}/app.pyweb")}
<h2 id="placement"><a class="anchor" href="#placement">#</a>What runs where</h2>
<p>Output of <code>pyweb inspect</code>: every page variable, handler and server function, where it runs and why.</p>
{code_block(chr(10).join(place), "text")}
<h2 id="javascript"><a class="anchor" href="#javascript">#</a>Generated JavaScript</h2>
<p>The browser modules the compiler wrote (before minification). They import the shared runtime.</p>
{code_block(js or "// no JavaScript: these pages are static", "js")}"""
    toc = [(2, "source", "Source"), (2, "placement", "What runs where"), (2, "javascript", "Generated JavaScript")]
    return content, toc


# ------------------------------------------------------------------- pages

def landing(bench, demo_gz):
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    sample = re.search(r"```pyweb\n(.*?)```", readme, re.S).group(1)
    install = code_block("pip install pyweb-stack\npyweb new myapp && cd myapp\npyweb dev app.pyweb", "bash")
    rt_gz = bench["counter"]["runtime_js_gzip"]
    counter_gz = bench["counter"]["page_js_gzip"]
    render_ms = bench["todo"]["ssr_ms"]
    feats = [
        ("Interactions run in the browser",
         "Handlers and expressions compile to small JavaScript modules. The network is only used when you call a "
         "<code>@server</code> function: no WebSocket per user, no Python runtime download."),
        ("Every page is server-rendered",
         "Complete HTML on first paint with real data from your database. Good for search engines, slow devices "
         "and links that just work."),
        ("No API layer to write",
         "<code>@server</code> functions get endpoints, argument validation, typed errors and generated browser calls."),
        ("Variables are state",
         "The compiler sees which variables your handlers change and makes exactly those reactive. Updates touch "
         "only the DOM nodes that read them."),
        ("Checked boundaries",
         "Database handles, imports and secrets can't reach browser code: it's a compile error with a line number. "
         "<code>pyweb inspect</code> explains every decision."),
        ("Boring to operate",
         "Stateless servers, signed-cookie sessions, plain JSON over HTTP. Run <code>pyweb serve</code>, uvicorn or "
         "the generated Dockerfile behind any load balancer."),
    ]
    feat_html = "".join(f"<div class=feat><h3>{t}</h3><p>{d}</p></div>" for t, d in feats)
    compare = """<div class="table"><table><thead><tr><th>Approach</th><th>You write</th><th>Trade-off</th></tr></thead><tbody>
<tr><td>Django / Flask / FastAPI + React</td><td>A Python API and a JavaScript app</td><td>Two languages, two builds, a hand-written API in between</td></tr>
<tr><td>Templates + htmx</td><td>Views, templates, an endpoint per interaction</td><td>Every interaction is a round trip; client state is awkward</td></tr>
<tr><td>Reflex, NiceGUI, Streamlit</td><td>Python only</td><td>UI state lives on the server; clicks round-trip over a WebSocket</td></tr>
<tr><td>PyScript / Pyodide</td><td>Python only</td><td>A multi-megabyte runtime before anything is interactive</td></tr>
<tr class="us"><td><b>PyWeb</b></td><td><b>One Python file</b></td><td><b>Browser code is a compiled subset of Python</b></td></tr>
</tbody></table></div>"""
    return f"""<main class="landing">
<section class="hero">
  <div class="hero-text">
    <p class="eyebrow">PyWeb {__version__}</p>
    <h1>Full-stack web apps<br>in one Python file.</h1>
    <p class="sub">Server-rendered pages, reactive UI compiled from Python, and typed calls to server
    functions. No JavaScript toolchain, no WebSocket per user, no runtime download.</p>
    <div class="cta"><a class="btn primary" href="quickstart.html">Get started</a>
    <a class="btn" href="introduction.html">Why PyWeb?</a></div>
    {install}
  </div>
  <div class="hero-code">{code_block(sample, "pyweb", "app.pyweb")}</div>
</section>
<section class="stats">
  <div><b>{rt_gz / 1024:.1f} KB</b><span>shared runtime, gzip, cached</span></div>
  <div><b>{counter_gz} B</b><span>page code for a counter, gzip</span></div>
  <div><b>0 B</b><span>JavaScript on pages that don't change</span></div>
  <div><b>{render_ms:.2f} ms</b><span>server render of the todo page</span></div>
</section>
<section class="band">
  <div class="band-text">
    <h2>Try it: this is a real PyWeb app</h2>
    <p>The todo example, compiled by PyWeb and running right here. Its page code is {demo_gz} bytes gzipped.
    Add a few items, tick some off, switch filters: everything happens in your browser.</p>
    <p><a href="example-todo.html">See its source, placement report and generated JavaScript →</a></p>
  </div>
  <div class="demo"><iframe src="demos/todo/index.html" title="Todo demo" loading="lazy"></iframe></div>
</section>
<section class="how">
  <h2>How it works</h2>
  <ol class="steps">
    <li><b>Write one file.</b> Pages are Python functions with markup. Mark server-only work with <code>@server</code>.</li>
    <li><b>The compiler places your code.</b> Variables your handlers change become signals; handlers become JavaScript;
    server calls become typed RPC; everything else stays on the server.</li>
    <li><b>The server renders HTML.</b> Each request runs the page function, renders real data, and sends only the values
    browser code reads.</li>
    <li><b>The browser takes over.</b> A small module binds every dynamic piece of the page to the state it reads.</li>
  </ol>
</section>
<section class="feats">{feat_html}</section>
<section class="compare">
  <h2>Where it fits</h2>
  <p>PyWeb is for Python developers building internal tools, dashboards, CRUD apps and small products who want a real web
  UI without adopting a JavaScript stack. For large client-heavy single-page apps, use a JavaScript framework; for
  scientific Python in the browser, use Pyodide. <a href="introduction.html">Read the full comparison →</a></p>
  {compare}
</section>
<section class="final">
  <h2>Build something in the next five minutes</h2>
  <div class="cta"><a class="btn primary" href="tutorial.html">Follow the tutorial</a><a class="btn" href="examples.html">Browse examples</a></div>
</section>
</main>"""


def examples_index():
    cards = []
    for name, title, blurb, live in EXAMPLES:
        tag = '<span class="pill live">live demo</span>' if live else '<span class="pill">needs server</span>'
        cards.append(f'<a class="card" href="example-{name}.html"><h3>{esc(title)} {tag}</h3><p>{esc(blurb)}</p>'
                     f'<code>examples/{name}/app.pyweb</code></a>')
    return f"""<p class="lead">Every example is a single <code>app.pyweb</code> file in the repository, and every one is
driven in a real browser by the test suite. Each page shows the source, where the compiler placed each name, and the
JavaScript it generated.</p><div class="cards">{''.join(cards)}</div>"""


def benchmarks_page(bench, example_sizes):
    rows = "".join(
        f"<tr><td>{esc(r['app'])}</td><td>{r['source_lines']}</td><td>{r['page_js_gzip']} B</td>"
        f"<td>{r['js_gzip']} B</td><td>{r['html_bytes']} B</td><td>{r['compile_ms']:.1f} ms</td><td>{r['ssr_ms']:.3f} ms</td></tr>"
        for r in bench.values())
    ex_rows = "".join(
        f"<tr><td><a href='example-{n}.html'>{n}</a></td><td>{s['pages']}</td><td>{s['page_gz']} B</td><td>{s['interactive']}</td></tr>"
        for n, s in example_sizes.items())
    rt = bench["counter"]
    return f"""<p class="lead">Numbers generated by <code>python -m pyweb.bench</code> and <code>pyweb build</code> when this site
was built. They measure PyWeb itself; they are not a comparison with other frameworks.</p>
<h2 id="shipped"><a class="anchor" href="#shipped">#</a>What ships to the browser</h2>
<ul class="facts"><li><b>{rt['runtime_js_bytes']:,} B</b> shared runtime, minified</li><li><b>{rt['runtime_js_gzip']:,} B</b> shared runtime, gzip</li></ul>
<p>The runtime is downloaded once and cached across pages (production builds content-hash it). Each interactive page adds
its own module; pages with nothing dynamic ship no JavaScript at all.</p>
<div class="table"><table><thead><tr><th>Benchmark app</th><th>Lines</th><th>Page JS (gzip)</th><th>Total JS incl. runtime (gzip)</th>
<th>HTML</th><th>Compile</th><th>Server render</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2 id="examples"><a class="anchor" href="#examples">#</a>Example apps</h2>
<div class="table"><table><thead><tr><th>Example</th><th>Pages</th><th>Page JS, all pages (gzip)</th><th>Interactive pages</th></tr></thead>
<tbody>{ex_rows}</tbody></table></div>
<h2 id="method"><a class="anchor" href="#method">#</a>Method</h2>
<ul><li>Sizes are of minified output (the production build's token-aware minifier) compressed with gzip level 9.</li>
<li>Server render time is the mean of 50 renders of the page through the same code path <code>pyweb serve</code> uses, in one process, on the machine that built this site.</li>
<li>Compile time is a single cold compile of the source.</li></ul>"""


# -------------------------------------------------------------------- build

def write(name, text):
    path = os.path.join(OUT, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def build(out=None):
    global OUT
    if out is not None:
        OUT = out
    from pyweb import bench as _bench
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "assets"))
    for asset in ("style.css", "site.js", "icon.svg"):
        shutil.copy(os.path.join(HERE, "assets", asset), os.path.join(OUT, "assets", asset))
    write(".nojekyll", "")
    search = []
    pages = []

    bench = _bench.run()
    # Docs
    for idx, (fname, slug, title, group) in enumerate(DOCS):
        with open(os.path.join(ROOT, "docs", fname), encoding="utf-8") as fh:
            text = fh.read()
        content, h1, toc = markdown(text)
        prev = (DOCS[idx - 1][1], DOCS[idx - 1][2]) if idx > 0 else None
        nxt = (DOCS[idx + 1][1], DOCS[idx + 1][2]) if idx + 1 < len(DOCS) else None
        body = doc_page(slug, h1 or title, toc, content, prev, nxt, fname)
        desc = re.sub(r"<[^>]+>", "", content.split("</p>")[0])[:180]
        write(f"{slug}.html", shell(f"{slug}.html", f"{h1 or title} · PyWeb", desc, body, active="docs"))
        search.append({"url": f"{slug}.html", "title": h1 or title, "group": group,
                       "headings": [[a, t] for _, a, t in toc],
                       "text": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content))[:6000]})
        pages.append(f"{slug}.html")

    # Examples
    example_sizes = {}
    demo_gz = 0
    for name, title, blurb, live in EXAMPLES:
        content, toc = example_page(name, title, blurb, live)
        body = doc_page(f"example-{name}", f"Example: {title}", toc, content, None, None, "../examples")
        body = body.replace(f'href="{REPO}/edit/main/docs/../examples"', f'href="{REPO}/blob/main/examples/{name}/app.pyweb"')
        write(f"example-{name}.html", shell(f"example-{name}.html", f"{title} example · PyWeb", blurb, body, active="examples"))
        pages.append(f"example-{name}.html")
        with open(os.path.join(ROOT, "examples", name, "app.pyweb"), encoding="utf-8") as fh:
            out = compile_source(fh.read(), filename="app.pyweb")
        gz = sum(len(gzip.compress(minify_js(p["js"]).encode(), 9)) for p in out["pages"].values() if p["js"])
        example_sizes[name] = {"pages": len(out["pages"]), "page_gz": gz,
                               "interactive": sum(1 for p in out["pages"].values() if p["js"])}
        if name == "todo":
            demo_gz = gz
        search.append({"url": f"example-{name}.html", "title": f"Example: {title}", "group": "Examples",
                       "headings": [], "text": blurb})

    body = f'<div class="layout">{sidebar("examples")}<main class="content"><article class="prose"><h1>Examples</h1>{examples_index()}</article></main><nav class="toc"></nav></div>'
    write("examples.html", shell("examples.html", "Examples · PyWeb", "Complete PyWeb apps with live demos and compiler output.", body, active="examples"))
    pages.append("examples.html")

    bench_html = benchmarks_page(bench, example_sizes)
    body = f'<div class="layout">{sidebar("benchmarks")}<main class="content"><article class="prose"><h1>Benchmarks</h1>{bench_html}</article></main><nav class="toc"></nav></div>'
    write("benchmarks.html", shell("benchmarks.html", "Benchmarks · PyWeb", "What PyWeb ships and how fast it renders.", body, active="benchmarks"))
    pages.append("benchmarks.html")

    write("index.html", shell("", "PyWeb: full-stack web apps in one Python file",
                              "Server-rendered pages, reactive UI compiled from Python, and typed server calls. "
                              "No JavaScript toolchain.", landing(bench, demo_gz), active="home", layout="home"))
    pages.insert(0, "index.html")

    body = ('<main class="landing"><section class="final"><h1>Page not found</h1><p>That page doesn\'t exist. '
            'Try the <a href="introduction.html">docs</a> or the search box above.</p></section></main>')
    write("404.html", shell("404.html", "Not found · PyWeb", "Page not found.", body, layout="home"))

    write("search.json", json.dumps(search, separators=(",", ":")))
    urls = "".join(f"<url><loc>{SITE}/{p if p != 'index.html' else ''}</loc></url>" for p in pages)
    write("sitemap.xml", f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
    write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n")
    return pages


if __name__ == "__main__":
    built = build()
    print(f"built {len(built)} pages -> {os.path.relpath(OUT)}")
