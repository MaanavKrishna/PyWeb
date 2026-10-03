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
import zipfile

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
    ("17-ai-assistants.md", "ai-assistants", "AI assistants & MCP", "Start"),
    ("04-pyweb-files.md", "language", "The .pyweb language", "Language"),
    ("05-reactivity.md", "reactivity", "State & reactivity", "Language"),
    ("07-browser-python.md", "browser-python", "Python in the browser", "Language"),
    ("18-npm-packages.md", "npm", "npm packages", "Language"),
    ("06-server-functions.md", "server-functions", "Server functions & RPC", "Server"),
    ("08-pages-routing-assets.md", "routing", "Pages, routing & assets", "Server"),
    ("19-layouts-navigation.md", "layouts", "Layouts & navigation", "Server"),
    ("20-ai-apps.md", "ai-apps", "Building AI apps", "Server"),
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
    ("chat", "Chat rooms", "Route parameters, shared server state and live updates over Server-Sent Events.", False),
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


def code_block(code, lang, title=None, *, window=False, body=None, extra=""):
    label = {"pyweb": "app.pyweb", "python": "Python", "bash": "Terminal", "js": "JavaScript",
             "text": "Output", "html": "HTML"}.get(lang, lang or "")
    dots = '<span class="dots"><i></i><i></i><i></i></span>' if window else ""
    head = f'<div class="code-head">{dots}<span>{esc(title or label)}</span>' \
           f'<button class="copy" type="button" aria-label="Copy code">Copy</button></div>'
    if body is None:
        body = f'<pre><code class="lang-{esc(lang)}">{highlight(code, lang)}</code></pre>'
    return f'<div class="code">{head}{body}{extra}</div>'


def line_roles(source):
    """1-based line -> "browser" | "server" | "both", from what the compiler decided."""
    import ast
    from pyweb.compiler import parser as P
    out = compile_source(source, filename="app.pyweb")
    tree = ast.parse(P.split_sources(source)[0])
    fns = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    roles = {}

    def mark(a, b, role):
        for ln in range(a, b + 1):
            roles.setdefault(ln, role)

    for spec in out["rpc"]:
        node = fns[spec["name"]]
        start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
        mark(start, node.end_lineno, "server")
    lines = source.split("\n")
    for page in out["pages"].values():
        info = page["info"]
        for node in info.handlers.values():
            mark(node.lineno, node.end_lineno, "browser")
        for name, init in info.inits.items():
            where = page["placement"].get(name, ("", ""))[0]
            if where in ("browser", "server"):
                roles.setdefault(init.lineno, where)
        first_ui = min((n.line for n in info.ui), default=None)
        if first_ui:
            for ln in range(first_ui, info.node.end_lineno + 1):
                if lines[ln - 1].strip():
                    roles.setdefault(ln, "both")
    return roles


def annotated_block(source, title):
    """A code window whose lines carry a rail colour for where they run."""
    roles = line_roles(source)
    html_lines = highlight(source.rstrip("\n"), "pyweb").split("\n")
    body = "".join(f'<span class="ln {roles.get(i, "")}">{text or " "}</span>'
                   for i, text in enumerate(html_lines, 1))
    legend = ('<div class="legend"><span class="browser">Runs in the browser</span>'
              '<span class="server">Runs on the server</span>'
              '<span class="both">Rendered on the server, updated in the browser</span></div>')
    return code_block(source, "pyweb", title, window=True,
                      body=f'<pre class="annotated"><code class="lang-pyweb">{body}</code></pre>', extra=legend)


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
        js = re.sub(r'^import \{[^}]*\} from "\./runtime\.js";\n', "", "\n".join(js_parts), flags=re.M)
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

CSS_VERSION = "3"

LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="pwg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#3d5afe"/><stop offset=".55" stop-color="#8b5cf6"/><stop offset="1" stop-color="#f2a10c"/>'
        '</linearGradient></defs><rect width="32" height="32" rx="9" fill="url(#pwg)"/>'
        '<path d="M10.5 10.5 5.5 16l5 5.5M21.5 10.5l5 5.5-5 5.5" stroke="#fff" stroke-width="2.6" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/><circle cx="16" cy="16" r="3.3" fill="#fff"/></svg>')

_ICON_PATHS = {
    "browser": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/>',
    "server": '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "link": '<path d="M9 12h6M10 7H8a5 5 0 0 0 0 10h2M14 7h2a5 5 0 0 1 0 10h-2"/>',
    "shield": '<path d="M12 3 4.5 6v6c0 4.6 3.2 7.8 7.5 9 4.3-1.2 7.5-4.4 7.5-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "pulse": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "box": '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/>',
    "github": '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12 12 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "edit": '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
}


def icon(name):
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{_ICON_PATHS[name]}</svg>')

#: Pyodide (CPython for WebAssembly) for the playground. PYWEB_PYODIDE_DIR
#: points at an unpacked `pyodide` npm package to serve it from the site
#: instead (tests do this to run offline).
PYODIDE_VERSION = "0.29.5"
PYODIDE_CDN = f"https://cdn.jsdelivr.net/npm/pyodide@{PYODIDE_VERSION}/"
PLAYGROUND_EXAMPLES = [("counter", "Counter"), ("todo", "Todo list"), ("showcase", "Showcase: server search"),
                       ("auth", "Sign-in and sessions"), ("chat", "Chat with live updates")]


def nav(active):
    items = [("introduction.html", "Docs", "docs"), ("examples.html", "Examples", "examples"),
             ("playground.html", "Playground", "playground"), ("benchmarks.html", "Benchmarks", "benchmarks")]
    links = "".join(f'<a href="{h}"{" aria-current=page" if a == active else ""}>{t}</a>' for h, t, a in items)
    return f"""<header class="top" id="top">
  <a class="logo" href="index.html" aria-label="PyWeb home"><span class="mark">{LOGO}</span>pyweb<small>v{__version__}</small></a>
  <nav class="main-nav">{links}</nav>
  <div class="search"><input id="search" type="search" placeholder="Search docs" aria-label="Search documentation" autocomplete="off"><kbd>/</kbd><div id="results" class="results" hidden></div></div>
  <button id="theme" class="icon" type="button" aria-label="Toggle dark mode"><span class="i-sun">{icon("sun")}</span><span class="i-moon">{icon("moon")}</span></button>
  <a class="icon gh" href="{REPO}" aria-label="GitHub repository">{icon("github")}<span>GitHub</span></a>
  <button id="menu" class="icon menu" type="button" aria-label="Open navigation">{icon("menu")}</button>
</header>"""


def sidebar(active):
    out = []
    for g in GROUPS:
        links = "".join(
            f'<a href="{slug}.html"{" aria-current=page" if slug == active else ""}>{esc(title)}</a>'
            for _f, slug, title, grp in DOCS if grp == g)
        out.append(f"<div class=group><h4>{g}</h4>{links}</div>")
    out[-1] = out[-1].replace("</div>", f'<a href="changelog.html"{" aria-current=page" if active == "changelog" else ""}>Changelog</a></div>')
    ex = "".join(f'<a href="example-{n}.html"{" aria-current=page" if active == "example-" + n else ""}>{esc(t)}</a>'
                 for n, t, _, _ in EXAMPLES)
    out.append(f"<div class=group><h4>Examples</h4>{ex}</div>")
    return f'<aside class="side" id="side">{"".join(out)}</aside>'


def footer():
    return f"""<footer class="foot">
  <div class="foot-inner">
    <div><a class="logo small" href="index.html"><span class="mark">{LOGO}</span>pyweb</a>
      <p>Full-stack web apps in one Python file: server-rendered pages, reactive UI compiled from Python, typed server calls.</p></div>
    <nav aria-label="Learn"><h4>Learn</h4><a href="quickstart.html">Quickstart</a><a href="tutorial.html">Tutorial</a>
      <a href="examples.html">Examples</a><a href="playground.html">Playground</a></nav>
    <nav aria-label="Reference"><h4>Reference</h4><a href="language.html">The .pyweb language</a><a href="cli.html">Command line</a>
      <a href="ai-assistants.html">AI assistants &amp; MCP</a><a href="benchmarks.html">Benchmarks</a></nav>
    <nav aria-label="Project"><h4>Project</h4><a href="{REPO}">GitHub</a><a href="changelog.html">Changelog</a>
      <a href="roadmap.html">Roadmap</a><a href="security.html">Security</a></nav>
  </div>
  <div class="foot-base"><span>pyweb {__version__} · MIT licensed</span><span>Made with Python, compiled for the web.</span></div>
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
<meta name="theme-color" content="#0b0c12" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#fafaf7" media="(prefers-color-scheme: light)">
<link rel="preload" href="assets/fonts/inter.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="assets/fonts/bricolage-grotesque.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="assets/style.css?v={CSS_VERSION}">
<script>document.documentElement.classList.add("js");try{{const t=localStorage.getItem("pyweb-theme");if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
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
    edit = f'<a class="edit" href="{REPO}/edit/main/docs/{source_file}">{icon("edit")} Edit this page on GitHub</a>'
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
    # An autofocused input inside the embedded demo would scroll the landing page.
    source = re.sub(r"\s+autofocus(?=[\s/>])", "", source)
    if name == "todo":  # start the demo with a few items rather than an empty list
        source = source.replace("todos = []", 'todos = [{"id": 1, "title": "Write one Python file", "done": True}, '
                                '{"id": 2, "title": "Run pyweb dev", "done": False}, '
                                '{"id": 3, "title": "Ship it", "done": False}]', 1)
        source = source.replace("next_id = 1", "next_id = 4", 1)
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
    with open(os.path.join(ROOT, "pyweb", "runtime", "browser", "markdown.js"), encoding="utf-8") as fh:
        md = minify_js(fh.read())
    with open(os.path.join(d, "static", "markdown.js"), "w", encoding="utf-8") as fh:
        fh.write(md)
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
        demo = (f'<div class="demo"><div class="demo-bar"><span class="dots"><i></i><i></i><i></i></span>'
                f'<span class="url">localhost:8000 · live, {js_gz} B of page code</span>'
                f'<a href="demos/{name}/index.html" target="_blank" rel="noopener">Open ↗</a></div>'
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
    compiled = compile_source(sample, filename="app.pyweb")
    page = next(iter(compiled["pages"].values()))
    # The browser half: the handler exactly as the compiler wrote it.
    handler = re.search(r"\n(  (?:async )?function submit\(\)[\s\S]*?\n  \})", page["js"]).group(1)
    handler = "\n".join(line[2:] for line in handler.split("\n"))
    page_gz = len(gzip.compress(minify_js(page["js"]).encode(), 9))
    # The server half: a real request and RPC call against the sample app.
    from pyweb.testing import TestClient
    client = TestClient(source=sample)
    html_kb = len(client.get("/").body) / 1024
    reply = json.dumps({"result": client.rpc("sign", name="Ada")})
    server_demo = (f"GET /\n  → runs Home(), renders {html_kb:.1f} KB of HTML\n\n"
                   f'POST /__pyweb/rpc/sign\n  {{"args": {{"name": "Ada"}}}}\n  → {reply}')

    mcp_setup = code_block("pip install pyweb-stack\nclaude mcp add pyweb -- pyweb mcp", "bash", "Claude Code", window=True) + \
        code_block('{\n  "mcpServers": {\n    "pyweb": { "command": "pyweb", "args": ["mcp"] }\n  }\n}', "text",
                   "Cursor · Claude Desktop · VS Code", window=True)
    rt_gz = bench["counter"]["runtime_js_gzip"]
    counter_gz = bench["counter"]["page_js_gzip"]
    render_ms = bench["todo"]["ssr_ms"]
    feats = [
        ("browser", "browser", "Interactions run in the browser",
         "Handlers and expressions compile to small JavaScript modules. The network is only used when you call a "
         "<code>@server</code> function: no WebSocket per user, no Python runtime download."),
        ("server", "server", "Every page is server-rendered",
         "Complete HTML on first paint with real data from your database, then hydrated in place. Good for search "
         "engines, slow devices and links that just work."),
        ("link", "both", "No API layer to write",
         "<code>@server</code> functions get endpoints, argument validation, typed errors and generated browser calls."),
        ("pulse", "browser", "Variables are state",
         "The compiler sees which variables your handlers change and makes exactly those reactive. Updates touch "
         "only the DOM nodes that read them."),
        ("shield", "server", "Checked boundaries",
         "Database handles, imports and secrets can't reach browser code: it's a compile error with a line number. "
         "<code>pyweb inspect</code> explains every decision."),
        ("box", "both", "Boring to operate",
         "Stateless servers, signed-cookie sessions, plain JSON over HTTP. Run <code>pyweb serve</code>, uvicorn or "
         "the generated Dockerfile behind any load balancer."),
    ]
    feat_html = "".join(f'<div class="feat reveal"><span class="chip {cls}">{icon(ic)}</span><h3>{t}</h3><p>{d}</p></div>'
                        for ic, cls, t, d in feats)
    tools = [
        ("playground.html", "Playground", "Write an app and run it in your browser, server functions included.", "Open it"),
        ("cli.html#editor-support", "VS Code &amp; LSP", "Errors as you type, and hover that shows where each line runs.",
         "Set up your editor"),
        ("ai-assistants.html", "AI assistants", "An MCP server to scaffold, check, render, screenshot and test.",
         "Connect an assistant"),
        ("server-functions.html#live-updates", "Live updates", "<code>publish()</code> on the server, "
         "<code>subscribe()</code> in the page, over Server-Sent Events.", "Read the guide"),
    ]
    tool_html = "".join(f'<a class="tool reveal" href="{h}"><b>{t}</b><span>{d}</span><em>{c} →</em></a>'
                        for h, t, d, c in tools)
    compare = """<div class="table"><table><thead><tr><th>Approach</th><th>You write</th><th>Trade-off</th></tr></thead><tbody>
<tr><td>Django / Flask / FastAPI + React</td><td>A Python API and a JavaScript app</td><td>Two languages, two builds, a hand-written API in between</td></tr>
<tr><td>Templates + htmx</td><td>Views, templates, an endpoint per interaction</td><td>Every interaction is a round trip; client state is awkward</td></tr>
<tr><td>Reflex, NiceGUI, Streamlit</td><td>Python only</td><td>UI state lives on the server; clicks round-trip over a WebSocket</td></tr>
<tr><td>PyScript / Pyodide</td><td>Python only</td><td>A multi-megabyte runtime before anything is interactive</td></tr>
<tr class="us"><td><b>PyWeb</b></td><td><b>One Python file</b></td><td><b>Browser code is a compiled subset of Python</b></td></tr>
</tbody></table></div>"""
    return f"""<main class="landing">
<div class="hero-wrap">
<section class="hero">
  <div class="hero-text">
    <a class="eyebrow" href="changelog.html"><b>New</b> {__version__}: hydration, live updates, playground →</a>
    <h1>Full-stack web apps in <span class="grad">one Python file.</span></h1>
    <p class="sub">Server-rendered pages, reactive UI compiled from Python, and typed calls to server functions.
    No JavaScript toolchain, no WebSocket per user, no runtime download.</p>
    <div class="cta"><a class="btn primary" href="quickstart.html">Get started <span class="arrow">→</span></a>
    <a class="btn" href="playground.html">Try it in your browser</a></div>
    <div class="install code"><span class="prompt">$</span><code>pip install pyweb-stack</code>
    <button class="copy" type="button" aria-label="Copy install command">Copy</button></div>
  </div>
  <div class="hero-code">{annotated_block(sample, "app.pyweb")}</div>
</section>
</div>
<section class="stats reveal">
  <div><b>{rt_gz / 1024:.1f} KB</b><span>shared runtime, gzip, cached</span></div>
  <div><b>{counter_gz} B</b><span>page code for a counter</span></div>
  <div><b>0 B</b><span>JavaScript on static pages</span></div>
  <div><b>{render_ms * 1000:.0f} µs</b><span>to server-render the todo page</span></div>
</section>
<section class="places">
  <div class="section-head center reveal"><div class="kicker">How it works</div>
    <h2>One file. The compiler puts each line where it belongs.</h2>
    <p>Write pages as Python functions with markup. PyWeb splits them: handlers become a tiny browser module,
    everything else stays on your server. This is the output for the app above.</p></div>
  <div class="split">
    <div class="place browser reveal"><h3><span class="chip">{icon("browser")}</span>In the browser</h3>
      <p>A {page_gz} B module (gzip) plus the shared runtime.</p>
      <ul><li>Variables your handlers change become signals</li><li>Handlers compile to JavaScript with Python semantics</li>
      <li>Hydrates the server's HTML in place: nothing is rebuilt</li></ul>
      {code_block(handler, "js", "Home.js · compiled from submit()")}</div>
    <div class="place server reveal"><h3><span class="chip">{icon("server")}</span>On the server</h3>
      <p>Plain Python, per request: your database, files and secrets stay here.</p>
      <ul><li>Renders complete HTML with real data</li><li><code>@server</code> functions become validated JSON endpoints</li>
      <li>Sends only the values browser code reads</li></ul>
      {code_block(server_demo, "text", "HTTP · a real request to the app above")}</div>
  </div>
</section>
<section class="band">
  <div class="band-text reveal"><div class="kicker">Live demo</div>
    <h2>This is a real PyWeb app</h2>
    <p>The todo example (started with three items), compiled by PyWeb and running right here: {demo_gz} bytes of page code.</p>
    <ul class="checks"><li>Add items, tick them off, switch filters</li><li>Everything updates locally, with no network round trips</li>
    <li>The same file renders the first paint on the server</li></ul>
    <a class="more" href="example-todo.html">See its source and generated JavaScript →</a>
  </div>
  <div class="demo reveal"><div class="demo-bar"><span class="dots"><i></i><i></i><i></i></span><span class="url">localhost:8000</span></div>
    <iframe src="demos/todo/index.html" title="Todo demo" loading="lazy"></iframe></div>
</section>
<section>
  <div class="section-head reveal"><div class="kicker">Why PyWeb</div><h2>The good parts of modern web stacks, in Python</h2></div>
  <div class="feats">{feat_html}</div>
</section>
<section>
  <div class="section-head reveal"><div class="kicker">Tooling</div><h2>Everything around the compiler</h2></div>
  <div class="tools">{tool_html}</div>
</section>
<section class="band ai">
  <div class="band-text reveal"><div class="kicker">AI assistants</div>
    <h2>Built for building with AI</h2>
    <p>One file, plain Python, and a compiler that answers mistakes with a line number and a fix. The built-in MCP
    server lets Claude Code, Cursor and other assistants scaffold apps, check them, see what runs where, render
    pages, call server functions, look at the result in a real browser and run the tests.</p>
    <ul class="checks"><li>New projects include <code>AGENTS.md</code>, <code>CLAUDE.md</code> and a starter test</li>
    <li>The docs ship as <a href="llms-full.txt">llms-full.txt</a></li></ul>
    <a class="more" href="ai-assistants.html">Set it up →</a>
  </div>
  <div class="reveal">{mcp_setup}</div>
</section>
<section class="compare">
  <div class="section-head reveal"><div class="kicker">Where it fits</div><h2>Made for Python developers who ship web UIs</h2>
  <p>Internal tools, dashboards, CRUD apps and small products, without adopting a JavaScript stack. For large
  client-heavy single-page apps, use a JavaScript framework; for scientific Python in the browser, use Pyodide.
  <a href="introduction.html">Read the full comparison →</a></p></div>
  <div class="reveal">{compare}</div>
</section>
<section class="final">
  <div class="panel reveal"><h2>Build something in the next five minutes</h2>
    <p>Install it, scaffold an app, and open it in your browser. Or skip the install and use the playground.</p>
    <div class="cta"><a class="btn primary" href="tutorial.html">Follow the tutorial <span class="arrow">→</span></a>
    <a class="btn" href="playground.html">Open the playground</a></div></div>
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

def playground_page():
    """playground.html plus the files it loads: PyWeb as a zip, the runtime, the host module."""
    base = os.path.join(OUT, "playground")
    os.makedirs(base, exist_ok=True)
    with zipfile.ZipFile(os.path.join(base, "pyweb.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(os.path.join(ROOT, "pyweb")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, ROOT))
    shutil.copy(os.path.join(ROOT, "pyweb", "runtime", "browser", "runtime.js"), os.path.join(base, "runtime.js"))
    shutil.copy(os.path.join(HERE, "playground", "host.py"), os.path.join(base, "host.py"))
    static = {}
    for name, _title in PLAYGROUND_EXAMPLES:
        folder = os.path.join(ROOT, "examples", name, "static")
        for fn in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            static.setdefault(fn, os.path.join(folder, fn))
    os.makedirs(os.path.join(base, "static"), exist_ok=True)
    for fn, src in static.items():
        shutil.copy(src, os.path.join(base, "static", fn))
    pyodide = PYODIDE_CDN
    local = os.environ.get("PYWEB_PYODIDE_DIR")
    if local:
        shutil.copytree(local, os.path.join(OUT, "pyodide"), dirs_exist_ok=True)
        pyodide = "pyodide/"
    examples = {}
    for name, title in PLAYGROUND_EXAMPLES:
        with open(os.path.join(ROOT, "examples", name, "app.pyweb"), encoding="utf-8") as fh:
            examples[name] = {"title": title, "source": fh.read()}
    config = json.dumps({"pyodide": pyodide, "base": "playground/", "static": sorted(static),
                         "examples": examples}).replace("</", "<\\/")
    return f"""<main class="playground">
<div class="pg-bar">
  <h1>Playground</h1>
  <label class="pg-pick"><span>Example</span><select id="pg-example"><option value="" hidden>Shared code</option></select></label>
  <button id="pg-run" class="btn primary" type="button" title="Ctrl/⌘ + Enter">Run</button>
  <button id="pg-share" class="btn" type="button">Copy link</button>
  <span id="pg-status" class="pg-status busy" role="status">Starting…</span>
</div>
<div class="pg-split">
  <section class="pg-editor" aria-label="Editor">
    <div class="pg-file">app.pyweb</div>
    <div class="pg-code"><pre id="pg-gutter" aria-hidden="true"></pre><textarea id="pg-source" spellcheck="false" autocapitalize="off" autocomplete="off" aria-label="app.pyweb source"></textarea></div>
    <div id="pg-error" class="pg-error" role="alert" hidden></div>
  </section>
  <section class="pg-output" aria-label="Output">
    <div class="pg-tabs" role="tablist">
      <button type="button" role="tab" data-tab="preview" aria-selected="true">Preview</button>
      <button type="button" role="tab" data-tab="js" aria-selected="false">JavaScript</button>
      <button type="button" role="tab" data-tab="place" aria-selected="false">What runs where</button>
    </div>
    <div class="pg-urlbar"><select id="pg-page" aria-label="Page"></select><input id="pg-url" aria-label="Path" value="/"></div>
    <iframe id="pg-frame" title="App preview"></iframe>
    <pre id="pg-js" class="pg-panel" hidden></pre>
    <div id="pg-place" class="pg-panel" hidden></div>
  </section>
</div>
<p class="pg-note">Everything runs in your browser: PyWeb's compiler, server rendering and your <code>@server</code>
functions run in real Python (<a href="https://pyodide.org">Pyodide</a>, CPython compiled to WebAssembly). Nothing
is sent anywhere. Apps that need a database file or other packages run with <code>pyweb dev</code>; see the
<a href="quickstart.html">quickstart</a>.</p>
</main>
<script id="pg-config" type="application/json">{config}</script>
<script src="assets/playground.js?v={CSS_VERSION}" defer></script>"""


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
    for asset in ("style.css", "site.js", "icon.svg", "playground.js"):
        shutil.copy(os.path.join(HERE, "assets", asset), os.path.join(OUT, "assets", asset))
    shutil.copytree(os.path.join(HERE, "assets", "fonts"), os.path.join(OUT, "assets", "fonts"))
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

    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
        log_html, _t, log_toc = markdown(fh.read(), compile_pyweb=False)
    log_toc = [t for t in log_toc if t[0] == 2 and t[2] != "[Unreleased]"]
    log_html = re.sub(r'<h2 id="unreleased">.*?</h2>\s*', "", log_html)
    body = doc_page("changelog", "Changelog", log_toc, log_html, None, None, "../CHANGELOG.md")
    body = body.replace(f'href="{REPO}/edit/main/docs/../CHANGELOG.md"', f'href="{REPO}/edit/main/CHANGELOG.md"')
    write("changelog.html", shell("changelog.html", "Changelog · PyWeb", "What changed in each PyWeb release.",
                                  body, active="docs"))
    pages.append("changelog.html")
    search.append({"url": "changelog.html", "title": "Changelog", "group": "Reference",
                   "headings": [[a, t] for _, a, t in log_toc], "text": re.sub(r"<[^>]+>", " ", log_html)[:6000]})

    write("playground.html", shell("playground.html", "Playground · PyWeb",
                                   "Write a PyWeb app and run it in your browser: compiler, server rendering and "
                                   "server functions included.", playground_page(), active="playground",
                                   layout="home wide"))
    pages.append("playground.html")
    search.append({"url": "playground.html", "title": "Playground", "group": "Start", "headings": [],
                   "text": "Run PyWeb apps in the browser. Edit code, see the preview, compiled JavaScript and "
                           "what runs where."})

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
    write_llms_txt()
    return pages


def write_llms_txt():
    """llms.txt (index) and llms-full.txt (AI guide + every docs page) for AI tools."""
    with open(os.path.join(ROOT, "pyweb", "ai", "guide.md"), encoding="utf-8") as fh:
        guide = fh.read()
    index = ["# PyWeb", "",
             "> Full-stack web apps in one Python file: server-rendered pages, reactive UI compiled from "
             "Python to JavaScript, and typed RPC to @server functions. Install with `pip install pyweb-stack`; "
             "import `pyweb`; command `pyweb`. MCP server: `pyweb mcp`.", "",
             "## Start here", "",
             f"- [AI guide (all rules in one page)]({SITE}/llms-full.txt): read this before writing .pyweb code", ""]
    full = [guide.rstrip(), ""]
    for group in GROUPS:
        index += [f"## {group}", ""]
        for fname, slug, title, grp in DOCS:
            if grp != group:
                continue
            with open(os.path.join(ROOT, "docs", fname), encoding="utf-8") as fh:
                text = fh.read()
            first = next((ln for ln in text.split("\n")[1:] if ln.strip() and not ln.startswith(("#", "`", "|"))), "")
            index.append(f"- [{title}]({SITE}/{slug}.html): {first.strip()}")
            full += ["", "---", "", text.rstrip()]
        index.append("")
    index += ["## Examples", ""] + [f"- [{t}]({SITE}/example-{n}.html): {b}" for n, t, b, _ in EXAMPLES]
    write("llms.txt", "\n".join(index) + "\n")
    write("llms-full.txt", "\n".join(full) + "\n")


if __name__ == "__main__":
    built = build(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"built {len(built)} pages -> {os.path.relpath(OUT)}")
