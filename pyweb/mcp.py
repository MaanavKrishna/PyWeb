"""MCP server for PyWeb: lets AI assistants build, check and run PyWeb apps.

Run ``pyweb mcp`` (stdio transport). Register it with an AI tool, e.g.::

    claude mcp add pyweb -- pyweb mcp                 # Claude Code
    {"mcpServers": {"pyweb": {"command": "pyweb", "args": ["mcp"]}}}   # Cursor / Claude Desktop

Tools (all paths are relative to the directory the server was started in):

* ``pyweb_guide``        the rules for writing `.pyweb` apps (read this first)
* ``pyweb_new_app``      scaffold an app from a template (+ AGENTS.md/CLAUDE.md)
* ``pyweb_check``        compile + security checks; errors with line numbers and fix hints
* ``pyweb_inspect``      what runs in the browser vs the server, and why
* ``pyweb_compiled``     the generated JavaScript / server-rendered HTML for a page
* ``pyweb_render``       request a URL from the app (server-side render, redirects, errors)
* ``pyweb_call``         call an ``@server`` function like the browser does (cookies persist)

Implemented with the standard library only (JSON-RPC 2.0 over stdin/stdout).
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import re
import sys
import traceback

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = ("blank", "counter", "todo", "blog", "auth", "chat")

INSTRUCTIONS = (
    "PyWeb builds full-stack web apps from one .pyweb file (Python + markup). "
    "Before writing PyWeb code, call pyweb_guide. After every edit, call pyweb_check and fix "
    "errors by line number using the hints. Use pyweb_inspect to see what runs in the browser "
    "vs the server, and pyweb_render / pyweb_call to verify behaviour."
)

ERROR_HINTS = [
    ("only exists on the server", "Move that logic into an @server function and call it from the handler."),
    ("is not defined in browser code", "Define it at module level (a literal constant or a helper function), "
                                       "make it a page variable, or do the work in an @server function."),
    ("markup expressions must be", "Assign the server call's result to a page variable at the top of the page, "
                                   "or call it from an event handler."),
    ("derived/read-only", "Only assign to page variables the handler owns (state). Computed values update "
                          "automatically from their inputs."),
    ("would be sent to the browser", "Keep secrets inside @server functions; never read them in markup or handlers."),
    ("must name a local variable", "Declare the bound variable in the page first, e.g. `name = \"\"`."),
    ("must name a page variable", "bind= needs a page variable name, e.g. bind={name}."),
    ("unknown component", "Define the component in the same file: a capitalized function containing markup."),
    ("is never closed", "Close the tag (or self-close void tags: <input ... />)."),
    ("mismatched </", "Make closing tags match the most recently opened tag."),
    ("unterminated tag or expression", "A tag or {expression} is missing its closing > or }."),
    ("is missing required prop", "Pass every required prop, or give the component parameter a default."),
    ("has no prop", "Use one of the component's parameter names as the attribute."),
    ("not supported in browser code", "Rewrite with supported Python (see pyweb_guide section 7) or move the code "
                                      "into an @server function."),
    ("must be called", "Call the server function inside a handler: `result = fn(...)`."),
    ("is a server function; call it from a", "Use a handler or lambda: onclick={lambda: fn(...)}"),
]


# ------------------------------------------------------------------ helpers

def _hint(message):
    for needle, hint in ERROR_HINTS:
        if needle in message:
            return hint
    return None


def _read_source(args):
    if args.get("source") is not None:
        return args["source"], args.get("path") or "app.pyweb"
    path = args.get("path") or "app.pyweb"
    with open(path, encoding="utf-8") as fh:
        return fh.read(), path


def _compile(args):
    from .compiler import compile_source
    source, filename = _read_source(args)
    return compile_source(source, filename=filename), source, filename


def _error_record(exc, filename):
    msg = getattr(exc, "pyweb_msg", None) or getattr(exc, "msg", None) or str(exc)
    if isinstance(msg, str) and msg.startswith("pyweb: "):
        msg = msg[len("pyweb: "):]
    line = getattr(exc, "lineno", None)
    rec = {"file": filename, "line": line, "message": str(msg)}
    hint = _hint(str(msg))
    if hint:
        rec["hint"] = hint
    return rec


def _gzip_size(js):
    from .build import minify_js
    return len(gzip.compress(minify_js(js).encode(), 9)) if js else 0


def _guide_text(section=None):
    with open(os.path.join(HERE, "ai", "guide.md"), encoding="utf-8") as fh:
        text = fh.read()
    if not section:
        return text
    parts = re.split(r"(?m)^## ", text)
    want = str(section).strip().lower()
    for part in parts[1:]:
        title = part.split("\n", 1)[0].lower()
        if title.startswith(want) or want in title:
            return "## " + part.strip()
    titles = [p.split("\n", 1)[0] for p in parts[1:]]
    return f"No section matching {section!r}. Sections: " + "; ".join(titles)


def _template_source(name, title=None):
    if name == "blank":
        source = BLANK_APP
    else:
        with open(os.path.join(HERE, "templates", f"{name}.pyweb"), encoding="utf-8") as fh:
            source = fh.read()
    if title:
        source = re.sub(r'App\(title="[^"]*"', f'App(title={json.dumps(title)}', source, count=1)
    return source


BLANK_APP = '''from pyweb import App

app = App(title="My app")


@app.page("/")
def Home():
    <main>
        <h1>Hello from PyWeb</h1>
    </main>
'''


def agent_instructions():
    """Contents of AGENTS.md written into new projects."""
    return (
        "# Instructions for AI coding agents\n\n"
        "This project is a PyWeb app (`pip install pyweb-stack`, `import pyweb`). The whole app is\n"
        "`app.pyweb`: Python plus HTML-like markup. Pages render on the server, event handlers\n"
        "compile to JavaScript, and `@server` functions run on the server.\n\n"
        "## Workflow\n\n"
        "- Run `pyweb check app.pyweb` after every change and fix errors by line number.\n"
        "- `pyweb inspect app.pyweb` shows what runs in the browser vs the server.\n"
        "- `pyweb dev app.pyweb` serves at http://localhost:8000 with live reload.\n"
        "- If the `pyweb` MCP server is available, use its tools (pyweb_guide, pyweb_check, ...).\n\n"
        + _guide_text().split("\n", 2)[2]
    )


def scaffold(directory, template="blank", title=None, overwrite=False):
    """Create a new app directory. Returns the list of files written."""
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; choose one of {', '.join(TEMPLATES)}")
    app_path = os.path.join(directory, "app.pyweb")
    if os.path.exists(app_path) and not overwrite:
        raise FileExistsError(f"{app_path} already exists (pass overwrite=true to replace it)")
    if title is None:
        title = os.path.basename(os.path.abspath(directory)).replace("-", " ").replace("_", " ").title()
    os.makedirs(os.path.join(directory, "static"), exist_ok=True)
    source = _template_source(template, title)
    files = {"app.pyweb": source,
             "AGENTS.md": agent_instructions(),
             "CLAUDE.md": "@AGENTS.md\n",
             ".gitignore": "dist/\n__pycache__/\n*.db\n"}
    if "/static/app.css" in source:
        with open(os.path.join(HERE, "templates", "app.css"), encoding="utf-8") as fh:
            files["static/app.css"] = fh.read()
    written = []
    for rel, text in files.items():
        path = os.path.join(directory, rel)
        if rel != "app.pyweb" and os.path.exists(path) and not overwrite:
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(path)
    return written


class _Apps:
    """TestClients per app path, reloaded when the file changes; cookies survive reloads."""

    def __init__(self):
        self._clients = {}

    def client(self, path):
        from .testing import TestClient
        path = os.path.abspath(path)
        mtime = os.path.getmtime(path)
        entry = self._clients.get(path)
        if entry is None or entry[0] != mtime:
            cookies = entry[1].cookies if entry else {}
            with contextlib.redirect_stdout(sys.stderr):
                client = TestClient(path)
            client.cookies = dict(cookies)
            self._clients[path] = (mtime, client)
        return self._clients[path][1]


# -------------------------------------------------------------------- tools

def tool_guide(args):
    return _guide_text(args.get("section"))


def tool_new_app(args):
    directory = args.get("directory") or "."
    files = scaffold(directory, args.get("template") or "blank", args.get("title"),
                     bool(args.get("overwrite")))
    return {"created": files,
            "next_steps": [f"Edit {os.path.join(directory, 'app.pyweb')}",
                           "Call pyweb_check after each edit",
                           f"Run: pyweb dev {os.path.join(directory, 'app.pyweb')}"]}


def tool_check(args):
    from .security import check_source
    try:
        source, filename = _read_source(args)
    except OSError as exc:
        return {"ok": False, "errors": [{"file": args.get("path"), "line": None, "message": str(exc)}]}
    from .compiler import compile_source
    findings = [{"line": f["line"], "kind": f["kind"], "message": f["message"]}
                for f in check_source(source, filename)]
    try:
        out = compile_source(source, filename=filename)
    except (SyntaxError, ValueError) as exc:
        return {"ok": False, "errors": [_error_record(exc, filename)], "findings": findings}
    pages = []
    for name, p in out["pages"].items():
        pages.append({"name": name, "route": p["route"], "signals": p["signals"],
                      "computeds": list(p["computeds"]), "interactive": bool(p["js"]),
                      "sent_to_browser": p.get("state_keys", []),
                      "page_js_gzip_bytes": _gzip_size(p["js"])})
    blocking = [f for f in findings if f["kind"] in ("secret-leak",)]
    return {"ok": not blocking, "errors": [], "findings": findings, "pages": pages,
            "server_functions": [{"name": s["name"], "args": s["args"], "returns": s["returns"]}
                                 for s in out["rpc"]]}


def tool_inspect(args):
    out, _source, _filename = _compile(args)
    pages = {}
    for name, p in out["pages"].items():
        pages[name] = {
            "route": p["route"],
            "placement": {sym: {"runs": loc, "why": why} for sym, (loc, why) in p["placement"].items()},
            "sent_to_browser": p.get("state_keys", []),
        }
    return {"pages": pages, "rpc_endpoints": [f"POST /__pyweb/rpc/{s['name']}" for s in out["rpc"]]}


def tool_compiled(args):
    out, _source, _filename = _compile(args)
    pages = out["pages"]
    name = args.get("page") or next(iter(pages), None)
    if name not in pages:
        return {"error": f"no page {name!r}; pages: {', '.join(pages)}"}
    p = pages[name]
    what = args.get("what") or "both"
    res = {"page": name}
    if what in ("js", "both"):
        res["javascript"] = p["js"] or "// no JavaScript: this page is static"
    if what in ("html", "both"):
        res["server_html"] = p["html_body"]
    return res


APPS = _Apps()


def tool_render(args):
    path = args.get("path") or "app.pyweb"
    url = args.get("url") or "/"
    with contextlib.redirect_stdout(sys.stderr):
        resp = APPS.client(path).get(url)
    text = resp.text
    limit = int(args.get("max_chars") or 20000)
    res = {"status": resp.status, "content_type": resp.header("Content-Type")}
    if resp.header("Location"):
        res["redirect_to"] = resp.header("Location")
    if resp.status >= 500:
        pre = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.S)
        if pre:
            import html as _html
            res["error"] = _html.unescape(pre.group(1)).strip()[-4000:]
    res["body"] = text[:limit] + ("\n... (truncated)" if len(text) > limit else "")
    return res


def tool_call(args):
    from .rpc import RPCError
    path = args.get("path") or "app.pyweb"
    fn = args.get("function")
    if not fn:
        raise ValueError("function is required")
    client = APPS.client(path)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = client.rpc(fn, **(args.get("args") or {}))
        return {"ok": True, "result": result, "cookies": sorted(client.cookies)}
    except RPCError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc), "status": exc.status}}


PATH_PROP = {"type": "string", "description": "Path to the .pyweb file (default: app.pyweb)."}
SOURCE_PROP = {"type": "string", "description": "Source text to check instead of reading `path`."}

TOOLS = [
    {"name": "pyweb_guide", "fn": tool_guide, "readOnly": True,
     "description": "The rules for writing PyWeb (.pyweb) apps: skeleton, what runs where, state, markup, "
                    "components, server functions, the supported browser Python subset, and common errors with "
                    "fixes. Read it before writing PyWeb code.",
     "inputSchema": {"type": "object", "properties": {
         "section": {"type": "string", "description": "Optional section title or number, e.g. 'markup' or '8'."}}}},
    {"name": "pyweb_new_app", "fn": tool_new_app, "readOnly": False,
     "description": "Create a new PyWeb app directory from a template, including AGENTS.md/CLAUDE.md instructions "
                    "for AI agents. Templates: blank, counter, todo (components, lists), blog (SQL database, "
                    "server functions, route params), auth (sessions, passwords), chat (polling).",
     "inputSchema": {"type": "object", "properties": {
         "directory": {"type": "string", "description": "Directory to create (default: current directory)."},
         "template": {"type": "string", "enum": list(TEMPLATES)},
         "title": {"type": "string", "description": "App title."},
         "overwrite": {"type": "boolean", "description": "Replace an existing app.pyweb."}}}},
    {"name": "pyweb_check", "fn": tool_check, "readOnly": True,
     "description": "Compile a PyWeb app and run security checks. Returns ok, errors (file, line, message, hint), "
                    "findings, pages (route, signals, computeds, what is sent to the browser, JS size) and server "
                    "functions. Call after every edit.",
     "inputSchema": {"type": "object", "properties": {"path": PATH_PROP, "source": SOURCE_PROP}}},
    {"name": "pyweb_inspect", "fn": tool_inspect, "readOnly": True,
     "description": "Explain where every page variable, handler and server function runs (browser or server) and "
                    "why, and which values are sent to the browser.",
     "inputSchema": {"type": "object", "properties": {"path": PATH_PROP, "source": SOURCE_PROP}}},
    {"name": "pyweb_compiled", "fn": tool_compiled, "readOnly": True,
     "description": "Show the JavaScript the compiler generated for a page and/or its server-rendered HTML "
                    "(static prerender). Useful for debugging browser behaviour.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "source": SOURCE_PROP,
         "page": {"type": "string", "description": "Page function name (default: first page)."},
         "what": {"type": "string", "enum": ["js", "html", "both"]}}}},
    {"name": "pyweb_render", "fn": tool_render, "readOnly": False,
     "description": "Request a URL from the app in-process (like a browser's first load) and return the status, "
                    "redirect target, server-rendered HTML, and the Python traceback for 500 errors. Runs the "
                    "app's server code. Session cookies from pyweb_call are reused.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH_PROP, "url": {"type": "string", "description": "URL path, e.g. / or /posts/1?x=y"},
         "max_chars": {"type": "integer", "description": "Truncate the body (default 20000)."}}}},
    {"name": "pyweb_call", "fn": tool_call, "readOnly": False,
     "description": "Call an @server function of the app exactly like the browser does (JSON RPC with "
                    "validation). Returns the result or the typed error (code, message). Cookies persist across "
                    "calls, so e.g. call a login function, then pyweb_render a protected page.",
     "inputSchema": {"type": "object", "required": ["function"], "properties": {
         "path": PATH_PROP, "function": {"type": "string"},
         "args": {"type": "object", "description": "Keyword arguments by parameter name."}}}},
]

PROMPTS = [
    {"name": "build_pyweb_app",
     "description": "Build a PyWeb app from a description, verifying it with the PyWeb tools.",
     "arguments": [{"name": "description", "description": "What the app should do.", "required": True},
                   {"name": "directory", "description": "Where to create it (default: current directory).",
                    "required": False}]},
]


def _prompt_text(args):
    desc = args.get("description", "").strip()
    directory = args.get("directory") or "."
    return (
        f"Build a PyWeb web app in `{directory}`: {desc}\n\n"
        "Steps:\n"
        "1. Call pyweb_guide and follow its rules.\n"
        f"2. Call pyweb_new_app (directory={directory!r}) with the closest template.\n"
        "3. Edit app.pyweb. Keep database/secret/import work in @server functions; keep handlers to "
        "browser-safe Python.\n"
        "4. Call pyweb_check after every edit until ok is true with no errors; apply the hints.\n"
        "5. Verify with pyweb_render (each page) and pyweb_call (each server function).\n"
        "6. Tell the user to run `pyweb dev app.pyweb` to try it."
    )


def resources():
    out = [{"uri": "pyweb://guide", "name": "PyWeb guide for AI assistants", "mimeType": "text/markdown",
            "description": "Rules, patterns and error fixes for writing .pyweb apps."}]
    for t in TEMPLATES:
        out.append({"uri": f"pyweb://templates/{t}", "name": f"Template: {t}", "mimeType": "text/plain",
                    "description": f"The {t} starter app (app.pyweb)."})
    return out


def read_resource(uri):
    if uri == "pyweb://guide":
        return _guide_text(), "text/markdown"
    m = re.fullmatch(r"pyweb://templates/(\w+)", uri)
    if m and m.group(1) in TEMPLATES:
        return _template_source(m.group(1)), "text/plain"
    raise KeyError(uri)


# ------------------------------------------------------------------ protocol

class Server:
    def __init__(self):
        from . import __version__
        self.version = __version__

    def handle(self, msg):
        """Handle one JSON-RPC message; return the response dict or None."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(None, -32600, "invalid request")
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if mid is None:  # notification
            return None
        try:
            result = self.dispatch(method, params)
        except _RPCFault as fault:
            return _error(mid, fault.code, fault.message)
        except Exception as exc:  # noqa: BLE001 - report, never crash the server
            return _error(mid, -32603, f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def dispatch(self, method, params):
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return {"protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False},
                                     "resources": {"listChanged": False, "subscribe": False},
                                     "prompts": {"listChanged": False}},
                    "serverInfo": {"name": "pyweb", "title": "PyWeb", "version": self.version},
                    "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"],
                               "annotations": {"readOnlyHint": t["readOnly"]}} for t in TOOLS]}
        if method == "tools/call":
            return self.call_tool(params.get("name"), params.get("arguments") or {})
        if method == "resources/list":
            return {"resources": resources()}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            uri = params.get("uri")
            try:
                text, mime = read_resource(uri)
            except KeyError:
                raise _RPCFault(-32002, f"resource not found: {uri}") from None
            return {"contents": [{"uri": uri, "mimeType": mime, "text": text}]}
        if method == "prompts/list":
            return {"prompts": PROMPTS}
        if method == "prompts/get":
            if params.get("name") != "build_pyweb_app":
                raise _RPCFault(-32602, f"unknown prompt {params.get('name')!r}")
            return {"description": PROMPTS[0]["description"],
                    "messages": [{"role": "user", "content": {"type": "text",
                                                              "text": _prompt_text(params.get("arguments") or {})}}]}
        raise _RPCFault(-32601, f"method not found: {method}")

    def call_tool(self, name, args):
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            raise _RPCFault(-32602, f"unknown tool {name!r}")
        try:
            with contextlib.redirect_stdout(sys.stderr):
                value = tool["fn"](args)
        except Exception as exc:  # noqa: BLE001 - tool errors are results, not protocol errors
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            return {"content": [{"type": "text", "text": detail}], "isError": True}
        if isinstance(value, str):
            return {"content": [{"type": "text", "text": value}], "isError": False}
        return {"content": [{"type": "text", "text": json.dumps(value, indent=2, default=str)}],
                "structuredContent": value, "isError": False}


class _RPCFault(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve_stdio(stdin=None, stdout=None):
    """Run the server until stdin closes."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            responses = [_error(None, -32700, "parse error")]
        else:
            msgs = msg if isinstance(msg, list) else [msg]
            responses = [r for r in (server.handle(m) for m in msgs) if r is not None]
        for resp in responses:
            stdout.write(json.dumps(resp, default=str) + "\n")
            stdout.flush()


def main():
    serve_stdio()


if __name__ == "__main__":
    main()
