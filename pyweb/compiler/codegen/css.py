"""CSS extraction: <style> blocks + css={...} props -> hashed .css bundle."""
from __future__ import annotations

import hashlib
import re

from pyweb.compiler.ast import (
    Attr,
    CompUse,
    Cond,
    CssDyn,
    CssStatic,
    Dyn,
    Element,
    For,
    Static,
    Style,
    iter_nodes,
)


def _scope_css(css: str, scope: str) -> str:
    """Scope bare selectors under ``.scope`` so component styles don't leak."""
    out: list[str] = []
    for chunk in css.split("}"):
        if "{" not in chunk:
            if chunk.strip():
                out.append(chunk)
            continue
        head, body = chunk.split("{", 1)
        selectors = []
        for sel in head.split(","):
            sel = sel.strip()
            if not sel:
                continue
            if sel.startswith("@") or sel.startswith(":") or sel.startswith(".pw-"):
                selectors.append(sel)
            else:
                selectors.append(f".{scope} {sel}")
            # note: @media inner rules are left as-is (documented)
        if selectors:
            out.append(", ".join(selectors) + " {" + body)
    css = "}".join(out)
    if out and not css.endswith("}"):
        css += "}"
    return css


def extract(doc_nodes: list, styles: list[Style], route: str) -> tuple[str, dict[int, str]]:
    """Return (css_text, hid -> scoped class). Static css=... becomes classes."""
    digest = hashlib.sha256(route.encode()).hexdigest()[:8]
    parts: list[str] = []
    mapping: dict[int, str] = {}
    counter = 0

    def scoped(css_text: str) -> str:
        nonlocal counter
        cls = f"pw-{digest}-{counter}"
        counter += 1
        parts.append(f"/* scope {cls} */\n{_scope_css(css_text, cls)}")
        return cls

    for style in styles:
        parts.append(style.css)
    for node in _walk(doc_nodes):
        if isinstance(node, Element):
            for attr in node.attrs:
                if attr.name == "css" and isinstance(attr.value, (CssStatic, Static)):
                    text = attr.value.css if isinstance(attr.value, CssStatic) else attr.value.text
                    if text.strip() and not node.css_class:
                        node.css_class = scoped(text.strip())
                    elif text.strip():
                        parts.append(_scope_css(text.strip(), node.css_class))
    css_text = "\n".join(p for p in parts if p.strip())
    return css_text, mapping


def _walk(nodes: list):
    for node in iter_nodes(nodes):
        yield node


def bundle_name(css_text: str) -> str:
    h = hashlib.sha256(css_text.encode()).hexdigest()[:10]
    return f"app.{h}.css"
