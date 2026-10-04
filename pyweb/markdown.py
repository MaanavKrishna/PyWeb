"""Safe Markdown to HTML, for the ``<Markdown text={...} />`` component.

The same rules are implemented in the browser runtime (``renderMarkdown``)
so text streamed into the page renders exactly as the server rendered it.

Safety: raw HTML is never passed through (``<b>`` shows as text), every
piece of text is escaped, and links and images only accept ``http(s)``,
``mailto``, relative and ``#`` URLs. The output only contains the tags
listed in :data:`TAGS`.

Supported: paragraphs, ``#`` headings, ``**bold**``, ``*italic*``,
``~~strike~~``, ```` `code` ````, fenced code blocks (an unclosed fence
runs to the end, which suits streamed text), ``-``/``*``/``1.`` lists
(nested by indentation), ``>`` quotes, ``---`` rules, tables, links,
images, ``<https://...>`` autolinks and hard line breaks (two trailing
spaces or a backslash).
"""

from __future__ import annotations

import re

TAGS = ("p", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "code", "pre", "ul", "ol", "li",
        "blockquote", "hr", "br", "a", "img", "table", "thead", "tbody", "tr", "th", "td")

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$")
_RULE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_QUOTE = re.compile(r"^ {0,3}> ?")
_ITEM = re.compile(r"^( *)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_TABLE_SEP = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_URL = r"((?:[^()\s]|\([^()\s]*\))+)"   # one level of balanced parentheses, as in Wikipedia links
_SAFE_URL = re.compile(r"^(https?:|mailto:|/|#|\./|\.\./|[\w.-]+(/|$))", re.I)


def escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def safe_url(url: str):
    url = url.strip()
    if not url or (re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I) and not re.match(r"^(https?|mailto):", url, re.I)):
        return None
    return url if _SAFE_URL.match(url) else None


# ------------------------------------------------------------------ inline

def inline(text: str) -> str:
    """Inline markdown (code, links, images, emphasis) in one line or paragraph."""
    saved: list[str] = []

    def keep(html):
        saved.append(html)
        return f"\x00{len(saved) - 1}\x00"

    out, i = [], 0
    while i < len(text):               # code spans first: nothing inside them is markdown
        if text[i] == "`":
            run = len(text[i:]) - len(text[i:].lstrip("`"))
            close = text.find("`" * run, i + run)
            while close != -1 and close + run < len(text) and text[close + run] == "`":
                close = text.find("`" * run, close + run + 1)
            if close != -1:
                code = text[i + run:close]
                if code.startswith(" ") and code.endswith(" ") and code.strip():
                    code = code[1:-1]
                out.append(keep(f"<code>{escape(code)}</code>"))
                i = close + run
                continue
            out.append("`" * run)
            i += run
            continue
        out.append(text[i])
        i += 1
    text = escape("".join(out))

    def image(m):
        url = safe_url(_unescape(m.group(2)))
        if url is None:
            return keep(escape(_unescape(m.group(1))))
        return keep(f'<img src="{escape(url)}" alt="{m.group(1)}">')

    def link(m):
        url = safe_url(_unescape(m.group(2)))
        label = _emphasis(m.group(1))
        if url is None:
            return keep(label)
        return keep(f'<a href="{escape(url)}" rel="noopener noreferrer">{label}</a>')

    def auto(m):
        url = _unescape(m.group(1))
        return keep(f'<a href="{escape(url)}" rel="noopener noreferrer">{escape(url)}</a>')

    text = re.sub(r"!\[([^\]\n]*)\]\(" + _URL + r"\)", image, text)
    text = re.sub(r"\[([^\]\n]+)\]\(" + _URL + r"\)", link, text)
    text = re.sub(r"&lt;(https?://[^\s&]+)&gt;", auto, text)
    text = _emphasis(text)
    text = re.sub(r"(?: {2,}|\\)\n", "<br>", text)
    while "\x00" in text:
        text = re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], text)
    return text


def _unescape(text):
    return (text.replace("&#39;", "'").replace("&quot;", '"').replace("&gt;", ">")
            .replace("&lt;", "<").replace("&amp;", "&"))


def _emphasis(text):
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w])__(?=\S)(.+?)(?<=\S)__(?![\w])", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(?=[^\s*])(.+?)(?<=[^\s*])\*", r"<em>\1</em>", text)
    text = re.sub(r"(?<![\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w])", r"<em>\1</em>", text)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", text)
    return text


# ------------------------------------------------------------------ blocks

def render(text) -> str:
    """Markdown ``text`` as safe HTML."""
    if text is None:
        return ""
    lines = str(text).replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").split("\n")
    return _blocks(lines)


def _starts_block(line):
    return bool(_FENCE.match(line) or _HEADING.match(line) or _RULE.match(line) or _QUOTE.match(line)
                or _ITEM.match(line))


def _blocks(lines):
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = _FENCE.match(line)
        if m:
            fence, lang = m.group(1), m.group(2)
            body, i = [], i + 1
            while i < n and not re.match(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$",
                                         lines[i]):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{escape(chr(10).join(body))}</code></pre>")
            continue
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2) or '')}</h{level}>")
            i += 1
            continue
        if _RULE.match(line):
            out.append("<hr>")
            i += 1
            continue
        if _QUOTE.match(line):
            body = []
            while i < n and lines[i].strip() and (_QUOTE.match(lines[i]) or not _starts_block(lines[i])):
                body.append(_QUOTE.sub("", lines[i], count=1))
                i += 1
            out.append(f"<blockquote>{_blocks(body)}</blockquote>")
            continue
        m = _ITEM.match(line)
        if m:
            html, i = _list(lines, i)
            out.append(html)
            continue
        if "|" in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            html, i = _table(lines, i)
            out.append(html)
            continue
        para = []
        while i < n and lines[i].strip() and not (para and _starts_block(lines[i])):
            para.append(lines[i].strip() if not lines[i].endswith("  ") else lines[i].lstrip())
            i += 1
        out.append(f"<p>{inline(chr(10).join(para))}</p>")
    return "".join(out)


def _list(lines, i):
    first = _ITEM.match(lines[i])
    indent = len(first.group(1))
    ordered = first.group(2)[0].isdigit()
    start = int(first.group(2)[:-1]) if ordered else 1
    items, n = [], len(lines)
    while i < n:
        m = _ITEM.match(lines[i])
        if not m or len(m.group(1)) != indent or m.group(2)[0].isdigit() != ordered:
            break
        body, i = [m.group(3)], i + 1
        loose = False
        while i < n:
            line = lines[i]
            if not line.strip():
                nxt = lines[i + 1] if i + 1 < n else ""
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) > indent:
                    body.append("")
                    loose = True
                    i += 1
                    continue
                break
            sub = _ITEM.match(line)
            if sub and len(sub.group(1)) <= indent:
                break
            if not sub and len(line) - len(line.lstrip()) <= indent and _starts_block(line):
                break
            body.append(line[indent + 2:] if line.startswith(" " * (indent + 2)) else line.lstrip())
            i += 1
        html = _blocks(body)
        if not loose and html.startswith("<p>") and html.count("<p>") == 1:
            html = html[3:].replace("</p>", "", 1)
        items.append(f"<li>{html}</li>")
    tag = "ol" if ordered else "ul"
    attr = f' start="{start}"' if ordered and start != 1 else ""
    return f"<{tag}{attr}>{''.join(items)}</{tag}>", i


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def _table(lines, i):
    head = _cells(lines[i])
    i += 2
    rows = []
    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
        rows.append(_cells(lines[i]))
        i += 1
    width = len(head)
    html = "<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead>"
    if rows:
        html += "<tbody>" + "".join(
            "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in (row + [""] * width)[:width]) + "</tr>"
            for row in rows) + "</tbody>"
    return html + "</table>", i


def Markdown(text="", **_attrs):  # noqa: N802 - used as a component name: <Markdown text={...} />
    """``<Markdown text={...} />`` in markup; called from Python it returns the safe HTML."""
    from .ssr import Markup
    return Markup(f'<div class="markdown">{render(text)}</div>')
