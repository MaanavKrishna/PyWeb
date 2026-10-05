"""Markup for the auth kit's pages: small, accessible, unstyled-but-tidy.

Pages use the app's stylesheets plus ``/__pyweb/auth/auth.css``, which only
uses CSS variables (``--pw-accent``, ``--pw-radius`` ...) so a site can
restyle it. Field markup matches ``<Form>``'s (``pw-field``, ``pw-error``).
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
    control = (f"<input{attrs(id=ident, name=name, type=type, value=value if type != 'password' else None, required=required, autocomplete=autocomplete, aria_invalid='true' if error else None, aria_describedby=ident + '-error', **extra)}>")
    hint_html = f'<p class="pw-help">{esc(hint)}</p>' if hint else ""
    return (f'<div class="pw-field" data-pw-field="{esc(name)}"><label for="{ident}">{esc(label)}</label>'
            f'{control}{hint_html}<p class="pw-error" id="{ident}-error"{"" if error else " hidden"}>'
            f"{esc(message)}</p></div>")


def form(action, body, *, csrf, method="post", cls="pw-form", extra=""):
    return (f'<form method="{method}" action="{esc(action)}" class="{cls}"{extra}>'
            f'<input type="hidden" name="__pw_csrf" value="{esc(csrf)}">{body}</form>')


def button(text, *, name=None, value=None, kind="primary", type="submit", **extra):
    return f'<button{attrs(type=type, name=name, value=value, class_="pw-button pw-" + kind, **extra)}>{esc(text)}</button>'


def notice(text, kind="info"):
    if not text:
        return ""
    role = "alert" if kind == "error" else "status"
    return f'<p class="pw-notice pw-{kind}" role="{role}">{esc(text)}</p>'


def card(title, body, *, wide=False, lead=""):
    lead_html = f'<p class="pw-lead">{esc(lead)}</p>' if lead else ""
    return (f'<main class="pw-auth{" pw-wide" if wide else ""}"><section class="pw-card">'
            f"<h1>{esc(title)}</h1>{lead_html}{body}</section></main>")


def shell(title, body, *, site="", css=(), scripts=(), lang="en"):
    links = "".join(f'<link rel="stylesheet" href="{esc(u)}">' for u in ["/__pyweb/auth/auth.css", *css])
    js = "".join(f'<script type="module" src="{esc(u)}"></script>' for u in scripts)
    full = f"{title} · {site}" if site and site != title else title
    return (f'<!doctype html>\n<html lang="{esc(lang)}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex"><meta name="referrer" content="no-referrer">'
            f"<title>{esc(full)}</title>{links}</head><body>{body}{js}</body></html>\n")


CSS = """
:root{--pw-accent:#4f46e5;--pw-accent-ink:#fff;--pw-ink:#111827;--pw-muted:#6b7280;--pw-line:#e5e7eb;
--pw-bg:#f9fafb;--pw-card:#fff;--pw-error:#b91c1c;--pw-ok:#15803d;--pw-radius:10px;
--pw-font:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root{--pw-ink:#f3f4f6;--pw-muted:#9ca3af;--pw-line:#374151;--pw-bg:#0b0f17;
--pw-card:#111827;--pw-error:#f87171;--pw-ok:#4ade80;--pw-accent:#818cf8;--pw-accent-ink:#0b0f17}}
.pw-auth{min-height:100vh;display:grid;place-items:center;padding:24px 16px;background:var(--pw-bg);
font-family:var(--pw-font);color:var(--pw-ink)}
.pw-card{width:100%;max-width:400px;background:var(--pw-card);border:1px solid var(--pw-line);
border-radius:calc(var(--pw-radius)*1.4);padding:28px}
.pw-wide .pw-card{max-width:760px}
.pw-card h1{font-size:1.5rem;margin:0 0 6px}.pw-card h2{font-size:1.1rem;margin:28px 0 10px}
.pw-lead,.pw-help,.pw-muted{color:var(--pw-muted);font-size:.92rem;margin:0 0 18px}
.pw-field{display:grid;gap:6px;margin:0 0 14px}.pw-field label{font-weight:600;font-size:.92rem}
.pw-field input,.pw-field select,.pw-field textarea{font:inherit;padding:10px 12px;border:1px solid var(--pw-line);
border-radius:var(--pw-radius);background:transparent;color:inherit;width:100%;box-sizing:border-box}
.pw-field input:focus-visible,.pw-button:focus-visible{outline:2px solid var(--pw-accent);outline-offset:2px}
.pw-field input[aria-invalid=true]{border-color:var(--pw-error)}
.pw-error{color:var(--pw-error);font-size:.88rem;margin:0}.pw-error[hidden]{display:none}
.pw-button{font:inherit;font-weight:600;padding:10px 16px;border-radius:var(--pw-radius);cursor:pointer;
border:1px solid var(--pw-line);background:transparent;color:inherit;width:100%;margin:4px 0}
.pw-primary{background:var(--pw-accent);color:var(--pw-accent-ink);border-color:var(--pw-accent)}
.pw-danger{color:var(--pw-error)}.pw-inline{width:auto}
.pw-notice{padding:10px 12px;border-radius:var(--pw-radius);border:1px solid var(--pw-line);font-size:.92rem}
.pw-notice.pw-error{border-color:var(--pw-error)}.pw-notice.pw-ok{border-color:var(--pw-ok)}
.pw-or{display:flex;align-items:center;gap:10px;color:var(--pw-muted);font-size:.85rem;margin:16px 0}
.pw-or:before,.pw-or:after{content:"";flex:1;border-top:1px solid var(--pw-line)}
.pw-links{display:flex;justify-content:space-between;gap:12px;margin-top:16px;font-size:.92rem}
.pw-links a,.pw-card a{color:var(--pw-accent)}
.pw-table{width:100%;border-collapse:collapse;font-size:.92rem}
.pw-table th,.pw-table td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--pw-line);vertical-align:top}
.pw-row{display:flex;gap:8px;align-items:center;justify-content:space-between}
.pw-code{font-family:ui-monospace,monospace;word-break:break-all;background:var(--pw-bg);padding:8px;
border-radius:var(--pw-radius)}
.pw-check{display:flex;gap:8px;align-items:center}
.pw-nav{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 18px;font-size:.92rem}
"""
