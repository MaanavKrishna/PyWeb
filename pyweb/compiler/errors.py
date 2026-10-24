"""Compiler errors that point at a `.pyweb` file and line, and how they are shown.

``describe(exc, source)`` turns any compile or render error into a record with the line, a code frame,
a fix hint and a docs link; the CLI, the dev overlay and the MCP server all show that same record.
"""

from __future__ import annotations

import difflib
import re

DOCS_URL = "https://maanavkrishna.github.io/PyWeb/"


class CompileError(ValueError):
    """A user-facing compile error.

    ``str(err)`` is ``"<file>:<line>: <message>"`` so editors and CI logs
    can jump to the source; ``err.lineno`` and ``err.msg`` are available
    separately.
    """

    def __init__(self, msg, lineno=None, filename=None):
        self.msg = msg
        self.lineno = lineno
        self.filename = filename
        super().__init__(self._render())

    def _render(self):
        where = self.filename or "<pyweb>"
        if self.lineno:
            where += f":{self.lineno}"
        return f"{where}: {self.msg}"

    def with_file(self, filename):
        self.filename = filename
        self.args = (self._render(),)
        return self


def suggest(name, candidates, cutoff=0.75):
    """The closest of ``candidates`` to a misspelled ``name``, or None."""
    names = [c for c in dict.fromkeys(candidates) if isinstance(c, str) and c and c != name]
    lower = {c.lower(): c for c in names}
    if name.lower() in lower:                           # only the case differs
        return lower[name.lower()]
    found = difflib.get_close_matches(name, names, n=1, cutoff=cutoff)
    return found[0] if found else None


def did_you_mean(name, candidates):
    """``"; did you mean 'x'?"`` for the closest candidate, or ``""``."""
    best = suggest(name, candidates)
    return f"; did you mean {best!r}?" if best else ""


# (text in the message, what to do about it); the first match wins.
HINTS = [
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
    ("unknown component", "Define it in this file (a capitalized function containing markup) or import it "
                          "from another .pyweb file: `from widgets import Card`."),
    ("is never closed", "Close the tag (or self-close void tags: <input ... />)."),
    ("mismatched </", "Make closing tags match the most recently opened tag."),
    ("unterminated tag or expression", "A tag or {expression} is missing its closing > or }."),
    ("is missing required prop", "Pass every required prop, or give the component parameter a default."),
    ("has no prop", "Use one of the component's parameter names as the attribute."),
    ("not supported in browser code", "Rewrite with supported Python or move the code into an @server function."),
    ("must be called", "Call the server function inside a handler: `result = fn(...)`."),
    ("is a server function; call it from a", "Use a handler or lambda: onclick={lambda: fn(...)}"),
    ("isn't installed: run `pyweb add", "Run the `pyweb add` command from the message in the app folder, then "
                                       "check again."),
    ("which only exists in the browser", "Call the npm package in a handler or on_mount and keep the result in a "
                                         "page variable that the markup shows."),
    ("has no {children}", "Put {children} exactly once in the layout's markup where the page should go."),
    ("uses {children}", "Put {children} exactly once in the layout's markup where the page should go."),
    ("can only take `children`", "Layouts take only children; read request/session data in the layout body."),
    ("asks for layout", "Use the name of a function decorated with @app.layout, or layout=None for no layout."),
    ("npm() takes literal strings", "Bind at module level: Name = npm(\"package\") or npm(\"package\", \"Export\")."),
    ("got an unexpected keyword argument", "Use the parameter names the function declares."),
    ("invalid expression", "The part inside { } must be one Python expression."),
]

# (text in the message, docs page slug); the first match wins.
DOCS = [
    ("browser code", "browser-python"), ("not supported in browser", "browser-python"),
    ("server function", "server-functions"), ("@server", "server-functions"),
    ("unexpected keyword argument", "server-functions"),
    ("component", "language"), ("prop ", "language"), ("tag", "language"), ("expression", "language"),
    ("bind=", "reactivity"), ("derived/read-only", "reactivity"),
    ("layout", "layouts"), ("{children}", "layouts"), ("npm", "npm"),
    ("variant", "ui"), ("motion", "ui"), ("Form", "forms"), ("Model", "data"), ("migration", "data"),
]


def hint_for(message):
    for needle, hint in HINTS:
        if needle in message:
            return hint
    return None


def docs_for(message):
    for needle, slug in DOCS:
        if needle in message:
            return f"{DOCS_URL}{slug}.html"
    return None


def frame(source, line, col=None, context=1):
    """The source around ``line`` (1-based) with a ``>`` marker and, given ``col`` (0-based), a caret."""
    if not source or not line:
        return ""
    lines = source.splitlines()
    if not 1 <= line <= len(lines):
        return ""
    lo, hi = max(1, line - context), min(len(lines), line + context)
    width = len(str(hi))
    out = []
    for n in range(lo, hi + 1):
        out.append(f"{'>' if n == line else ' '} {n:>{width}} | {lines[n - 1].rstrip()}")
        if n == line and col is not None and col >= 0:
            out.append(f"  {' ' * width} | {' ' * col}^")
    return "\n".join(out)


def _message(exc):
    msg = getattr(exc, "pyweb_msg", None) or getattr(exc, "msg", None) or str(exc)
    msg = str(msg)
    if msg.startswith("pyweb: "):
        msg = msg[len("pyweb: "):]
    return msg


def _column(exc, source, line, msg):
    col = getattr(exc, "col", None)
    if col is None and getattr(exc, "offset", None) and isinstance(exc, SyntaxError) \
            and not getattr(exc, "pyweb_msg", None):
        col = exc.offset - 1
    if col is None and source and line:                  # point at the quoted name the message is about
        lines = source.splitlines()
        if 1 <= line <= len(lines):
            for pattern in (r"'([^']+)'", r"<([A-Za-z][\w.]*)>"):  # a quoted name first, else the tag
                m = re.search(pattern, msg)
                at = lines[line - 1].find(m.group(1)) if m else -1
                if at >= 0:
                    col = at
                    break
    return col


def describe(exc, source=None, filename=None):
    """A record for showing an error: ``{file, line, col, message, hint, docs, frame}`` (empty keys dropped)."""
    msg = _message(exc)
    line = getattr(exc, "lineno", None) or getattr(exc, "line", None)
    line = line if isinstance(line, int) and line > 0 else None
    col = _column(exc, source, line, msg)
    rec = {"file": getattr(exc, "filename", None) or filename, "line": line, "col": col, "message": msg,
           "hint": hint_for(msg), "docs": docs_for(msg), "frame": frame(source, line, col)}
    return {k: v for k, v in rec.items() if v not in (None, "")}


def pretty(exc, source=None, filename=None):
    """The error as terminal text: ``file:line: message``, the code frame, then the hint and docs link."""
    rec = describe(exc, source, filename)
    where = rec.get("file") or "<pyweb>"
    if rec.get("line"):
        where += f":{rec['line']}"
    out = [f"{where}: {rec['message']}"]
    if rec.get("frame"):
        out += ["", rec["frame"], ""]
    if rec.get("hint"):
        out.append(f"hint: {rec['hint']}")
    if rec.get("docs"):
        out.append(f"docs: {rec['docs']}")
    return "\n".join(out)


def _is_app_frame(filename):
    import os
    if not filename or filename.startswith("<"):
        return False
    if filename.endswith(".pyweb"):
        return True
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the pyweb package
    path = os.path.abspath(filename)
    return not (path.startswith(here + os.sep) or "site-packages" in path or "dist-packages" in path
                or path.startswith(os.path.dirname(os.__file__)))


def _function_name(name):
    for prefix in ("__pyweb_state_", "__pyweb_page_", "__pyweb_"):
        if name.startswith(prefix):
            return name[len(prefix):] or name
    return name


def explain_exception(exc):
    """A runtime error for the dev error page: the error, then the frames in the app's own files (newest
    first, each with its source lines), then the full traceback."""
    import linecache
    import traceback
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if _is_app_frame(f.filename)]
    head = f"{type(exc).__name__}: {exc}"
    if not frames:
        return head + "\n\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    out = [head, "", "In your code (most recent call first):"]
    for f in reversed(frames):
        out.append(f"  {f.filename}:{f.lineno}, in {_function_name(f.name)}")
        lines = [linecache.getline(f.filename, n).rstrip() for n in range(max(1, f.lineno - 1), f.lineno + 1)]
        for n, text in zip(range(max(1, f.lineno - 1), f.lineno + 1), lines):
            if text or n == f.lineno:
                out.append(f"    {'>' if n == f.lineno else ' '} {n:>4} | {text}")
    out += ["", "Full traceback:", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()]
    return "\n".join(out)
