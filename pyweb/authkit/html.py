"""Markup for the auth kit's pages (sign-in, account, organizations, admin), built on the UI kit.

Pages link the app's theme (``/__pyweb/theme.css``, so ``App(theme=...)`` restyles them), the
kit's stylesheet (``/__pyweb/ui.css``) and ``/__pyweb/auth/auth.css``, which only lays the page
out. Buttons, cards, notices, fields and tables use the kit's classes (``pw-button`` with
``data-variant``, ``pw-card`` with parts, ``pw-alert`` with ``data-tone``, ``pw-field``,
``pw-table``), so the app's own CSS can restyle them like any kit component.
"""

from __future__ import annotations

import html as _html

esc = _html.escape


def attrs(**kw):
    out = []
    for key, value in kw.items():
        if value is None or value is False:
            continue
        name = key.rstrip("_").replace("_", "-")
        if value is True:
            out.append(f" {name}")
        else:
            out.append(f' {name}="{esc(str(value))}"')
    return "".join(out)


def field(name, label, *, type="text", value="", error="", required=True, autocomplete=None, hint="",
          form="f", **extra):
    ident = f"pw-{form}-{name}"
    message = f"{label} {error}" if error and error[:1].islower() else error
    control = (f"<input{attrs(id=ident, class_='pw-control', name=name, type=type, value=value if type != 'password' else None, required=required, autocomplete=autocomplete, aria_invalid='true' if error else None, aria_describedby=ident + '-error', **extra)}>")
    hint_html = f'<p class="pw-help">{esc(hint)}</p>' if hint else ""
    return (f'<div class="pw-field" data-pw-field="{esc(name)}"><label for="{ident}">{esc(label)}</label>'
            f'{control}{hint_html}<p class="pw-error" id="{ident}-error"{"" if error else " hidden"}>'
            f"{esc(message)}</p></div>")


def form(action, body, *, csrf, method="post", cls="pw-form", extra=""):
    return (f'<form method="{method}" action="{esc(action)}" class="{cls}"{extra}>'
            f'<input type="hidden" name="__pw_csrf" value="{esc(csrf)}">{body}</form>')


#: The auth kit's button kinds, as kit variants.
VARIANTS = {"primary": "default", "secondary": "outline", "danger": "destructive", "ghost": "ghost", "link": "link"}


def button(text, *, name=None, value=None, kind="primary", type="submit", **extra):
    """A kit button. ``kind`` is primary, secondary, danger, ghost or link; add ``" pw-inline"``
    for a button that isn't full width."""
    words = kind.split()
    variant = VARIANTS.get(words[0] if words else "primary", "default")
    cls = "pw-button" + ("" if "pw-inline" in words else " pw-block")
    return (f'<button{attrs(type=type, name=name, value=value, class_=cls, data_variant=variant, **extra)}>'
            f"{esc(text)}</button>")


def link_button(text, href, *, kind="secondary"):
    return (f'<a class="pw-button pw-block" data-variant="{VARIANTS.get(kind, "outline")}" href="{esc(href)}">'
            f"{esc(text)}</a>")


def badge(text, tone=None, *, variant="secondary"):
    return f'<span{attrs(class_="pw-badge", data_variant=variant, data_tone=tone)}>{esc(text)}</span>'


def table(rows_html, *, cls=""):
    return f'<div class="pw-table-wrap"><table class="pw-table{(" " + cls) if cls else ""}">{rows_html}</table></div>'


TONES = {"error": "danger", "ok": "success", "success": "success", "info": "info", "warning": "warning"}


def notice(text, kind="info", *, raw=False):
    """A kit alert; ``raw=True`` takes markup instead of text."""
    if not text:
        return ""
    tone = TONES.get(kind, "info")
    role = "alert" if tone in ("danger", "warning") else "status"
    inner = text if raw else f'<p data-part="description">{esc(text)}</p>'
    return f'<div class="pw-alert pw-notice" data-tone="{tone}" role="{role}">{inner}</div>'


def card(title, body, *, wide=False, lead=""):
    lead_html = f'<p data-part="description">{esc(lead)}</p>' if lead else ""
    return (f'<main class="pw-auth{" pw-wide" if wide else ""}"><section class="pw-card">'
            f'<div data-part="header"><h1 data-part="title">{esc(title)}</h1>{lead_html}</div>'
            f'<div data-part="content">{body}</div></section></main>')


def kit_links(app=None):
    """The theme and kit stylesheets for these pages (the theme follows ``App(theme=...)``)."""
    from pyweb.ui import serve
    return serve.head_tags(app, kit=False) + f'<link rel="stylesheet" href="/__pyweb/ui.css?v={serve.version()}">'


def shell(title, body, *, site="", css=(), scripts=(), lang="en", app=None):
    links = kit_links(app) + "".join(f'<link rel="stylesheet" href="{esc(u)}">'
                                     for u in ["/__pyweb/auth/auth.css", *css])
    js = "".join(f'<script type="module" src="{esc(u)}"></script>' for u in scripts)
    full = f"{title} · {site}" if site and site != title else title
    return (f'<!doctype html>\n<html lang="{esc(lang)}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex"><meta name="referrer" content="no-referrer">'
            f"<title>{esc(full)}</title>{links}</head><body>{body}{js}</body></html>\n")


#: Layout only: colours, type, buttons, fields and tables come from the theme and ui.css.
CSS = """
@layer pyweb.auth{
.pw-auth{min-height:100vh;display:grid;place-items:center;padding:var(--pw-space-10,24px) 16px;background:var(--pw-bg)}
.pw-auth>.pw-card{width:100%;max-width:420px}
.pw-auth.pw-wide>.pw-card{max-width:820px}
.pw-auth h1[data-part=title]{font-size:var(--pw-text-xl,1.4rem)}
.pw-auth h2{font-size:var(--pw-text-md,1.05rem);margin:var(--pw-space-10,28px) 0 var(--pw-space-5,10px)}
.pw-auth h3{font-size:var(--pw-text-sm,.95rem);margin:var(--pw-space-8,20px) 0 var(--pw-space-4,8px)}
.pw-auth a:not(.pw-button){color:var(--pw-accent-fg)}
.pw-block{width:100%;margin:var(--pw-space-2,4px) 0}
.pw-help,.pw-muted{color:var(--pw-fg-muted);font-size:var(--pw-text-xs,.88rem);margin:0 0 var(--pw-space-6,14px)}
.pw-field .pw-help{margin:var(--pw-space-2,4px) 0 0}
.pw-notice{margin:0 0 var(--pw-space-6,14px)}
.pw-or{display:flex;align-items:center;gap:10px;color:var(--pw-fg-muted);font-size:var(--pw-text-xs,.85rem);margin:16px 0}
.pw-or:before,.pw-or:after{content:"";flex:1;border-top:1px solid var(--pw-border)}
.pw-links{display:flex;justify-content:space-between;gap:12px;margin-top:16px}
.pw-table-wrap{margin:0 0 var(--pw-space-6,14px)}
.pw-table td{vertical-align:middle}.pw-table form{margin:0}
.pw-row{display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.pw-row form{margin:0}
.pw-code{font-family:var(--pw-font-mono,ui-monospace,monospace);word-break:break-all;background:var(--pw-gray-2);
border:1px solid var(--pw-border);padding:8px;border-radius:var(--pw-radius)}
.pw-check{display:flex;gap:8px;align-items:center}
.pw-nav{display:flex;gap:var(--pw-space-2,4px);flex-wrap:wrap;margin:0 0 var(--pw-space-8,18px);padding:var(--pw-space-2,4px);
background:var(--pw-gray-3);border-radius:var(--pw-radius)}
.pw-nav a{padding:6px 10px;border-radius:var(--pw-radius-sm,6px);color:var(--pw-fg-muted)!important;text-decoration:none}
.pw-nav a:hover{color:var(--pw-fg)!important}
.pw-nav a[aria-current=page]{background:var(--pw-surface);color:var(--pw-fg)!important;box-shadow:var(--pw-shadow-1)}
.pw-search{display:flex;gap:8px;margin:0 0 var(--pw-space-6,14px)}.pw-search .pw-control{flex:1}
}
"""
