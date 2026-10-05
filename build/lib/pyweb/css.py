"""Styling pipeline: scoped modules, tokens, dict CSS, Tailwind passthrough.

Four levels, no lock-in — plain ``.css`` files keep working untouched:

1. ``css({...})`` — Python dict -> scoped class name + stylesheet text.
2. :class:`Tokens` — design tokens -> ``:root`` CSS variables.
3. :func:`module` — ``.module.css``-style scoping: rewrites selectors
   with a content hash so component styles never leak.
4. Tailwind — ``tw("...")`` marks utility strings as intentional so the
   compiler preserves them verbatim and the build can run the Tailwind
   CLI over emitted HTML when configured.
"""

from __future__ import annotations

import hashlib
import re

__all__ = ["css", "Tokens", "module", "tw", "extract_styles",
           "is_tailwind", "TAILWIND_MARK"]


def _hash(text: str, n=6) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def css(rules: dict, *, name="c") -> tuple:
    """``css({"padding": 12, "border_radius": 8})`` -> ``(cls, stylesheet)``.

    Underscores become hyphens; bare numbers become ``px`` (except for
    unitless properties like ``z_index``, ``opacity``, ``flex``).
    """
    _UNIT_LESS = {"z-index", "opacity", "flex", "flex-grow", "flex-shrink",
                  "order", "font-weight", "line-height", "zoom"}
    decls = []
    for prop, value in rules.items():
        css_prop = prop.replace("_", "-")
        if isinstance(value, (int, float)) and css_prop not in _UNIT_LESS:
            value = f"{value}px"
        decls.append(f"{css_prop}:{value}")
    cls = f"{name}_{_hash(repr(sorted(rules.items())))}"
    return cls, f".{cls}{{{';'.join(decls)}}}"


class Tokens:
    """Design tokens -> ``:root`` variables + typed Python access.

    ``Tokens(primary="#635bff").var("primary")`` -> ``"var(--primary)"``.
    """

    def __init__(self, **tokens):
        self._tokens = tokens

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._tokens[name]
        except KeyError:
            raise AttributeError(f"unknown token {name!r}") from None

    def var(self, name):
        if name not in self._tokens:
            raise KeyError(f"unknown token {name!r}")
        return f"var(--{name.replace('_', '-')})"

    def stylesheet(self) -> str:
        decls = ";".join(f"--{k.replace('_', '-')}:{v}"
                         for k, v in self._tokens.items())
        return f":root{{{decls}}}"


def module(css_text: str, *, namespace="m") -> tuple:
    """Scope a stylesheet: ``.btn`` -> ``.btn_<hash>``.

    Returns ``(mapping, scoped_css)`` where mapping is
    ``{"btn": "btn_<hash>"}`` for use as ``class_={m['btn']}``.
    Element selectors (``h1``, ``article``) and at-rules pass through
    untouched — only class selectors are namespaced.
    """
    digest = _hash(css_text)
    classes = sorted(set(re.findall(r"\.([A-Za-z_][\w-]*)", css_text)))
    mapping = {c: f"{c}_{namespace}_{digest}" for c in classes}

    def _repl(match):
        return "." + mapping[match.group(1)]

    scoped = re.sub(r"\.([A-Za-z_][\w-]*)", _repl, css_text)
    return mapping, scoped


TAILWIND_MARK = "tw:"


def tw(utilities: str) -> str:
    """Mark a Tailwind utility string as intentional (``tw:` prefix)."""
    return f"{TAILWIND_MARK}{utilities}"


def is_tailwind(value: str) -> bool:
    return isinstance(value, str) and value.startswith(TAILWIND_MARK)


_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.S)


def extract_styles(html: str) -> tuple:
    """Split ``<style>`` blocks out of SSR HTML -> ``(html, css)``.

    The build writes extracted CSS to hashed ``.css`` files so pages
    stream HTML without duplicated inline styles.
    """
    parts = _STYLE_RE.findall(html)
    clean = _STYLE_RE.sub("", html)
    return clean, "\n".join(parts)
