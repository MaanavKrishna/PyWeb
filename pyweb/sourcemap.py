"""Source maps (v3) from a page's browser module back to its ``.pyweb`` file.

Each line of the generated JavaScript points at the ``.pyweb`` line it came from, as far as the
compiler records it: a handler's body at the handler (``def add():``), a state variable at its
assignment, the rest at the page. Browser stack traces and devtools breakpoints then land in the
``.pyweb`` file. ``pyweb dev`` serves them (with the source); ``pyweb build`` writes them next to
each module (without the source, which also holds your server code) and links them with
``--sourcemaps``.
"""

from __future__ import annotations

import re

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _vlq(n):
    v = (-n << 1) | 1 if n < 0 else n << 1
    out = ""
    while True:
        digit = v & 31
        v >>= 5
        if v:
            digit |= 32
        out += _B64[digit]
        if not v:
            return out


def _decode(segment):
    """Inverse of :func:`_vlq` for a whole segment (tests)."""
    values, shift, value = [], 0, 0
    for ch in segment:
        digit = _B64.index(ch)
        value += (digit & 31) << shift
        if digit & 32:
            shift += 5
            continue
        values.append(-(value >> 1) if value & 1 else value >> 1)
        shift, value = 0, 0
    return values


def line_table(js, marks, default_line):
    """The ``.pyweb`` line (1-based) for each line of ``js``. ``marks`` is the compiled page's
    ``sourcemap``: ``("handler add", 30)``, ``("sig todos", 23)``."""
    handlers = {label.split(" ", 1)[1]: line for label, line in marks if label.startswith("handler ") and line}
    signals = {label.split(" ", 1)[1]: line for label, line in marks if label.startswith("sig ") and line}
    out, current, depth = [], default_line, 0
    for text in js.split("\n"):
        m = re.match(r"\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", text)
        s = re.match(r"\s*(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*\$signal\(", text)
        if m and m.group(1) in handlers:
            current, depth = handlers[m.group(1)], 0
        if s and s.group(1) in signals:
            out.append(signals[s.group(1)])
            continue
        out.append(current)
        if current != default_line:
            depth += text.count("{") - text.count("}")
            if depth <= 0 and "}" in text:
                current = default_line
    return out


def build(js, marks, *, source_name, default_line=1, source_text=None, file=None):
    """A v3 source map (a dict) for ``js``."""
    lines = line_table(js, marks, default_line)
    segments, prev = [], 0
    for line in lines:
        src = max(0, line - 1)
        segments.append("A" + ("A" if not segments else "A") + _vlq(src - prev) + "A")
        prev = src
    out = {"version": 3, "file": file or "", "sources": [source_name], "names": [],
           "mappings": ";".join(segments)}
    if source_text is not None:
        out["sourcesContent"] = [source_text]
    return out
