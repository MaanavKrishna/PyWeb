"""Motion: animations written as attributes, in the spirit of motion.dev.

    <li initial={{"opacity": 0, "y": 12}} animate={{"opacity": 1, "y": 0}} exit={{"opacity": 0}}>...</li>
    <div animate="fade-up">...</div>
    <button while_hover={{"scale": 1.05}} while_tap={{"scale": 0.97}}>Save</button>
    <li layout>...</li>

The attributes become one ``data-pw-motion`` attribute (JSON) that the browser's motion engine
(``runtime/browser/motion.js``) reads; the server renders the ``initial`` pose as inline style so
the first paint already starts there. ``App(motion=Motion(feel="gentle", speed=0.8))`` changes
every animation in the app and in the UI kit; ``preset(name, spec)`` adds your own presets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

#: Markup attributes the motion engine reads (``-`` or ``_`` in the names).
KEYS = (
    "initial", "animate", "exit", "transition", "variants",
    "while_hover", "while_tap", "while_focus", "while_in_view", "while_drag", "viewport",
    "drag", "drag_constraints", "drag_elastic", "drag_momentum", "drag_snap_back",
    "layout", "layout_id",
)
ATTR = "data-pw-motion"
FEELS = ("snappy", "gentle", "bouncy", "none")
REDUCED = ("auto", "always", "never")
BUILTIN_PRESETS = (
    "fade", "fade-up", "fade-down", "fade-left", "fade-right", "slide-up", "slide-down", "slide-left",
    "slide-right", "scale", "pop", "blur-in", "flip", "collapse", "shake", "pulse",
)

_PRESETS: dict = {}


def _norm(key):
    return key.replace("-", "_")


@dataclass(frozen=True)
class Motion:
    """The app's motion: ``feel`` (snappy, gentle, bouncy, or none for no animation), ``speed``
    (2.0 is twice as fast) and ``reduced``: "auto" follows the visitor's reduced-motion setting,
    "always" reduces motion for everyone, "never" ignores the setting (use with care)."""

    feel: str = "snappy"
    speed: float = 1.0
    reduced: str = "auto"

    def __post_init__(self):
        if self.feel not in FEELS:
            raise ValueError(f"Motion(feel=...) is one of {', '.join(FEELS)}, not {self.feel!r}")
        if not isinstance(self.speed, (int, float)) or isinstance(self.speed, bool) or not 0.1 <= self.speed <= 10:
            raise ValueError("Motion(speed=...) is a number from 0.1 to 10 (1.0 is normal)")
        if self.reduced not in REDUCED:
            raise ValueError(f"Motion(reduced=...) is one of {', '.join(REDUCED)}, not {self.reduced!r}")

    def config(self) -> dict:
        return {"feel": self.feel, "speed": self.speed, "reduced": self.reduced, "presets": dict(_PRESETS)}

    def css_vars(self) -> str:
        """CSS variables the UI kit's own transitions use, so they follow the same feel."""
        if self.feel == "none":
            return "--pw-duration: 0ms; --pw-duration-collapse: 0ms;"
        base = {"snappy": 160, "gentle": 240, "bouncy": 200}[self.feel]
        ease = {"snappy": "cubic-bezier(.2, .8, .2, 1)", "gentle": "cubic-bezier(.25, .1, .25, 1)",
                "bouncy": "cubic-bezier(.34, 1.56, .64, 1)"}[self.feel]
        return (f"--pw-duration: {round(base / self.speed)}ms; --pw-duration-collapse: {round(220 / self.speed)}ms; "
                f"--pw-ease: {ease};")


def config(app) -> Motion:
    """The app's Motion (the default one when it set none)."""
    m = getattr(app, "motion", None) if app is not None else None
    return m if isinstance(m, Motion) else Motion()


def coerce(value):
    """``App(motion=...)``: a Motion, a feel name, False (no animation) or None."""
    if value is None or isinstance(value, Motion):
        return value
    if value is False:
        return Motion(feel="none")
    if isinstance(value, str):
        return Motion(feel=value)
    raise TypeError("App(motion=...) takes Motion(...), a feel name ('snappy', 'gentle', 'bouncy', 'none') or False")


def preset(name: str, spec: dict) -> None:
    """Add a named animation usable anywhere: ``animate="hero-in"``. ``spec`` has ``initial``,
    ``animate``, ``exit`` and ``transition`` (all optional)."""
    if not isinstance(name, str) or not name or not all(c.isalnum() or c in "-_" for c in name):
        raise ValueError("preset names are letters, digits, '-' and '_'")
    if not isinstance(spec, dict) or not set(spec) <= {"initial", "animate", "exit", "transition"}:
        raise ValueError("a preset is a dict with initial, animate, exit and/or transition")
    json.dumps(spec)                                  # must be plain data: it's sent to the browser
    _PRESETS[name] = spec


def head_tags(app) -> str:
    m = config(app)
    data = json.dumps(m.config(), separators=(",", ":")).replace("&", "&amp;").replace("'", "&#39;")
    from .ui.serve import version
    return (f"<meta name=\"pyweb-motion\" content='{data}'>"
            '<script type="module" src="/static/motion.js"></script>'
            f'<link rel="stylesheet" href="/__pyweb/motion.css?v={version()}">')


# ------------------------------------------------------------------ markup → data-pw-motion

def rewrite(attrs: dict) -> dict:
    """Parser hook for HTML elements: motion attributes become ``data-pw-motion`` (and the
    ``initial`` pose joins ``style`` on the server). ``attrs`` values are the parser's
    ``("lit", s, line)``, ``("expr", src, line)`` or ``True``."""
    found = [k for k in attrs if _norm(k) in KEYS]
    if not found:
        return attrs
    parts = []
    initial = variants = animate = None
    for k in found:
        v = attrs[k]
        src = "True" if v is True else repr(v[1]) if v[0] == "lit" else f"({v[1]})"
        parts.append(f"{_norm(k)!r}: {src}")
        if _norm(k) == "initial":
            initial = src
        elif _norm(k) == "variants":
            variants = src
        elif _norm(k) == "animate":
            animate = src
    out = {}
    for k, v in attrs.items():
        if k == found[0]:
            out[ATTR] = ("expr", "__pw_motion__({" + ", ".join(parts) + "})", 0)
        if k in found or (k == "style" and (initial or animate)):
            continue
        out[k] = v
    if initial or animate:
        style = attrs.get("style")
        ssrc = "None" if style is None else "True" if style is True else repr(style[1]) if style[0] == "lit" \
            else f"({style[1]})"
        out["style"] = ("expr", f"__pw_initial__({initial or 'None'}, {ssrc}, {variants or 'None'}, "
                                f"{animate or 'None'})", 0)
    return out


def props(extra: dict) -> dict:
    """Component props that aren't parameters (they go to ``**attrs``): motion keys become
    ``data-pw-motion`` (and the initial pose joins ``style``). Values are already evaluated."""
    if "motion" in extra:                             # motion=False on a kit component: no animation
        extra = dict(extra)
        m = extra.pop("motion")
        if m is False or m == "none":
            extra["data-motion"] = "none"
        elif m not in (None, True):
            extra.setdefault("animate", m)
    found = {_norm(k): v for k, v in extra.items() if _norm(k) in KEYS}
    if not found:
        return extra
    out = {k: v for k, v in extra.items() if _norm(k) not in KEYS}
    out[ATTR] = encode(found)
    if "initial" in found or "animate" in found:
        out["style"] = _initial(found.get("initial"), extra.get("style"), found.get("variants"), found.get("animate"))
    return out


def encode(spec: dict) -> str:
    out = json.dumps(spec, separators=(",", ":"), default=str)
    return out.replace(":Infinity", ':"infinite"') if "Infinity" in out else out   # repeat=float("inf")


_TRANSFORM_UNITS = {"x": "px", "y": "px"}


def initial_style(initial, style=None):
    """The ``initial`` pose as CSS (server-rendered first paint), merged into ``style``."""
    pose = {}
    if isinstance(initial, str):
        pose = _preset_initial(initial)
    elif isinstance(initial, dict):
        pose = initial
    css = {}
    x, y = pose.get("x"), pose.get("y")
    if x is not None or y is not None:
        css["translate"] = f"{_unit(x or 0)} {_unit(y or 0)}"
    if "scale" in pose:
        css["scale"] = str(pose["scale"])
    elif "scale_x" in pose or "scale_y" in pose or "scaleX" in pose or "scaleY" in pose:
        sx = pose.get("scale_x", pose.get("scaleX", 1))
        sy = pose.get("scale_y", pose.get("scaleY", 1))
        css["scale"] = f"{sx} {sy}"
    if "rotate" in pose:
        r = pose["rotate"]
        css["rotate"] = f"{r}deg" if isinstance(r, (int, float)) else str(r)
    if "opacity" in pose:
        css["opacity"] = str(pose["opacity"])
    if "blur" in pose:
        css["filter"] = f"blur({_unit(pose['blur'])})"
    for k, v in pose.items():
        if k not in ("x", "y", "scale", "scale_x", "scale_y", "scaleX", "scaleY", "rotate", "opacity", "blur") \
                and isinstance(v, (str, int, float)) and not isinstance(v, bool):
            css[k.replace("_", "-")] = str(v)
    if style is None or style is True or style == "":
        return css or None
    if isinstance(style, dict):
        merged = dict(style)
        merged.update(css)
        return merged
    from .ssr import style_of
    return str(style).rstrip("; ") + ("; " + style_of(css) if css else "")


def _unit(v):
    return f"{v}px" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


_PRESET_INITIAL = {
    "fade": {"opacity": 0}, "fade-up": {"opacity": 0, "y": 16}, "fade-down": {"opacity": 0, "y": -16},
    "fade-left": {"opacity": 0, "x": 16}, "fade-right": {"opacity": 0, "x": -16},
    "slide-up": {"y": "100%"}, "slide-down": {"y": "-100%"}, "slide-left": {"x": "100%"},
    "slide-right": {"x": "-100%"}, "scale": {"opacity": 0, "scale": 0.95}, "pop": {"opacity": 0, "scale": 0.6},
    "blur-in": {"opacity": 0, "blur": 8}, "flip": {"opacity": 0, "rotate_x": "-90deg"},
}


def _preset_initial(name):
    if name in _PRESETS:
        return _PRESETS[name].get("initial") or {}
    return _PRESET_INITIAL.get(name, {})


def _motion_json(spec):
    """``__pw_motion__``: the attribute value (server side)."""
    return encode({k: v for k, v in spec.items() if v is not None})


def _initial(initial, style, variants=None, animate=None):
    """``__pw_initial__``: the server renders the initial pose (``initial=False``: start at
    ``animate``). A variant label is looked up in ``variants``; with no ``initial``, a preset
    name in ``animate`` starts from the preset's first pose."""
    if initial is None and isinstance(animate, str) and not (isinstance(variants, dict) and animate in variants):
        initial = animate
    if initial is False or initial is None:
        return style
    if isinstance(initial, str) and isinstance(variants, dict) and isinstance(variants.get(initial), dict):
        initial = variants[initial]
    elif isinstance(initial, str) and initial not in _PRESETS and initial not in _PRESET_INITIAL:
        return style
    return initial_style(initial, style)


# ------------------------------------------------------------------ browser code API (server stand-ins)
# These run in the browser (runtime/browser/motion-api.js). On the server a page renders its
# first frame: values start at their beginning, and calls that animate do nothing.

def animate(target, keyframes, options=None):
    """Browser code: animate an element (or a CSS selector) to ``keyframes``."""
    return None


def timeline(steps):
    """Browser code: ``[[target, keyframes, options], ...]`` one after another."""
    return None


def stagger(each=0.05, from_="first", start=0):
    """A delay for each of several elements: ``animate(".row", {...}, {"delay": stagger(0.05)})``."""
    return lambda i, count: start + i * each


def scroll_progress(target=None):
    """How far the page (or ``target``) has scrolled, 0 to 1. Call the result to read it."""
    return lambda: 0.0


def use_transform(source, input, output, options=None):
    """``source`` mapped from ``input`` range to ``output`` range. Call the result to read it."""
    return lambda: output[0]


#: Names the server-side renderer provides to markup expressions.
SSR_HELPERS = {"__pw_motion__": _motion_json, "__pw_initial__": _initial}
