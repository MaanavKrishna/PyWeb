"""Themes: one accent colour in, a complete, accessible palette out.

``Theme(accent="#6366f1")`` makes 12-step colour scales (like Radix Colors) for the
accent, a matching gray and the status colours, in light and dark, computed in OKLCH so
every step looks evenly spaced. Each step has a job:

====  ==========================================
1-2   app and subtle backgrounds
3-5   component backgrounds: normal, hover, pressed
6-8   borders: subtle, normal, strong (and focus rings)
9-10  solid fills (buttons): normal and hover
11    readable coloured text
12    high-contrast text
====  ==========================================

Text steps are checked (and adjusted) to reach a 4.5:1 contrast ratio. Everything becomes
CSS variables (``--pw-accent-9``, ``--pw-gray-11``, ``--pw-radius``...) that the UI kit and
your own CSS use, so plain CSS can override any of it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import re

# ------------------------------------------------------------------ colour maths (OKLab/OKLCH, sRGB)


def _to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_rgb(color):
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c):
        raise ValueError(f"not a hex colour: {color!r} (use #rrggbb)")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#" + "".join(f"{round(min(1.0, max(0.0, v)) * 255):02x}" for v in rgb)


def rgb_to_oklch(rgb):
    r, g, b = (_to_linear(v) for v in rgb)
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    C = math.hypot(a, bb)
    H = math.degrees(math.atan2(bb, a)) % 360
    return L, C, H


def _oklch_to_linear(L, C, H):
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_ ** 3, m_ ** 3, s_ ** 3
    return (4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3,
            -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3,
            -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3)


def oklch_to_rgb(L, C, H):
    """sRGB for an OKLCH colour, reducing chroma until it fits the sRGB gamut."""
    lo, hi = 0.0, C
    lin = _oklch_to_linear(L, C, H)
    if all(-1e-6 <= v <= 1 + 1e-6 for v in lin):
        return tuple(_to_srgb(min(1.0, max(0.0, v))) for v in lin)
    for _ in range(24):
        mid = (lo + hi) / 2
        lin = _oklch_to_linear(L, mid, H)
        if all(-1e-6 <= v <= 1 + 1e-6 for v in lin):
            lo = mid
        else:
            hi = mid
    lin = _oklch_to_linear(L, lo, H)
    return tuple(_to_srgb(min(1.0, max(0.0, v))) for v in lin)


def luminance(rgb):
    r, g, b = (_to_linear(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(c1, c2):
    """WCAG contrast ratio between two colours (hex or rgb tuples)."""
    a = luminance(hex_to_rgb(c1) if isinstance(c1, str) else c1)
    b = luminance(hex_to_rgb(c2) if isinstance(c2, str) else c2)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ------------------------------------------------------------------ scales

# Lightness per step, light and dark (tuned to look like Radix Colors' scales).
LIGHT_L = (0.993, 0.982, 0.958, 0.932, 0.903, 0.868, 0.822, 0.756, None, None, 0.52, 0.31)
DARK_L = (0.178, 0.205, 0.255, 0.292, 0.330, 0.375, 0.435, 0.515, None, None, 0.80, 0.94)
# Chroma as a share of the accent's own chroma (backgrounds are faint, solids full).
LIGHT_C = (0.06, 0.12, 0.25, 0.36, 0.46, 0.55, 0.64, 0.78, 1.0, 1.0, 0.95, 0.55)
DARK_C = (0.20, 0.25, 0.42, 0.55, 0.62, 0.68, 0.74, 0.85, 1.0, 1.0, 0.75, 0.30)


def scale(base, *, dark=False, gray=False):
    """The 12 steps (hex) for ``base`` (hex); ``gray=True`` makes a neutral tinted with its hue."""
    L9, C9, H = rgb_to_oklch(hex_to_rgb(base))
    if gray:
        C9 = min(C9, 0.02)
    Ls = list(DARK_L if dark else LIGHT_L)
    Cs = DARK_C if dark else LIGHT_C
    solid = L9 if not gray else (0.56 if not dark else 0.62)
    Ls[8] = solid
    Ls[9] = solid - 0.05 if (not dark and solid > 0.5) or (dark and solid > 0.75) else solid + 0.05
    if not dark:
        Ls[10] = min(Ls[10], Ls[9] - 0.04)       # coloured text stays darker than the hover fill
    steps = [rgb_to_hex(oklch_to_rgb(L, C9 * Cs[i], H)) for i, L in enumerate(Ls)]
    # Text on the solid fill (buttons) must read at 4.5:1 too: darken the fill a little if needed.
    L = Ls[8]
    while max(contrast(steps[8], "#ffffff"), contrast(steps[8], "#111111")) < 4.5 and L > 0.05:
        L -= 0.01
        steps[8] = rgb_to_hex(oklch_to_rgb(L, C9, H))
        steps[9] = rgb_to_hex(oklch_to_rgb(L - 0.05, C9, H))
    # Readable text: step 11 on step 2 and step 12 on step 1 reach 4.5:1 (darken or lighten if not).
    for idx, bg in ((10, steps[1]), (11, steps[0])):
        L = Ls[idx]
        while contrast(steps[idx], bg) < 4.5 and 0.02 < L < 0.98:
            L = L + 0.015 if dark else L - 0.015
            steps[idx] = rgb_to_hex(oklch_to_rgb(L, C9 * Cs[idx], H))
    return steps


def on_solid(color):
    """White or near-black, whichever reads better on ``color`` (text on step 9)."""
    return "#ffffff" if contrast(color, "#ffffff") >= contrast(color, "#111111") else "#111111"


# ------------------------------------------------------------------ the theme

PRESETS = {
    "indigo": {"accent": "#6366f1"},
    "zinc": {"accent": "#52525b", "gray": "#71717a"},
    "slate": {"accent": "#475569", "gray": "#64748b"},
    "rose": {"accent": "#e11d48"},
    "ocean": {"accent": "#0284c7", "gray": "#64748b"},
    "violet": {"accent": "#7c3aed"},
    "emerald": {"accent": "#059669"},
    "amber": {"accent": "#d97706", "gray": "#78716c"},
}
STATUS = {"danger": "#e5484d", "success": "#30a46c", "warning": "#f5a524", "info": "#0090ff"}
DENSITY = {"compact": 0.85, "comfortable": 1.0, "spacious": 1.15}
FONTS = {
    "system": 'system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif',
    "inter": '"Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif',
    "serif": 'ui-serif,Georgia,Cambria,"Times New Roman",serif',
    "mono": 'ui-monospace,SFMono-Regular,Menlo,Consolas,monospace',
}


@dataclasses.dataclass(frozen=True)
class Theme:
    """How the UI kit (and the auth pages) look. Every value is optional.

    ``accent``: the brand colour (#rrggbb). ``gray``: the neutral's hue (default: a gray
    tinted toward the accent). ``radius``: corner radius in px. ``font``: a CSS
    font-family, or "system", "inter", "serif", "mono". ``density``: "compact",
    "comfortable" or "spacious". ``dark``: "auto" (follow the OS), "light" or "dark".
    """

    accent: str = "#6366f1"
    gray: str | None = None
    radius: float = 10
    font: str = "system"
    density: str = "comfortable"
    dark: str = "auto"

    def __post_init__(self):
        hex_to_rgb(self.accent)
        if self.gray is not None:
            hex_to_rgb(self.gray)
        if not 0 <= float(self.radius) <= 40:
            raise ValueError("Theme radius is in px, from 0 to 40")
        if self.density not in DENSITY:
            raise ValueError(f"Theme density is one of {', '.join(DENSITY)}")
        if self.dark not in ("auto", "light", "dark"):
            raise ValueError('Theme dark is "auto", "light" or "dark"')
        if re.search(r"[;{}<>]", self.font):
            raise ValueError("Theme font is a font-family list, like '\"Inter\", sans-serif'")

    @classmethod
    def preset(cls, name, **overrides):
        """A built-in look: indigo, zinc, slate, rose, ocean, violet, emerald or amber."""
        if name not in PRESETS:
            raise ValueError(f"no theme preset {name!r} (presets: {', '.join(PRESETS)})")
        return cls(**{**PRESETS[name], **overrides})

    # -- output ------------------------------------------------------------------------
    def variables(self, *, dark=False) -> dict:
        """CSS custom properties (name -> value) for light or dark."""
        out = {}
        gray_base = self.gray or self.accent
        scales = {"accent": scale(self.accent, dark=dark), "gray": scale(gray_base, dark=dark, gray=True)}
        for name, base in STATUS.items():
            scales[name] = scale(base, dark=dark)
        for name, steps in scales.items():
            for i, v in enumerate(steps, 1):
                out[f"--pw-{name}-{i}"] = v
            out[f"--pw-{name}-contrast"] = on_solid(steps[8])
        g, a = scales["gray"], scales["accent"]
        out.update({
            "--pw-bg": g[0], "--pw-bg-subtle": g[1], "--pw-surface": "#ffffff" if not dark else g[1],
            "--pw-fg": g[11], "--pw-fg-muted": g[10], "--pw-border": g[5], "--pw-border-strong": g[7],
            "--pw-ring": a[7], "--pw-accent": a[8], "--pw-accent-hover": a[9], "--pw-accent-fg": a[10],
            "--pw-accent-contrast": on_solid(a[8]),
            "--pw-overlay": "rgba(0,0,0,.45)" if not dark else "rgba(0,0,0,.65)",
            "--pw-shadow-1": "0 1px 2px rgba(0,0,0,.06),0 1px 3px rgba(0,0,0,.08)" if not dark
            else "0 1px 2px rgba(0,0,0,.4)",
            "--pw-shadow-2": "0 4px 12px rgba(0,0,0,.08),0 2px 4px rgba(0,0,0,.06)" if not dark
            else "0 4px 14px rgba(0,0,0,.5)",
            "--pw-shadow-3": "0 12px 32px rgba(0,0,0,.14),0 4px 10px rgba(0,0,0,.08)" if not dark
            else "0 14px 40px rgba(0,0,0,.6)",
            # The names the auth kit's pages already use.
            "--pw-ink": g[11], "--pw-muted": g[10], "--pw-line": g[5], "--pw-card": "#ffffff" if not dark else g[1],
            "--pw-accent-ink": on_solid(a[8]), "--pw-error": scales["danger"][10], "--pw-ok": scales["success"][10],
        })
        return out

    def shared(self) -> dict:
        r = float(self.radius)
        d = DENSITY[self.density]
        font = FONTS.get(self.font, self.font)
        out = {"--pw-radius": f"{r:g}px", "--pw-radius-sm": f"{max(r * 0.6, 0):g}px",
               "--pw-radius-lg": f"{r * 1.4:g}px", "--pw-radius-full": "9999px",
               "--pw-font": font, "--pw-font-mono": FONTS["mono"], "--pw-density": f"{d:g}"}
        for i, px in enumerate((2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64), 1):
            out[f"--pw-space-{i}"] = f"{px * d:g}px"
        for name, (size, line) in {"xs": (12, 16), "sm": (14, 20), "md": (16, 24), "lg": (18, 28),
                                   "xl": (20, 28), "2xl": (24, 32), "3xl": (30, 36), "4xl": (36, 40)}.items():
            out[f"--pw-text-{name}"] = f"{size}px"
            out[f"--pw-leading-{name}"] = f"{line}px"
        return out

    def css(self) -> str:
        """The theme as a stylesheet (``/__pyweb/theme.css``)."""
        def block(sel, values):
            return sel + "{" + ";".join(f"{k}:{v}" for k, v in values.items()) + "}"
        light, dark = self.variables(dark=False), self.variables(dark=True)
        shared = self.shared()
        parts = [block(":root", {**shared, **light, "color-scheme": "light dark" if self.dark == "auto"
                                 else self.dark})]
        if self.dark == "dark":
            parts = [block(":root", {**shared, **dark, "color-scheme": "dark"})]
        elif self.dark == "auto":
            parts.append("@media (prefers-color-scheme:dark){" + block(':root:not([data-theme="light"])', dark) + "}")
        parts.append(block(':root[data-theme="dark"]', {**dark, "color-scheme": "dark"}))
        parts.append(block(':root[data-theme="light"]', {**light, "color-scheme": "light"}))
        return "\n".join(parts) + "\n"

    def version(self) -> str:
        return hashlib.sha256(repr(self).encode()).hexdigest()[:10]


DEFAULT = Theme()


def config(app) -> Theme:
    value = getattr(app, "theme", None)
    if value is None:
        return DEFAULT
    if isinstance(value, Theme):
        return value
    if isinstance(value, str):
        return Theme.preset(value)
    if isinstance(value, dict):
        return Theme(**value)
    raise TypeError("App(theme=...) takes pyweb.ui.Theme(...) or a preset name")
