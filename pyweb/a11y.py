"""Accessibility checks on an app's markup (``pyweb check`` and the editor show them).

Each finding names the rule and the line: images without ``alt``, clicks on elements a keyboard
can't reach, form fields without a label, buttons and links without a name, positive
``tabindex``, and an empty page language. They're warnings: ``pyweb check --strict`` fails on
them.
"""

from __future__ import annotations

import ast

STATIC = {"div", "span", "li", "p", "td", "tr", "section", "article", "img", "svg", "main", "header", "footer",
          "nav", "aside", "ul", "ol", "label", "h1", "h2", "h3", "h4", "h5", "h6"}
UNLABELLED_TYPES = {"hidden", "submit", "button", "reset", "image"}
NAMED_BY = ("aria-label", "aria-labelledby", "title")
FIELD_NAMED_BY = NAMED_BY + ("placeholder",)   # a last-resort name, as browsers and axe accept it


def _attr(node, name):
    v = node.attrs.get(name)
    if v is None:
        return None
    if v is True:
        return True
    return v[1] if v[0] == "lit" else ("expr", v[1])


def _has(node, *names):
    return any(n in node.attrs for n in names) or any(k.startswith("**") for k in node.attrs)


def _children(n):
    for kids in (getattr(n, "children", None), getattr(n, "body", None), getattr(n, "orelse", None)):
        yield from kids or ()


def _has_text(n):
    """Whether markup under ``n`` gives it a name: text, an expression, or an image with alt."""
    for c in _children(n):
        kind = type(c).__name__
        if kind == "TextNode" and c.text.strip():
            return True
        if kind in ("ExprNode", "SlotNode", "MarkdownNode"):
            return True
        tag = getattr(c, "tag", None)
        if tag is not None and tag[:1].isupper():
            return True                               # a component: assume it renders text
        if tag == "img":
            alt = _attr(c, "alt")
            if isinstance(alt, tuple) or (isinstance(alt, str) and alt.strip()):
                return True
            continue
        if tag is not None and (_has(c, "aria-label", "aria-labelledby") or _has_text(c)):
            return True
        if kind in ("ControlIf", "ControlFor") and _has_text(c):
            return True
    return False


def _walk(nodes, ancestors=()):
    for n in nodes or ():
        yield n, ancestors
        tag = getattr(n, "tag", None)
        yield from _walk(list(_children(n)), ancestors + ((tag,) if tag else ()))


def lint_ui(ui):
    """``[(line, rule, message)]`` for parsed markup."""
    nodes = list(_walk(ui))
    labelled = {_attr(n, "for") for n, _a in nodes if getattr(n, "tag", None) == "label"
                and isinstance(_attr(n, "for"), str)}
    out = []
    for n, ancestors in nodes:
        tag = getattr(n, "tag", None)
        if tag is None or not hasattr(n, "attrs"):
            continue
        line = getattr(n, "line", 0)
        spread = any(k.startswith("**") for k in n.attrs)
        if tag == "img" and "alt" not in n.attrs and not spread:
            out.append((line, "img-alt", '<img> needs alt="…" (or alt="" when it is decoration)'))
        if tag in STATIC and "onclick" in n.attrs and not (_has(n, "role") and _has(n, "tabindex")):
            out.append((line, "click-static", f"<{tag} onclick> can't be reached with a keyboard: use <button> "
                                              f'(or <Button>), or add role="button" and tabindex="0"'))
        if tag in ("input", "select", "textarea") and not spread:
            typ = _attr(n, "type")
            if not (isinstance(typ, str) and typ in UNLABELLED_TYPES) and not _has(n, *FIELD_NAMED_BY) \
                    and "label" not in ancestors and _attr(n, "id") not in labelled:
                out.append((line, "input-label", f"<{tag}> has no label: wrap it in <label>, give it an id that a "
                                                 f'<label for> names, or aria-label="…"'))
        if tag in ("button", "Button") and not _has(n, *NAMED_BY) and not _has_text(n):
            out.append((line, "button-name", "this button has no text: screen readers can't name it "
                                             '(add aria-label="…")'))
        if tag == "a" and "href" in n.attrs and not _has(n, *NAMED_BY) and not _has_text(n):
            out.append((line, "link-name", 'this link has no text: add some, or aria-label="…"'))
        tab = _attr(n, "tabindex")
        if isinstance(tab, str) and tab.strip().lstrip("-").isdigit() and int(tab) > 0:
            out.append((line, "tabindex-positive", "a positive tabindex changes the page's tab order; use 0 or -1"))
    return out


def lint_app(tree):
    """``[(line, rule, message)]`` from the Python side: an empty page language."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "App":
            for k in node.keywords:
                if k.arg == "lang" and isinstance(k.value, ast.Constant) and not k.value.value:
                    out.append((node.lineno, "html-lang", 'App(lang=...) is empty: screen readers need the '
                                                          'page language (lang="en")'))
    return out


def check(text, path=None):
    """Findings for a .pyweb source, as ``pyweb check`` prints them."""
    from .lsp_data import parse
    tree, ui = parse(text, path)
    if tree is None:
        return []
    found = sorted(lint_ui(ui) + lint_app(tree))
    return [{"kind": "a11y-" + rule, "line": line, "message": msg} for line, rule, msg in found]


# ------------------------------------------------------------------ rendered pages

def check_html(html):
    """The same rules on a rendered page (what the markup became, components included): a list of
    ``{"rule", "element", "message"}``. ``TestClient.a11y(path)`` calls it."""
    from .htmlquery import Node, parse
    doc = parse(html)
    out = []

    def report(rule, node, message):
        out.append({"rule": rule, "element": f"<{node.tag}{' id=' + node.attrs['id'] if 'id' in node.attrs else ''}>",
                    "message": message})

    def named(node, by=NAMED_BY):
        return bool(node.text or any((node.attrs.get(a) or "").strip() for a in by)
                    or any(isinstance(c, Node) and c.tag == "img" and (c.attrs.get("alt") or "").strip()
                           for c in node.children))

    html_el = doc.find("html")
    if html_el is not None and not (html_el.attrs.get("lang") or "").strip():
        report("html-lang", html_el, "the page has no language: <html lang=\"en\">")
    labelled = {n.attrs.get("for") for n in doc.find_all("label") if n.attrs.get("for")}
    for img in doc.find_all("img"):
        if "alt" not in img.attrs and img.attrs.get("role") != "presentation":
            report("img-alt", img, "image without alt= (alt=\"\" if it's decoration)")
    for field in doc.find_all("input, select, textarea"):
        if field.tag == "input" and (field.attrs.get("type") or "text") in UNLABELLED_TYPES:
            continue
        inside_label, p = False, field.parent
        while p is not None:
            if p.tag == "label":
                inside_label = True
                break
            p = p.parent
        if not (inside_label or field.attrs.get("id") in labelled
                or any((field.attrs.get(a) or "").strip() for a in FIELD_NAMED_BY)):
            report("label", field, "form field without a label (<label for=...>, or aria-label=)")
    for b in doc.find_all("button"):
        if not named(b):
            report("button-name", b, "button without a name: text inside, or aria-label=")
    for a in doc.find_all("a[href]"):
        if not named(a):
            report("link-name", a, "link without a name: text inside, or aria-label=")
    for n in doc.find_all("[tabindex]"):
        try:
            if int(n.attrs["tabindex"]) > 0:
                report("tabindex", n, "positive tabindex changes the keyboard order: use 0 or -1")
        except ValueError:
            pass
    return out
