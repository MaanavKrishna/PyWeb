"""The dev toolbar: what each request did, on the page, while you develop.

``pyweb dev`` adds a small badge to every page (time and query count, red
when something looks wrong). Opening it shows, for that page and for every
request since (server function calls, form posts):

- the SQL that ran, with timings, and a warning when the same query ran
  10+ times (an N+1 loop);
- spans (``with pyweb.telemetry.span("charge card"):``);
- jobs queued (with a link to retry failed ones), emails "sent";
- the error, if there was one.

It's served by the dev server only: production builds and ``pyweb serve``
never include it, and its endpoints don't exist there.
"""

from __future__ import annotations

import json

PREFIX = "/__pyweb/dev/"


def _json(data, status=200):
    return status, [("Content-Type", "application/json"), ("Cache-Control", "no-store")], \
        json.dumps(data, default=str).encode()


def _same_origin(headers):
    h = {k.lower(): v for k, v in (headers or {}).items()}
    site = h.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin, host = h.get("origin"), h.get("host", "")
    return origin is None or origin.split("://", 1)[-1] == host


def _theme_fields(app, query):
    """The app's theme with the toolbar's changes (from the query string) on top."""
    from urllib.parse import parse_qsl

    from pyweb.ui import theme as T
    from pyweb.ui.source_edit import FIELDS
    th = T.config(app)
    fields = {k: getattr(th, k) for k in FIELDS}
    for k, v in parse_qsl(query):
        if k == "preset" and v in T.PRESETS:
            fields.update(T.PRESETS[v])
        elif k in FIELDS:
            fields[k] = v or None
    return fields


def theme_preview(app, query):
    """``/__pyweb/dev/theme.css?accent=…``: the theme stylesheet with the toolbar's changes."""
    from pyweb.ui import serve as ui_serve
    from pyweb.ui.extending import css as extend_css
    from pyweb.ui.source_edit import make_theme
    import dataclasses

    from pyweb.ui import theme as T
    th = dataclasses.replace(make_theme(_theme_fields(app, query)), extend=T.config(app).extend)
    body = th.css() + ui_serve._motion_css(app) + extend_css(th)
    return 200, [("Content-Type", "text/css; charset=utf-8"), ("Cache-Control", "no-store")], body.encode()


def handle(method, path, headers, body=b"", app=None, app_path=None):
    """Answer a ``/__pyweb/dev/...`` request, or None when it isn't one of ours."""
    from . import RECENT
    bare, _, query = path.partition("?")
    what = bare[len(PREFIX):]
    if what == "theme.css":
        try:
            return theme_preview(app, query)
        except (ValueError, TypeError) as exc:
            return 400, [("Content-Type", "text/plain")], str(exc).encode()
    if what == "theme" and method == "GET":
        from pyweb.ui import theme as T
        from pyweb.ui.source_edit import theme_code
        fields = _theme_fields(app, "")
        return _json({"theme": fields, "code": theme_code(fields), "presets": T.PRESETS,
                      "densities": list(T.DENSITY), "fonts": ["system", "inter", "serif", "mono"],
                      "can_save": bool(app_path)})
    if what == "theme" and method == "POST":
        if not _same_origin(headers):
            return _json({"error": "POST from this page only"}, 403)
        if not app_path:
            return _json({"error": "this app has no file to save to"}, 409)
        try:
            fields = json.loads(body or b"{}")
            from pyweb.ui.source_edit import FIELDS, save_theme
            code = save_theme(app_path, {k: fields.get(k) for k in FIELDS})
        except (ValueError, TypeError, SyntaxError) as exc:
            return _json({"error": str(exc)}, 400)
        return _json({"ok": True, "code": code})
    if what == "requests":
        since = dict(p.partition("=")[::2] for p in query.split("&") if p).get("since", "")
        items = list(RECENT)
        if since:
            ids = [r["id"] for r in items]
            items = items[ids.index(since) + 1:] if since in ids else items
        return _json({"requests": items[-60:]})
    if what == "jobs":
        from pyweb.jobs import core
        store = core.backend()
        return _json({"store": type(store).__name__, "jobs": store.list(limit=50)})
    if what == "mail":
        from pyweb.mail import OUTBOX
        return _json({"mail": [{"to": m.to, "subject": m.subject, "text": m.text, "html": m.html}
                               for m in reversed(OUTBOX)]})
    if what.startswith("jobs/") and what.endswith("/retry"):
        if method != "POST" or not _same_origin(headers):
            return _json({"error": "POST from this page only"}, 403)
        from pyweb.jobs import core
        return _json({"ok": bool(core.backend().retry(what[len("jobs/"):-len("/retry")]))})
    return None


def inject(html, summary):
    """``html`` with the toolbar (and this page's request summary) before ``</body>``."""
    data = json.dumps(summary or {}, default=str).replace("</", "<\\/").replace("<!--", "<\\!--")
    tag = f'<script>window.__PYWEB_REQ__={data};</script>{TOOLBAR_JS}'
    return html.replace(b"</body>", tag.encode() + b"</body>", 1)


TOOLBAR_JS = r"""<script>(() => {
if (window.__pywebDevtools) return; window.__pywebDevtools = true;
const host = document.createElement("pyweb-devtools");
const root = host.attachShadow({mode: "open"});
root.innerHTML = `<style>
:host{all:initial}
*{box-sizing:border-box;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.pill{position:fixed;left:12px;bottom:12px;z-index:2147483646;display:flex;gap:6px;align-items:center;
 background:#111827;color:#e5e7eb;border-radius:999px;padding:5px 11px;cursor:pointer;
 box-shadow:0 4px 14px rgba(0,0,0,.25);border:1px solid #374151;user-select:none}
.pill b{color:#a5b4fc;font-weight:600}.bad{color:#fca5a5!important}.warn{color:#fcd34d!important}
.panel{position:fixed;left:12px;right:12px;bottom:52px;max-height:60vh;z-index:2147483647;display:none;
 flex-direction:column;background:#0b1020;color:#e5e7eb;border:1px solid #374151;border-radius:10px;
 box-shadow:0 10px 30px rgba(0,0,0,.35);overflow:hidden}
.panel.open{display:flex}
nav{display:flex;gap:2px;border-bottom:1px solid #1f2937;padding:6px 6px 0}
nav button{background:none;border:0;color:#9ca3af;padding:6px 10px;cursor:pointer;border-radius:6px 6px 0 0}
nav button.on{background:#1f2937;color:#fff}
nav .x{margin-left:auto}
main{overflow:auto;padding:8px 10px}
table{border-collapse:collapse;width:100%}td{padding:3px 6px;vertical-align:top;border-bottom:1px solid #1f2937}
td.n{text-align:right;color:#9ca3af;white-space:nowrap}
.row{cursor:pointer}.row:hover{background:#111827}.sel{background:#1e293b}
code{white-space:pre-wrap;word-break:break-word;color:#e5e7eb}
.muted{color:#6b7280}.tag{display:inline-block;padding:0 6px;border-radius:4px;background:#1f2937;margin-right:4px}
h4{margin:10px 0 4px;color:#a5b4fc;font-weight:600}
a,button.link{color:#93c5fd;background:none;border:0;cursor:pointer;padding:0}
pre{margin:4px 0;white-space:pre-wrap;color:#fca5a5}
.form{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px 16px;margin:6px 0 10px}
.form label{display:flex;flex-direction:column;gap:3px;color:#9ca3af}
.form input,.form select{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:3px 6px}
.form input[type=color]{height:28px;padding:1px}
.btn{background:#1f2937;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:4px 10px;cursor:pointer;margin-right:6px}
.btn.on{background:#4338ca;border-color:#6366f1}
.code{display:block;background:#111827;border-radius:6px;padding:6px 8px;margin:6px 0}
.ok{color:#86efac}
</style>
<div class="pill" part="pill"></div>
<div class="panel"><nav>
 <button data-tab="req" class="on">This page</button><button data-tab="all">Requests</button>
 <button data-tab="jobs">Jobs</button><button data-tab="mail">Emails</button>
 <button data-tab="theme">Theme</button><button data-tab="motion">Motion</button>
 <button class="x" title="close">✕</button></nav><main></main></div>`;
const pill = root.querySelector(".pill"), panel = root.querySelector(".panel"), main = root.querySelector("main");
const page = window.__PYWEB_REQ__ || {};
let tab = "req", seen = [], selected = null, timer = null;
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const ms = v => (v < 10 ? v.toFixed(1) : Math.round(v)) + " ms";
function badge() {
  const all = [page, ...seen], warn = all.some(r => (r.warnings || []).length), err = all.some(r => r.error);
  pill.innerHTML = `<span>⚡</span><b>${ms(page.ms || 0)}</b><span>${page.queries || 0} SQL</span>` +
    (seen.length ? `<span class="muted">+${seen.length}</span>` : "") +
    (err ? `<span class="bad">● error</span>` : warn ? `<span class="warn">● N+1?</span>` : "");
}
function detail(r) {
  if (!r || !r.id) return `<p class="muted">No request recorded.</p>`;
  let h = `<div><span class="tag">${esc(r.method)}</span>${esc(r.path)} <span class="muted">→ ${esc(r.status)} in ${ms(r.ms)}` +
    ` · ${r.queries} queries (${ms(r.query_ms || 0)}) · ${esc(r.route || "")}${r.user ? " · user " + esc(r.user) : ""}</span></div>`;
  if (r.error) h += `<h4>Error</h4><pre>${esc(r.error)}</pre>`;
  for (const w of r.warnings || []) h += `<p class="warn">⚠ ${esc(w)}</p>`;
  if ((r.sql || []).length) {
    // The same statement run many times is one row: how often, and the time it all took.
    const groups = new Map();
    r.sql.forEach(q => { const g = groups.get(q.sql); if (g) { g.n++; g.ms += q.ms; } else groups.set(q.sql, {...q, n: 1}); });
    h += `<h4>SQL</h4><table>` + [...groups.values()].map(q => `<tr><td class="n">${ms(q.ms)}</td><td><code>${esc(q.sql)}</code>` +
      (q.n > 1 ? ` <span class="${q.n >= 10 ? "warn" : "muted"}">×${q.n}</span>`
       : q.params && q.params.length ? ` <span class="muted">${esc(JSON.stringify(q.params))}</span>` : "") + `</td></tr>`).join("") + `</table>`;
  }
  if ((r.spans || []).length) h += `<h4>Spans</h4><table>` + r.spans.map(s => `<tr><td class="n">${ms(s.ms)}</td><td>${esc(s.name)}</td></tr>`).join("") + `</table>`;
  const ev = r.events || [];
  if (ev.length) h += `<h4>Side effects</h4><table>` + ev.map(e => `<tr><td class="n">${esc(e.kind)}</td><td>` +
    (e.kind === "job" ? `queued <b>${esc(e.name)}</b> <span class="muted">${esc(e.id)}${e.delay ? " in " + e.delay + "s" : ""}</span>`
     : e.kind === "mail" ? `email to ${esc((e.to || []).join(", "))}: <b>${esc(e.subject)}</b>` : esc(JSON.stringify(e))) + `</td></tr>`).join("") + `</table>`;
  return h + `<p class="muted">trace ${esc(r.trace_id)}</p>`;
}
async function get(url) { const r = await fetch(url, {cache: "no-store"}); return r.json(); }
async function poll() {
  try {
    const last = seen.length ? seen[seen.length - 1].id : page.id || "";
    const d = await get("/__pyweb/dev/requests?since=" + encodeURIComponent(last));
    const fresh = (d.requests || []).filter(r => r.id !== page.id && !seen.some(s => s.id === r.id));
    if (fresh.length) { seen = seen.concat(fresh).slice(-50); badge(); if (tab === "all") render(); }
  } catch (e) {}
}
async function render() {
  root.querySelectorAll("nav [data-tab]").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  if (tab === "req") { main.innerHTML = detail(page); return; }
  if (tab === "all") {
    const rows = [page, ...seen].filter(r => r.id).reverse();
    main.innerHTML = `<table>` + rows.map(r => `<tr class="row ${selected === r.id ? "sel" : ""}" data-id="${esc(r.id)}">` +
      `<td class="n">${ms(r.ms)}</td><td><span class="tag">${esc(r.method)}</span>${esc(r.path)}</td>` +
      `<td class="n ${r.status >= 500 || r.error ? "bad" : ""}">${esc(r.status)}</td><td class="n">${r.queries} SQL</td>` +
      `<td>${(r.warnings || []).length ? '<span class="warn">N+1?</span>' : ""}</td></tr>` +
      (selected === r.id ? `<tr><td colspan="5">${detail(r)}</td></tr>` : "")).join("") + `</table>`;
    return;
  }
  if (tab === "jobs") {
    const d = await get("/__pyweb/dev/jobs");
    main.innerHTML = `<p class="muted">store: ${esc(d.store)}</p><table>` + (d.jobs || []).map(j =>
      `<tr><td class="n">${esc(j.state)}</td><td><b>${esc(j.name)}</b> <span class="muted">${esc(j.id)}` +
      ` · ${j.attempts}/${j.max_attempts}</span>${j.last_error ? `<pre>${esc(j.last_error.split("\n")[0])}</pre>` : ""}</td>` +
      `<td>${["dead", "failed"].includes(j.state) ? `<button class="link" data-retry="${esc(j.id)}">retry</button>` : ""}</td></tr>`).join("") +
      `</table>` + ((d.jobs || []).length ? "" : `<p class="muted">No jobs yet.</p>`);
    return;
  }
  if (tab === "theme") { await themeTab(); return; }
  if (tab === "motion") { motionTab(); return; }
  if (tab === "mail") {
    const d = await get("/__pyweb/dev/mail");
    main.innerHTML = (d.mail || []).map(m => `<h4>${esc(m.subject)}</h4><div class="muted">to ${esc(m.to.join(", "))}</div>` +
      `<code>${esc(m.text)}</code>`).join("") || `<p class="muted">No emails yet. In development they're captured here instead of sent.</p>`;
  }
}
// ---------------------------------------------------------------- Theme: live preview, copy, save
let theme = null, themeInfo = null, themeMsg = "";
const DEFAULTS = {accent: "#6366f1", gray: null, radius: 10, font: "system", density: "comfortable", dark: "auto"};
function themeCode() {
  const parts = Object.keys(DEFAULTS).filter(k => theme[k] != null && theme[k] !== "" && String(theme[k]) !== String(DEFAULTS[k]))
    .map(k => `${k}=${typeof DEFAULTS[k] === "number" ? Number(theme[k]) : JSON.stringify(theme[k])}`);
  return `Theme(${parts.join(", ")})`;
}
function preview() {
  const q = new URLSearchParams();
  for (const k of Object.keys(DEFAULTS)) if (theme[k] != null) q.set(k, theme[k]);
  let link = document.querySelector('link[href*="/__pyweb/theme.css"], link[data-pw-dev-theme]');
  if (!link) { link = document.createElement("link"); link.rel = "stylesheet"; document.head.appendChild(link); }
  link.dataset.pwDevTheme = "";
  link.href = "/__pyweb/dev/theme.css?" + q;
  const code = root.querySelector("#theme-code");
  if (code) code.textContent = themeCode();
}
async function themeTab() {
  if (!themeInfo) { themeInfo = await get("/__pyweb/dev/theme"); theme = {...themeInfo.theme}; }
  const opt = (list, cur) => list.map(v => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(v)}</option>`).join("");
  main.innerHTML = `<div class="form">
    <label>Accent <input type="color" data-f="accent" value="${esc(theme.accent)}"></label>
    <label>Gray <input type="color" data-f="gray" value="${esc(theme.gray || theme.accent)}"></label>
    <label>Radius <span><input type="range" min="0" max="24" step="1" data-f="radius" value="${esc(theme.radius)}"> ${esc(theme.radius)}px</span></label>
    <label>Font <select data-f="font">${opt([...new Set([...themeInfo.fonts, theme.font])], theme.font)}</select></label>
    <label>Density <select data-f="density">${opt(themeInfo.densities, theme.density)}</select></label>
    <label>Dark mode <select data-f="dark">${opt(["auto", "light", "dark"], theme.dark)}</select></label>
  </div><div>${Object.keys(themeInfo.presets).map(p => `<button class="btn" data-preset="${esc(p)}">${esc(p)}</button>`).join("")}</div>
  <code class="code" id="theme-code">${esc(themeCode())}</code>
  <button class="btn" data-copy-theme>Copy as code</button>` +
  (themeInfo.can_save ? `<button class="btn on" data-save-theme>Save to app.pyweb</button>` : "") +
  ` <span class="${themeMsg.startsWith("Saved") ? "ok" : "bad"}">${esc(themeMsg)}</span>`;
}
root.addEventListener("input", ev => {
  const f = ev.target.dataset && ev.target.dataset.f;
  if (!f || !theme) return;
  theme[f] = ev.target.value;
  if (f === "radius") ev.target.parentNode.lastChild.textContent = " " + ev.target.value + "px";
  preview();
});
// ---------------------------------------------------------------- Motion: pick an element, tune, replay
let picked = null, picking = false;
const specOf = el => { try { return JSON.parse(el.getAttribute("data-pw-motion") || "{}"); } catch (e) { return {}; } };
const pyLit = v => JSON.stringify(v).replace(/\btrue\b/g, "True").replace(/\bfalse\b/g, "False").replace(/\bnull\b/g, "None");
function motionTab() {
  if (!picked) {
    main.innerHTML = `<p><button class="btn ${picking ? "on" : ""}" data-pick>${picking ? "Click an animated element…" : "Pick an element"}</button>` +
      ` <span class="muted">${document.querySelectorAll("[data-pw-motion]").length} animated elements on this page</span></p>`;
    return;
  }
  const spec = specOf(picked), t = spec.transition || {};
  const type = t.type || "spring";
  const num = (k, lo, hi, step, def) => `<label>${k} <span><input type="range" min="${lo}" max="${hi}" step="${step}" data-m="${k}" value="${t[k] ?? def}"> ${t[k] ?? def}</span></label>`;
  main.innerHTML = `<p><span class="tag">&lt;${esc(picked.localName)}&gt;</span> <code>${esc(pyLit({animate: spec.animate, initial: spec.initial}))}</code></p>
    <div class="form"><label>type <select data-m="type">${["spring", "tween"].map(v => `<option ${v === type ? "selected" : ""}>${v}</option>`).join("")}</select></label>` +
    (type === "spring" ? num("stiffness", 20, 1000, 10, 500) + num("damping", 1, 80, 1, 32) + num("mass", 0.1, 5, 0.1, 1)
     : num("duration", 0.05, 2, 0.05, 0.3) + `<label>ease <select data-m="ease">${["ease-out", "ease-in-out", "ease-in", "linear", "back-out"].map(v => `<option ${v === (t.ease || "ease-out") ? "selected" : ""}>${v}</option>`).join("")}</select></label>`) +
    `</div><button class="btn on" data-replay>Replay</button><button class="btn" data-pick>Pick another</button>
    <code class="code" id="motion-code">transition={${esc(pyLit(t))}}</code><button class="btn" data-copy-motion>Copy as code</button>`;
}
root.addEventListener("change", ev => {
  const k = ev.target.dataset && ev.target.dataset.m;
  if (!k || !picked) return;
  const spec = specOf(picked);
  const t = {...(spec.transition || {})};
  t[k] = k === "type" || k === "ease" ? ev.target.value : Number(ev.target.value);
  if (k === "type") { for (const x of ["stiffness", "damping", "mass", "duration", "ease"]) delete t[x]; }
  spec.transition = t;
  picked.setAttribute("data-pw-motion", JSON.stringify(spec));
  motionTab();
  if (window.pywebMotion) window.pywebMotion.replay(picked);
});
function pickOne(ev) {
  if (ev.composedPath().includes(host)) return;
  const el = ev.target instanceof Element && ev.target.closest("[data-pw-motion]");
  if (!el) return;
  ev.preventDefault(); ev.stopPropagation();
  document.removeEventListener("click", pickOne, true);
  picking = false;
  if (picked) picked.style.outline = "";
  picked = el;
  el.style.outline = "2px dashed #a5b4fc";
  tab = "motion"; panel.classList.add("open"); render();
}
root.addEventListener("click", async ev => {
  const t = ev.target.closest("[data-preset],[data-copy-theme],[data-save-theme],[data-pick],[data-replay],[data-copy-motion]");
  if (!t) return;
  if (t.dataset.preset !== undefined) { theme = {...theme, ...themeInfo.presets[t.dataset.preset]}; preview(); themeTab(); }
  else if (t.hasAttribute("data-copy-theme")) { navigator.clipboard && navigator.clipboard.writeText(themeCode()); themeMsg = ""; }
  else if (t.hasAttribute("data-save-theme")) {
    const r = await fetch("/__pyweb/dev/theme", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(theme)});
    const d = await r.json();
    themeMsg = r.ok ? "Saved: " + d.code : (d.error || "could not save");
    themeTab();
  }
  else if (t.hasAttribute("data-pick")) {
    if (picked) { picked.style.outline = ""; picked = null; }
    picking = true; document.addEventListener("click", pickOne, true); motionTab();
  }
  else if (t.hasAttribute("data-replay") && picked && window.pywebMotion) window.pywebMotion.replay(picked);
  else if (t.hasAttribute("data-copy-motion") && picked && navigator.clipboard) navigator.clipboard.writeText("transition={" + pyLit(specOf(picked).transition || {}) + "}");
});
root.addEventListener("click", async ev => {
  const t = ev.target.closest("[data-tab],[data-id],[data-retry],.x,.pill");
  if (!t) return;
  if (t.classList.contains("pill")) { panel.classList.toggle("open"); if (panel.classList.contains("open")) render(); return; }
  if (t.classList.contains("x")) { panel.classList.remove("open"); return; }
  if (t.dataset.tab) { tab = t.dataset.tab; render(); return; }
  if (t.dataset.id) { selected = selected === t.dataset.id ? null : t.dataset.id; render(); return; }
  if (t.dataset.retry) { await fetch(`/__pyweb/dev/jobs/${encodeURIComponent(t.dataset.retry)}/retry`, {method: "POST"}); render(); }
});
badge();
(document.body || document.documentElement).appendChild(host);
timer = setInterval(() => { if (!document.hidden) poll(); }, 1500);
})();</script>"""
