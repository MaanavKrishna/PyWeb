"""Editor support for the UI kit and motion: completions for variant/size/tone values, motion
presets, Theme(...) and Motion(...) settings and transition keys, and warnings for values the
kit or the motion engine doesn't know (plus the accessibility lint)."""

from __future__ import annotations

import ast
import re

TRANSITION_KEYS = ("type", "stiffness", "damping", "mass", "bounce", "duration", "ease", "delay", "repeat",
                   "repeat_type", "velocity", "stagger_children", "delay_children", "stagger_direction", "times")
THEME_KEYS = ("accent", "gray", "radius", "font", "density", "dark", "extend")
MOTION_KEYS = ("feel", "speed", "reduced")
SPRING_RANGES = {"stiffness": (1, 2000), "damping": (0, 200), "mass": (0.05, 20), "bounce": (0, 1),
                 "duration": (0, 30)}


def _kit_imports(tree):
    """Names this file imports from pyweb.ui (all of them for ``import *``)."""
    from pyweb.ui import components
    out = set()
    for node in getattr(tree, "body", []) if tree is not None else []:
        if isinstance(node, ast.ImportFrom) and node.module == "pyweb.ui":
            for a in node.names:
                out |= set(components()) if a.name == "*" else {a.asname or a.name}
    return out


def _app_extensions(text):
    """``{component: {dimension: [values]}}`` from ``ui.extend("X", variant={"a": ...})`` in the file."""
    out = {}
    from pyweb.compiler.parser import split_sources
    try:
        tree = ast.parse(split_sources(text)[0])
    except (SyntaxError, ValueError):
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == "extend" \
                and node.args and isinstance(node.args[0], ast.Constant):
            comp = out.setdefault(node.args[0].value, {})
            for k in node.keywords:
                if isinstance(k.value, ast.Dict):
                    comp.setdefault(k.arg, []).extend(
                        x.value for x in k.value.keys if isinstance(x, ast.Constant))
    return out


def _app_presets(text):
    return set(re.findall(r"""\bpreset\(\s*["']([\w-]+)["']""", text))


def allowed(component, text=""):
    from pyweb.ui.extending import values
    vals = values(component)
    for dim, extra in (_app_extensions(text).get(component) or {}).items():
        vals.setdefault(dim, [])
        vals[dim] += [v for v in extra if v not in vals[dim]]
    return vals


def completions(prefix, text, tree):
    """Completion items for the UI kit and motion at the end of ``prefix``, or None."""
    from pyweb import motion
    from pyweb.ui import KIT_HELPERS, components, theme as T
    item = lambda label, kind=12, detail="": {"label": label, "kind": kind, "detail": detail}  # noqa: E731
    if re.search(r"^\s*from\s+pyweb\.ui\s+import\s+[\w\s,]*$", prefix):
        return [item(n, 7, "UI kit component") for n in sorted(components())] + \
               [item(n, 3, "class helper") for n in KIT_HELPERS] + [item(n, 3) for n in ("Theme", "toast", "extend")]
    if re.search(r"^\s*from\s+pyweb\.motion\s+import\s+[\w\s,]*$", prefix):
        return [item(n, 3) for n in ("Motion", "preset", "animate", "timeline", "stagger", "scroll_progress",
                                     "use_transform")]
    m = re.search(r"<([A-Z]\w*)\b[^<>]*\b(variant|size|tone)=[\"']([\w-]*)$", prefix)
    if m:
        vals = allowed(m.group(1), text).get(m.group(2)) or []
        return [item(v, 12, f"{m.group(1)} {m.group(2)}") for v in vals]
    m = re.search(r"<\w[^<>]*\b(animate|initial|exit|while_\w+)=[\"']([\w-]*)$", prefix)
    if m:
        names = list(motion.BUILTIN_PRESETS) + sorted(_app_presets(text)) + (["count"] if m.group(1) == "animate" else [])
        return [item(n, 12, "motion preset") for n in names]
    if re.search(r"\btransition=\{\{[^}]*[\"'][\w]*$", prefix) or re.search(r"\btransition=\{\{\s*$", prefix):
        return [item(k, 10, "transition") for k in TRANSITION_KEYS]
    m = re.search(r"\b(density|dark|feel|reduced)=[\"']([\w-]*)$", prefix)
    if m:
        values = {"density": list(T.DENSITY), "dark": ["auto", "light", "dark"], "feel": list(motion.FEELS),
                  "reduced": list(motion.REDUCED)}[m.group(1)]
        return [item(v) for v in values]
    if re.search(r"\bTheme\.preset\(\s*[\"'][\w-]*$", prefix) or re.search(r"\bApp\([^)]*\btheme=[\"'][\w-]*$", prefix):
        return [item(n, 12, "theme preset") for n in T.PRESETS]
    m = re.search(r"\b(Theme|Motion)\(([^()]*)$", prefix)
    if m and not re.search(r"=\s*[\"'][^\"']*$", m.group(2)):
        used = set(re.findall(r"(\w+)\s*=", m.group(2)))
        keys = THEME_KEYS if m.group(1) == "Theme" else MOTION_KEYS
        return [{"label": k, "kind": 5, "insertText": k + "=", "detail": m.group(1)} for k in keys if k not in used]
    return None


def _walk(nodes):
    for n in nodes or ():
        yield n
        for kids in (getattr(n, "children", None), getattr(n, "body", None), getattr(n, "orelse", None)):
            yield from _walk(kids)


def _motion_spec(node):
    """The motion attributes of an element, as literals where they're literal."""
    v = node.attrs.get("data-pw-motion")
    if not isinstance(v, tuple) or v[0] != "expr":
        return {}
    try:
        call = ast.parse(v[1], mode="eval").body
        d = call.args[0]
    except (SyntaxError, IndexError, AttributeError):
        return {}
    out = {}
    for k, val in zip(d.keys, d.values):
        try:
            out[ast.literal_eval(k)] = ast.literal_eval(val)
        except (ValueError, SyntaxError, TypeError):
            pass
    return out


def problems(text, tree, ui):
    """``[(line, code, message)]`` for kit props and motion values that won't work."""
    from pyweb import motion
    if tree is None:
        return []
    out = []
    kit = _kit_imports(tree)
    presets = set(motion.BUILTIN_PRESETS) | _app_presets(text) | {"count"}
    for n in _walk(ui):
        tag = getattr(n, "tag", None)
        if tag is None or not hasattr(n, "attrs"):
            continue
        line = getattr(n, "line", 0)
        if tag in kit:
            vals = allowed(tag, text)
            for dim, ok in vals.items():
                v = n.attrs.get(dim)
                if isinstance(v, tuple) and v[0] == "lit" and v[1] not in ok:
                    from .compiler.errors import suggest
                    best = suggest(v[1], ok)
                    tip = f"did you mean {best!r}? " if best else ""
                    out.append((line, "unknown-variant", f"<{tag}> has no {dim} {v[1]!r} "
                                                         f"({tip}{', '.join(ok)}; add your own with ui.extend)"))
        spec = _motion_spec(n)
        variants = spec.get("variants") if isinstance(spec.get("variants"), dict) else {}
        for key in ("initial", "animate", "exit"):
            v = spec.get(key)
            if isinstance(v, str) and v not in presets and v not in variants:
                from .compiler.errors import did_you_mean
                out.append((line, "unknown-preset", f"{key}={v!r} is neither a preset ({', '.join(motion.BUILTIN_PRESETS)}) "
                                                    f"nor one of this element's variants{did_you_mean(v, list(presets) + list(variants))}"))
        tr = spec.get("transition")
        if isinstance(tr, dict):
            for k, (lo, hi) in SPRING_RANGES.items():
                val = tr.get(k)
                if isinstance(val, (int, float)) and not isinstance(val, bool) and not lo <= val <= hi:
                    out.append((line, "motion-range", f"transition {k}={val} is outside {lo}–{hi}"))
            if tr.get("type") not in (None, "spring", "tween", "inertia", "keyframes", "instant"):
                out.append((line, "motion-range", f"transition type {tr.get('type')!r} isn't spring, tween or inertia"))
    return out


def check(text, path=None):
    """Kit, motion and teams problems as ``pyweb check`` findings: ``[{kind, line, message}]``."""
    from . import lsp_data, lsp_teams
    try:
        tree, ui = lsp_data.parse(text, path)
    except Exception:  # noqa: BLE001 - the compiler reports files that don't parse
        return []
    if tree is None:
        return []
    return [{"kind": code, "line": line, "message": msg}
            for line, code, msg in problems(text, tree, ui) + lsp_teams.problems(text, tree)]
