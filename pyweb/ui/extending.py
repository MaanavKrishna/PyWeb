"""Your own variants for the kit's components.

    from pyweb import ui

    ui.extend("Button", variant={
        "brand": {"background": "var(--pw-accent-11)", "color": "white",
                  "hover": {"background": "var(--pw-accent-12)"}},
    }, size={"xl": {"height": "52px", "padding": "0 28px", "font-size": "var(--pw-text-lg)"}})

    <Button variant="brand" size="xl">Get started</Button>

Each value becomes a CSS rule for the component's part in the theme stylesheet, inside the kit's
cascade layer (so your own CSS still wins): ``.pw-button[data-variant="brand"] {...}``.
Sub-dicts named hover, focus, active, disabled or open style that state. The same dict can go in
``Theme(extend={"Button": {"variant": {...}}})``.
"""

from __future__ import annotations

import os
import re

DIMENSIONS = ("variant", "size", "tone")
STATES = {"hover": ":hover", "focus": ":focus-visible", "active": ":active",
          "disabled": ":is(:disabled, [aria-disabled=\"true\"])", "open": "[data-state=\"open\"]",
          "checked": ":is(:checked, [data-state=\"on\"], [aria-checked=\"true\"])"}
_PROP = re.compile(r"^(--[\w-]+|[a-z][a-z-]*)$")
_VALUE_BAD = re.compile(r"[;{}<>\\]|/\*|\*/|expression\(|javascript:", re.I)
_URL = re.compile(r"url\(\s*['\"]?([^'\")]*)", re.I)
_NAME = re.compile(r"^[\w-]+$")

_EXTENDED: dict = {}
_ROOTS: dict = {}
_DEFAULTS: dict = {}


def roots() -> dict:
    """``{component: root class}`` for the kit (Button -> pw-button), from the kit's files."""
    if not _ROOTS:
        from pyweb.compiler.pipeline import KIT_DIR
        for fname in sorted(os.listdir(KIT_DIR)):
            if not fname.endswith(".pyweb"):
                continue
            with open(os.path.join(KIT_DIR, fname), encoding="utf-8") as fh:
                src = fh.read()
            for m in re.finditer(r"^def ([A-Z]\w*)\(.*?(?=^def |\Z)", src, re.M | re.S):
                cls = re.search(r'class="(pw-[\w-]+)', m.group(0))
                if cls:
                    _ROOTS[m.group(1)] = cls.group(1)
                sig = m.group(0).split("):", 1)[0]
                _DEFAULTS[m.group(1)] = dict(re.findall(rf'\b({"|".join(DIMENSIONS)})="([\w-]+)"', sig))
    return _ROOTS


def _decl(prop, value):
    prop = str(prop).strip()
    prop = prop.replace("_", "-") if not prop.startswith("--") else prop
    if not _PROP.match(prop):
        raise ValueError(f"{prop!r} isn't a CSS property name")
    v = str(value).strip()
    if _VALUE_BAD.search(v):
        raise ValueError(f"CSS value for {prop} can't contain ; {{ }} < > \\ or comments: {v!r}")
    for url in _URL.findall(v):
        u = url.strip()
        if not (u.startswith("/") and not u.startswith("//") or u.startswith("https://")):
            raise ValueError(f"url() in {prop} must be a path or an https URL")
    return f"{prop}:{v}"


def _rules(selector, style):
    if isinstance(style, str):
        decls = [d.strip() for d in style.split(";") if d.strip()]
        body = ";".join(_decl(*d.split(":", 1)) for d in decls)
        return [f"{selector}{{{body}}}"] if body else []
    if not isinstance(style, dict):
        raise TypeError("a variant's style is a dict of CSS properties (or a CSS string)")
    plain = {k: v for k, v in style.items() if not isinstance(v, dict)}
    out = [f"{selector}{{{';'.join(_decl(k, v) for k, v in plain.items())}}}"] if plain else []
    for state, sub in style.items():
        if isinstance(sub, dict):
            if state not in STATES:
                raise ValueError(f"unknown state {state!r} (states: {', '.join(STATES)})")
            out += _rules(selector + STATES[state], sub)
    return out


def css_for(table: dict) -> str:
    """CSS for ``{component: {dimension: {value: style}}}``."""
    out = []
    known = roots()
    for component, dims in table.items():
        if component not in known:
            raise ValueError(f"no kit component {component!r} to extend")
        for dim, values in dims.items():
            if dim not in DIMENSIONS:
                raise ValueError(f"{component} variants are variant=, size= or tone=, not {dim}=")
            for value, style in values.items():
                if not _NAME.match(str(value)):
                    raise ValueError(f"variant names are letters, digits, '-' and '_' (not {value!r})")
                out += _rules(f'.{known[component]}[data-{dim}="{value}"]', style)
    return "@layer pyweb.components{" + "".join(out) + "}\n" if out else ""


def extend(component: str, variant: dict | None = None, size: dict | None = None, tone: dict | None = None):
    """Add variants to a kit component (see the module docs)."""
    dims = {k: v for k, v in (("variant", variant), ("size", size), ("tone", tone)) if v}
    css_for({component: dims})                        # validate now, not on the first request
    entry = _EXTENDED.setdefault(component, {})
    for dim, values in dims.items():
        entry.setdefault(dim, {}).update(values)


def registered() -> dict:
    return {c: {d: dict(v) for d, v in dims.items()} for c, dims in _EXTENDED.items()}


def css(theme=None) -> str:
    """Extensions from ``ui.extend`` calls and the theme's ``extend=``."""
    table = registered()
    for component, dims in ((getattr(theme, "extend", None) or {}).items()):
        for dim, values in dims.items():
            table.setdefault(component, {}).setdefault(dim, {}).update(values)
    return css_for(table)


def values(component: str) -> dict:
    """``{dimension: [values]}`` a kit component accepts: the kit's own (from ui.css) and extensions."""
    from .serve import _file
    root = roots().get(component)
    out = {d: [] for d in DIMENSIONS}
    for dim, val in _DEFAULTS.get(component, {}).items():
        out[dim].append(val)
    if root:
        text = _file("ui.css")[0].decode()
        for dim, val in re.findall(rf'\.{re.escape(root)}\[data-({"|".join(DIMENSIONS)})="([\w-]+)"\]', text):
            if val not in out[dim]:
                out[dim].append(val)
    # A base style has no [data-variant] rule of its own: take the defaults of every component on the
    # same root class too (DialogClose renders a pw-button, so Button's variant="default" works on it).
    for other, other_root in roots().items():
        if root and other_root == root and other != component:
            for dim, val in _DEFAULTS.get(other, {}).items():
                if val not in out[dim]:
                    out[dim].append(val)
    for dim, vals in (registered().get(component) or {}).items():
        out[dim] += [v for v in vals if v not in out[dim]]
    return {d: v for d, v in out.items() if v}
