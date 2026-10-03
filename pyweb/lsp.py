"""The PyWeb language server: ``pyweb lsp``.

Speaks the Language Server Protocol over stdio (standard library only)
and gives editors, for ``.pyweb`` files:

* diagnostics: compile errors (with the right line, including errors in
  imported ``.pyweb`` files) and security warnings, as you type;
* hover: where a name runs (browser or server) and why, component
  signatures and server functions;
* completion: components and HTML tags after ``<``, a component's props
  and common attributes inside a tag, names after ``from pyweb import``;
* go to definition for components, server functions and helpers, across
  files;
* an outline of pages, components and server functions.

The analysis functions are pure (source text in, plain data out) so they
can be tested without a transport.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import urllib.parse

from .compiler import parser as P

HTML_TAGS = sorted({
    "a", "abbr", "article", "aside", "audio", "b", "blockquote", "br", "button", "canvas", "code",
    "datalist", "dd", "details", "dialog", "div", "dl", "dt", "em", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i", "iframe", "img", "input",
    "label", "legend", "li", "main", "mark", "nav", "ol", "optgroup", "option", "output", "p", "picture",
    "pre", "progress", "section", "select", "small", "source", "span", "strong", "sub", "summary", "sup",
    "svg", "table", "tbody", "td", "textarea", "tfoot", "th", "thead", "time", "tr", "u", "ul", "video",
})
HTML_ATTRS = ["id", "class", "style", "href", "src", "alt", "title", "type", "name", "value", "placeholder",
              "disabled", "checked", "required", "for", "role", "aria-label", "bind", "onclick", "oninput",
              "onchange", "onsubmit", "onkeydown", "onfocus", "onblur"]
PYWEB_EXPORTS = ["App", "server", "component", "request", "session", "redirect", "NotFound", "RPCError",
                 "channel", "publish", "subscribe", "Model", "task", "worker", "edge"]

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# ------------------------------------------------------------- analysis

class Analysis:
    """Compile ``text`` as the file at ``path``; keeps errors instead of raising."""

    def __init__(self, text, path=None):
        from .compiler import compile_source
        self.text, self.path = text, path
        self.compiled, self.error = None, None
        try:
            self.compiled = compile_source(text, filename=path or "<editor>")
        except (SyntaxError, ValueError) as exc:  # CompileError is a ValueError
            self.error = exc

    @property
    def ok(self):
        return self.compiled is not None


def word_at(text, line, col):
    """The identifier at 0-based ``line``/``col``, or ``""``."""
    lines = text.splitlines()
    if not 0 <= line < len(lines):
        return ""
    for m in _WORD.finditer(lines[line]):
        if m.start() <= col <= m.end():
            return m.group(0)
    return ""


def _range(line, start=0, end=None, text=None):
    if end is None:
        lines = (text or "").splitlines()
        end = len(lines[line]) if 0 <= line < len(lines) else start + 1
    return {"start": {"line": line, "character": start}, "end": {"line": line, "character": end}}


def _import_line(text, path):
    """0-based line of ``from <module> import`` for the .pyweb file ``path``."""
    stem = os.path.splitext(os.path.basename(path))[0]
    for i, line in enumerate(text.splitlines()):
        if re.match(rf"\s*from\s+[\w.]*\b{re.escape(stem)}\s+import\b", line):
            return i
    return 0


def diagnostics(text, path=None, analysis=None):
    """LSP diagnostics for one document."""
    from .security import check_source
    a = analysis or Analysis(text, path)
    out = []
    if a.error is not None:
        exc = a.error
        msg = getattr(exc, "pyweb_msg", None) or getattr(exc, "msg", None) or str(exc)
        msg = str(msg).removeprefix("pyweb: ")
        where = getattr(exc, "filename", None)
        line = (getattr(exc, "lineno", None) or 1) - 1
        if where and path and os.path.abspath(where) != os.path.abspath(path) and not where.startswith("<"):
            msg = f"{os.path.basename(where)}:{line + 1}: {msg}"
            line = _import_line(text, where)
        out.append({"range": _range(line, text=text), "severity": 1, "source": "pyweb", "message": msg})
    try:
        findings = check_source(text, path or "app.pyweb")
    except Exception:  # noqa: BLE001 - security checks are best effort while typing
        findings = []
    for f in findings:
        line = max((f.get("line") or 1) - 1, 0)
        sev = 1 if f["kind"] == "secret-leak" else 2
        out.append({"range": _range(line, text=text), "severity": sev, "source": "pyweb",
                    "code": f["kind"], "message": f["message"]})
    return out


def _fn_signature(node):
    args = []
    for a in node.args.args:
        ann = f": {ast.unparse(a.annotation)}" if a.annotation is not None else ""
        args.append(a.arg + ann)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{node.name}({', '.join(args)}){ret}"


def _page_at(compiled, line):
    """The page whose function spans 1-based ``line``."""
    for name, page in compiled["pages"].items():
        node = page["info"].node
        if node.lineno <= line <= getattr(node, "end_lineno", node.lineno):
            return name, page
    return None, None


def _definitions(compiled, path):
    """name -> (file, 1-based line, kind, signature) for the files this one imports."""
    out = {}
    for lib in compiled.get("libraries", []):
        try:
            with open(lib.path, encoding="utf-8") as fh:
                out.update(_local_definitions(fh.read(), lib.path))
        except OSError:
            continue
    ctx = compiled["context"]
    for name, info in ctx.components.items():  # by the name used here (`import X as Y`)
        source = ctx.imported_components.get(name)
        out[name] = (source[0].path if source else path, info.node.lineno, "component", _fn_signature(info.node))
    return out


def _local_definitions(text, path):
    try:
        tree = ast.parse(P.split_sources(text)[0])
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decos = " ".join(ast.unparse(d) for d in node.decorator_list)
            kind = ("page" if ".page" in decos else "server function" if "server" in decos
                    else "component" if node.name[:1].isupper() else "function")
            out[node.name] = (path, node.lineno, kind, _fn_signature(node))
    return out


def hover(text, path, line, col, analysis=None):
    """Markdown describing the name at the position, or ``None``."""
    word = word_at(text, line, col)
    if not word:
        return None
    a = analysis or Analysis(text, path)
    defs = _local_definitions(text, path)
    if a.ok:
        defs = {**_definitions(a.compiled, path), **defs}
        _name, page = _page_at(a.compiled, line + 1)
        if page and word in page["placement"]:
            where, why = page["placement"][word]
            label = {"browser": "runs in the browser", "server": "runs on the server"}.get(where, where)
            return f"**{word}** {label}: {why}"
        for spec in a.compiled["rpc"]:
            if spec["name"] == word:
                args = ", ".join(f"{x['name']}: {x['type']}" if x.get("type") else x["name"] for x in spec["args"])
                ret = f" -> {spec['returns']}" if spec.get("returns") else ""
                return (f"```python\n@server\ndef {word}({args}){ret}\n```\n"
                        f"Runs on the server. Browser code calls it over RPC (`POST /__pyweb/rpc/{word}`).")
    if word in defs:
        file, ln, kind, sig = defs[word]
        where = "" if not file or file == path else f" (in {os.path.basename(file)}:{ln})"
        return f"```python\ndef {sig}\n```\n{kind}{where}"
    return None


def _open_tag(prefix):
    """``(tag, typed_attrs)`` if the cursor is inside an unclosed ``<tag ...``."""
    m = re.search(r"<([A-Za-z][\w.]*)((?:\s+[^<>]*)?)$", prefix)
    if not m or not m.group(2).strip() and not prefix.endswith(" "):
        return None
    attrs = re.findall(r"([A-Za-z_][\w-]*)\s*=", m.group(2))
    return m.group(1), attrs


def completions(text, path, line, col, analysis=None):
    """LSP completion items for the position."""
    lines = text.splitlines()
    prefix = lines[line][:col] if 0 <= line < len(lines) else ""
    if re.search(r"^\s*from\s+pyweb\s+import\s+[\w\s,]*$", prefix):
        return [{"label": n, "kind": 9} for n in PYWEB_EXPORTS]
    a = analysis
    components = {}
    if a is not None and a.ok:
        for name, info in a.compiled["context"].components.items():
            components[name] = info
    tag = re.search(r"<([A-Za-z][\w]*)?$", prefix)
    if tag:
        items = [{"label": name, "kind": 7, "detail": "component " + _fn_signature(info.node)}
                 for name, info in sorted(components.items())]
        items += [{"label": t, "kind": 10, "detail": "HTML element"} for t in HTML_TAGS]
        return items
    inside = _open_tag(prefix)
    if inside:
        name, used = inside
        if name in components:
            info = components[name]
            return [{"label": p, "kind": 10, "detail": f"prop of {name}", "insertText": p + "={$1}",
                     "insertTextFormat": 2} for p in info.params if p not in used and p != "children"]
        return [{"label": at, "kind": 10, "insertText": at + "={$1}" if at.startswith("on") or at == "bind"
                 else at + '="$1"', "insertTextFormat": 2} for at in HTML_ATTRS if at not in used]
    return []


def definition(text, path, line, col, analysis=None):
    """``(file, 0-based line)`` where the name at the position is defined."""
    word = word_at(text, line, col)
    if not word:
        return None
    defs = _local_definitions(text, path)
    a = analysis or Analysis(text, path)
    if a.ok:
        defs = {**_definitions(a.compiled, path), **defs}
    if word in defs:
        file, ln, _kind, _sig = defs[word]
        return file or path, ln - 1
    return None


def symbols(text):
    """Outline entries: pages, components, server functions, classes, functions."""
    out = []
    for name, (_f, line, kind, _sig) in _local_definitions(text, None).items():
        out.append({"kind": kind, "name": name, "line": line})
    try:
        tree = ast.parse(P.split_sources(text)[0])
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [getattr(b, "id", "") for b in node.bases]
            out.append({"kind": "model" if "Model" in bases else "class", "name": node.name, "line": node.lineno})
    return sorted(out, key=lambda s: s["line"])


# -------------------------------------------------------------- protocol

SYMBOL_KINDS = {"page": 12, "component": 5, "server function": 12, "function": 12, "model": 5, "class": 5}


def _path(uri):
    parsed = urllib.parse.urlparse(uri)
    return urllib.parse.unquote(parsed.path) if parsed.scheme == "file" else None


def _uri(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


class LanguageServer:
    """One editor session. ``send(message)`` writes a JSON-RPC message."""

    def __init__(self, send):
        self.send = send
        self.docs = {}       # uri -> text
        self.analyses = {}   # uri -> last Analysis
        self.good = {}       # uri -> last Analysis that compiled (for completion while typing)

    def _analyse(self, uri):
        a = Analysis(self.docs[uri], _path(uri))
        self.analyses[uri] = a
        if a.ok:
            self.good[uri] = a
        self.send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                   "params": {"uri": uri, "diagnostics": diagnostics(a.text, a.path, a)}})
        return a

    def handle(self, msg):
        method, params, mid = msg.get("method"), msg.get("params") or {}, msg.get("id")
        try:
            result = self.dispatch(method, params)
        except Exception as exc:  # noqa: BLE001 - never kill the editor session
            if mid is not None:
                self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}})
            return
        if mid is not None and method is not None:
            self.send({"jsonrpc": "2.0", "id": mid, "result": result})

    def dispatch(self, method, p):
        doc = p.get("textDocument", {})
        uri = doc.get("uri")
        pos = p.get("position", {})
        if method == "initialize":
            from . import __version__
            return {"capabilities": {
                "textDocumentSync": {"openClose": True, "change": 1, "save": True},
                "hoverProvider": True, "definitionProvider": True, "documentSymbolProvider": True,
                "completionProvider": {"triggerCharacters": ["<", " "]},
            }, "serverInfo": {"name": "pyweb", "version": __version__}}
        if method == "textDocument/didOpen":
            self.docs[uri] = doc.get("text", "")
            self._analyse(uri)
        elif method == "textDocument/didChange":
            changes = p.get("contentChanges") or []
            if changes:
                self.docs[uri] = changes[-1].get("text", "")
                self._analyse(uri)
        elif method == "textDocument/didSave":
            if uri in self.docs:
                self._analyse(uri)
        elif method == "textDocument/didClose":
            for table in (self.docs, self.analyses, self.good):
                table.pop(uri, None)
            self.send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                       "params": {"uri": uri, "diagnostics": []}})
        elif method == "textDocument/hover":
            text = hover(self.docs.get(uri, ""), _path(uri), pos["line"], pos["character"],
                         self.analyses.get(uri))
            return {"contents": {"kind": "markdown", "value": text}} if text else None
        elif method == "textDocument/completion":
            a = self.good.get(uri)
            return completions(self.docs.get(uri, ""), _path(uri), pos["line"], pos["character"], a)
        elif method == "textDocument/definition":
            found = definition(self.docs.get(uri, ""), _path(uri), pos["line"], pos["character"],
                               self.good.get(uri))
            if found:
                file, line = found
                return {"uri": _uri(file) if file else uri, "range": _range(line, 0, 0)}
            return None
        elif method == "textDocument/documentSymbol":
            text = self.docs.get(uri, "")
            out = []
            lines = text.splitlines()
            for s in symbols(text):
                ln = s["line"] - 1
                r = _range(ln, 0, len(lines[ln]) if ln < len(lines) else 0)
                out.append({"name": s["name"], "detail": s["kind"], "kind": SYMBOL_KINDS.get(s["kind"], 12),
                            "range": r, "selectionRange": r})
            return out
        elif method == "shutdown":
            return None
        return None


def _read_message(stream):
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        name, _, value = line.decode("ascii", "replace").partition(":")
        if name.lower() == "content-length":
            length = int(value.strip())
    if length is None:
        return {}
    return json.loads(stream.read(length).decode("utf-8"))


def serve_stdio(stdin=None, stdout=None):
    """Run the language server until the editor sends ``exit``."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer

    def send(message):
        body = json.dumps(message).encode("utf-8")
        stdout.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        stdout.flush()

    server = LanguageServer(send)
    while True:
        msg = _read_message(stdin)
        if msg is None or msg.get("method") == "exit":
            return
        if msg:
            server.handle(msg)
