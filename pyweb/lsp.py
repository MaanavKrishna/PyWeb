"""The PyWeb language server: ``pyweb lsp``.

Speaks the Language Server Protocol over stdio (standard library only)
and gives editors, for ``.pyweb`` files:

* diagnostics: compile errors (with the right line, including errors in
  imported ``.pyweb`` files) and security warnings, as you type;
* hover: where a name runs (browser or server) and why, component
  signatures and server functions;
* completion: components and HTML tags after ``<``, a component's props
  and common attributes inside a tag, names after ``from pyweb import``;
* npm packages: hover shows the installed version and its TypeScript
  declaration, completion offers installed packages inside ``npm("`` and
  a module's exports after ``name.``;
* go to definition for components, server functions and helpers, across
  files;
* an outline of pages, components and server functions;
* data (see :mod:`pyweb.lsp_data`): Model fields and query methods after
  ``Post.``, keyword fields in ``where(``/``create(``, relations in
  ``include("``, a ``<Form>``'s fields in ``name="``; errors for form fields
  the action lacks, warnings for loops that read a relation the query didn't
  include (N+1), and "the Models changed without a migration" (with a code
  lens that writes it), checked with ``pyweb db check`` when the file is saved.

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
              "onchange", "onsubmit", "onkeydown", "onfocus", "onblur", "ref"]
PYWEB_EXPORTS = ["App", "server", "component", "request", "session", "redirect", "NotFound", "RPCError",
                 "channel", "publish", "subscribe", "Model", "task", "worker", "edge", "npm", "head", "Markdown"]

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NPM = re.compile(r"""^\s*([A-Za-z_]\w*)\s*=\s*npm\(\s*["']([^"']+)["']\s*(?:,\s*["']([^"']+)["']\s*)?\)""", re.M)


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
    from . import lsp_data as D
    tree, ui = D.parse(text, path)
    if tree is not None:
        known = D.models(tree)
        if a.error is None:      # the compiler already reports a bad field; say it once
            for line, msg in D.form_problems(tree, ui, known):
                out.append({"range": _range(line - 1, text=text), "severity": 1, "source": "pyweb",
                            "code": "unknown-form-field", "message": msg})
        for line, msg in D.n_plus_one(tree, ui, known):
            out.append({"range": _range(line - 1, text=text), "severity": 2, "source": "pyweb",
                        "code": "missing-include", "message": msg})
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
            kind = ("page" if ".page" in decos else "layout" if ".layout" in decos
                    else "error page" if ".error(" in decos else "server function" if "server" in decos
                    else "component" if node.name[:1].isupper() else "function")
            out[node.name] = (path, node.lineno, kind, _fn_signature(node))
    return out


def _lock(path):
    from . import packages
    root = packages.find_lock(os.path.dirname(os.path.abspath(path))) if path else None
    return packages.read_lock(root) if root else {"packages": {}, "imports": {}}


def _npm_bindings(text):
    """``{name: (spec, export)}`` for ``name = npm("spec", "export")`` lines (works while the file is broken)."""
    return {m.group(1): (m.group(2), m.group(3) or "default") for m in _NPM.finditer(text)}


def _npm_hover(word, spec, export, path):
    from .packages import split_specifier
    lock = _lock(path)
    pkg = lock["packages"].get(split_specifier(spec)[0])
    head = f"```python\n{word} = npm({spec!r}{'' if export == 'default' else f', {export!r}'})\n```\n"
    if not pkg:
        return head + f"Not installed: run `pyweb add {spec}` in the app folder."
    types = pkg.get("types") or {}
    decl = types.get(word if export == "default" else export) if export != "*" else None
    lines = [f"npm package **{split_specifier(spec)[0]}@{pkg['version']}**, runs in the browser."]
    if decl:
        lines.append(f"```ts\n{decl}\n```")
    elif export == "*" and types:
        lines.append("Exports: " + ", ".join(f"`{n}`" for n in sorted(types)[:30]))
    return head + "\n".join(lines)


def hover(text, path, line, col, analysis=None):
    """Markdown describing the name at the position, or ``None``."""
    word = word_at(text, line, col)
    if not word:
        return None
    bound = _npm_bindings(text)
    if word in bound:
        return _npm_hover(word, *bound[word], path)
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
    if not m or (not m.group(2).strip() and not prefix.endswith(" ")):
        return None
    attrs = re.findall(r"([A-Za-z_][\w-]*)\s*=", m.group(2))
    return m.group(1), attrs


def completions(text, path, line, col, analysis=None, parsed=None):
    """LSP completion items for the position (``parsed``: the last ``(tree, ui)`` that parsed)."""
    lines = text.splitlines()
    prefix = lines[line][:col] if 0 <= line < len(lines) else ""
    from . import lsp_data as D
    tree, ui = D.parse(text, path)
    if tree is None and parsed:
        tree, ui = parsed
    if tree is not None:
        known = D.models(tree)
        found = D.form_completion(prefix, tree, ui, line + 1, known)
        if found is None:
            found = D.complete(prefix, tree, known)
        if found is not None:
            return found
    if re.search(r"^\s*from\s+pyweb\s+import\s+[\w\s,]*$", prefix):
        return [{"label": n, "kind": 9} for n in PYWEB_EXPORTS]
    if re.search(r"""\bnpm\(\s*["'][^"']*$""", prefix):
        from .packages import split_specifier
        lock = _lock(path)
        return [{"label": spec, "kind": 9,
                 "detail": lock["packages"].get(split_specifier(spec)[0], {}).get("version", "")}
                for spec in sorted(lock["imports"])]
    member = re.search(r"\b([A-Za-z_]\w*)\.(\w*)$", prefix)
    if member:
        bound = _npm_bindings(text).get(member.group(1))
        if bound and bound[1] == "*":
            from .packages import split_specifier
            pkg = _lock(path)["packages"].get(split_specifier(bound[0])[0]) or {}
            return [{"label": n, "kind": 6, "detail": d} for n, d in sorted((pkg.get("types") or {}).items())]
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

SYMBOL_KINDS = {"page": 12, "layout": 12, "error page": 12, "component": 5, "server function": 12, "function": 12, "model": 5, "class": 5}


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
        self.parsed = {}     # uri -> last (tree, ui) that parsed
        self.migrations = {} # uri -> [missing change, ...] from `pyweb db check` (None: not checked)
        self._checking = set()
        self._threads = {}
        self._requests = 0

    def _analyse(self, uri):
        from . import lsp_data as D
        a = Analysis(self.docs[uri], _path(uri))
        self.analyses[uri] = a
        if a.ok:
            self.good[uri] = a
        tree, ui = D.parse(a.text, a.path)
        if tree is not None:
            self.parsed[uri] = (tree, ui)
        self._publish(uri, a)
        return a

    def _publish(self, uri, a=None):
        a = a or self.analyses.get(uri)
        if a is None:
            return
        found = diagnostics(a.text, a.path, a) + self._migration_diagnostics(uri)
        self.send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                   "params": {"uri": uri, "diagnostics": found}})

    # ------------------------------------------------- migrations out of date
    def _model_lines(self, uri):
        from . import lsp_data as D
        tree = (self.parsed.get(uri) or (None, None))[0]
        return {} if tree is None else {n: m["line"] for n, m in D.models(tree).items() if m["line"]}

    def _migration_diagnostics(self, uri):
        missing = self.migrations.get(uri)
        lines = self._model_lines(uri)
        if not missing or not lines:
            return []
        first = min(lines.values()) - 1
        text = self.docs.get(uri, "")
        return [{"range": _range(first, text=text), "severity": 2, "source": "pyweb", "code": "migration-needed",
                 "message": "The Models changed without a migration:\n" + "\n".join(f"- {m}" for m in missing)
                            + "\nRun `pyweb db diff` (or use the code lens) to write it."}]

    def check_migrations(self, uri, *, wait=False):
        """Run ``pyweb db check`` for the app (in the background) when it has a migrations folder."""
        import subprocess
        import threading
        path = _path(uri)
        if not path or not os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(path)), "migrations")):
            self.migrations[uri] = None
            return
        running = self._threads.get(uri)
        if running is not None and running.is_alive():
            if wait:
                running.join(90)
            return
        self._checking.add(uri)

        def run():
            try:
                done = subprocess.run([sys.executable, "-m", "pyweb.cli", "db", "check", "--app", path, "--json"],
                                      cwd=os.path.dirname(os.path.abspath(path)), capture_output=True, text=True,
                                      timeout=60, env={**os.environ, "PYWEB_WORKER": "0"})
                last = (done.stdout.strip().splitlines() or ["{}"])[-1]
                result = json.loads(last) if last.startswith("{") else {}
                self.migrations[uri] = list(result.get("missing") or []) if "ok" in result else None
            except Exception:  # noqa: BLE001 - an app that doesn't load yet: say nothing
                self.migrations[uri] = None
            finally:
                self._checking.discard(uri)
            self._publish(uri)
            self._requests += 1
            self.send({"jsonrpc": "2.0", "id": f"pyweb-{self._requests}", "method": "workspace/codeLens/refresh"})
        thread = threading.Thread(target=run, name="pyweb-db-check", daemon=True)
        self._threads[uri] = thread
        thread.start()
        if wait:
            thread.join(90)

    def code_lenses(self, uri):
        lines = self._model_lines(uri)
        missing = self.migrations.get(uri)
        if not lines or missing is None:
            return []
        lenses = []
        for name, line in sorted(lines.items(), key=lambda kv: kv[1]):
            if missing:
                title = f"$(warning) needs a migration ({len(missing)} change{'s' if len(missing) != 1 else ''}): " \
                        "Create migration"
                cmd = {"title": title, "command": "pyweb.makeMigration", "arguments": [_path(uri)]}
            else:
                cmd = {"title": "$(check) migrations up to date", "command": ""}
            lenses.append({"range": _range(line - 1, 0, 0), "command": cmd})
            break                       # one lens, on the first Model, is enough
        return lenses

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
                "completionProvider": {"triggerCharacters": ["<", " ", ".", "(", "\"", "'", ","]},
                "codeLensProvider": {"resolveProvider": False},
            }, "serverInfo": {"name": "pyweb", "version": __version__}}
        if method == "textDocument/didOpen":
            self.docs[uri] = doc.get("text", "")
            self._analyse(uri)
            self.check_migrations(uri)
        elif method == "textDocument/didChange":
            changes = p.get("contentChanges") or []
            if changes:
                self.docs[uri] = changes[-1].get("text", "")
                self._analyse(uri)
        elif method == "textDocument/didSave":
            if uri in self.docs:
                self._analyse(uri)
                self.check_migrations(uri)
        elif method == "textDocument/codeLens":
            return self.code_lenses(uri)
        elif method == "textDocument/didClose":
            for table in (self.docs, self.analyses, self.good, self.parsed, self.migrations):
                table.pop(uri, None)
            self.send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                       "params": {"uri": uri, "diagnostics": []}})
        elif method == "textDocument/hover":
            text = hover(self.docs.get(uri, ""), _path(uri), pos["line"], pos["character"],
                         self.analyses.get(uri))
            return {"contents": {"kind": "markdown", "value": text}} if text else None
        elif method == "textDocument/completion":
            a = self.good.get(uri)
            return completions(self.docs.get(uri, ""), _path(uri), pos["line"], pos["character"], a,
                               self.parsed.get(uri))
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
