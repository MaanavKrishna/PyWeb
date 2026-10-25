// pyweb dev only: "Fix with AI" on error pages, and Alt+Shift-click any element to change it with AI.
// The answer is shown as a diff; nothing is written until you press Apply, and Undo puts it back.
(() => {
  if (window.__pywebAssist) return;
  window.__pywebAssist = true;
  const UNDO = "pyweb-dev-undo";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  const host = document.createElement("pyweb-assist");
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
:host{all:initial}*{box-sizing:border-box;font:13px/1.45 system-ui,sans-serif}
code,pre,textarea{font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.fab{position:fixed;right:16px;bottom:16px;z-index:2147483646;background:#4f46e5;color:#fff;border:0;border-radius:999px;
 padding:9px 16px;cursor:pointer;box-shadow:0 6px 20px rgba(79,70,229,.45);font-weight:600}
.fab:hover{background:#4338ca}
.card{position:fixed;z-index:2147483647;background:#0b1020;color:#e5e7eb;border:1px solid #374151;border-radius:12px;
 box-shadow:0 14px 40px rgba(0,0,0,.45);padding:12px 14px;display:none}
.card.open{display:block}
.panel{left:50%;top:6vh;transform:translateX(-50%);width:min(860px,calc(100vw - 24px));max-height:86vh;overflow:auto}
.ask{width:min(380px,calc(100vw - 24px))}
textarea{width:100%;min-height:64px;background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:8px;padding:6px 8px;resize:vertical}
.btn{background:#1f2937;color:#e5e7eb;border:1px solid #374151;border-radius:7px;padding:5px 12px;cursor:pointer;margin:8px 6px 0 0}
.btn.go{background:#4f46e5;border-color:#6366f1;color:#fff}.btn:disabled{opacity:.5;cursor:default}
.muted{color:#9ca3af}.bad{color:#fca5a5}.ok{color:#86efac}
pre.diff{background:#060a16;border-radius:8px;padding:8px 0;overflow:auto;max-height:56vh;margin:8px 0}
pre.diff span{display:block;padding:0 10px;white-space:pre}
.add{background:rgba(34,197,94,.14);color:#bbf7d0}.del{background:rgba(239,68,68,.14);color:#fecaca}.hunk{color:#93c5fd}
.spin{display:inline-block;width:12px;height:12px;border:2px solid #6366f1;border-top-color:transparent;border-radius:50%;
 animation:s .8s linear infinite;vertical-align:-2px;margin-right:6px}@keyframes s{to{transform:rotate(1turn)}}
.toast{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:2147483647;background:#111827;color:#e5e7eb;
 border:1px solid #374151;border-radius:9px;padding:7px 12px;display:none}.toast.open{display:block}
.toast .btn{margin:0 0 0 10px;padding:2px 10px}
</style>
<button class="fab" hidden>Fix with AI</button>
<div class="card panel" role="dialog" aria-label="AI change"></div>
<div class="card ask" role="dialog" aria-label="Edit with AI"></div>
<div class="toast" role="status"></div>`;
  const fab = root.querySelector(".fab"), panel = root.querySelector(".panel"), ask = root.querySelector(".ask"), toast = root.querySelector(".toast");

  async function post(what, data) {
    const r = await fetch("/__pyweb/dev/ai/" + what, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });
    let d = {};
    try { d = await r.json(); } catch { d = { error: "the dev server didn't answer" }; }
    if (!r.ok && !d.error) d.error = "failed (" + r.status + ")";
    return d;
  }

  function diffHtml(diff) {
    return diff.split("\n").filter((l) => !l.startsWith("---") && !l.startsWith("+++")).map((l) => {
      const cls = l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "hunk" : "";
      return `<span class="${cls}">${esc(l) || " "}</span>`;
    }).join("");
  }

  let current = null;
  function show(html) { panel.innerHTML = html; panel.classList.add("open"); }
  function close() { panel.classList.remove("open"); current = null; }
  function thinking(what) { show(`<p><span class="spin"></span>${esc(what)}…</p><p class="muted">This can take a minute. Nothing changes until you press Apply.</p>`); }

  function result(d) {
    if (d.error) { show(`<p class="bad">${esc(d.error)}</p><button class="btn" data-close>Close</button>`); return; }
    if (!d.provider && d.prompt) {
      show(`<p>${esc(d.hint)}</p><textarea readonly rows="10">${esc(d.prompt)}</textarea>` +
        `<button class="btn go" data-copy>Copy prompt</button><button class="btn" data-close>Close</button>`);
      current = { prompt: d.prompt };
      return;
    }
    current = d;
    const head = `<p><b>${esc(d.file)}</b> <span class="muted">· ${esc(d.provider || "")}</span></p>` +
      (d.note ? `<p>${esc(d.note)}</p>` : "");
    if (!d.changed) { show(head + `<p class="muted">The answer didn't change the file.</p><button class="btn" data-close>Close</button>`); return; }
    show(head + (d.problem ? `<p class="bad">This change doesn't compile, so it can't be applied:</p><pre class="diff"><span>${esc(d.problem)}</span></pre>` : "") +
      `<pre class="diff">${diffHtml(d.diff)}</pre>` +
      `<button class="btn go" data-apply ${d.problem ? "disabled" : ""}>Apply</button><button class="btn" data-close>Discard</button>`);
  }

  function note(html, ms) {
    toast.innerHTML = html;
    toast.classList.add("open");
    clearTimeout(note.t);
    note.t = setTimeout(() => toast.classList.remove("open"), ms || 4000);
  }
  function offerUndo(id, file) {
    note(`Changed ${esc(file)} <button class="btn" data-undo="${esc(id)}">Undo</button>`, 15000);
  }

  // ------------------------------------------------------------ Fix with AI (error pages)
  // The toolbar's request summary (status, id) is added after this script: look once the page is parsed.
  const req = () => window.__PYWEB_REQ__ || {};
  const check = () => { if (document.querySelector("[data-pw-error]") || req().status >= 500) fab.hidden = false; };
  if (document.readyState === "loading") addEventListener("DOMContentLoaded", check, { once: true });
  else check();
  async function fix() {
    thinking("Asking for a fix");
    result(await post("fix", { request_id: req().id || null }));
  }

  // ------------------------------------------------------------ Edit with AI (Alt+Shift-click)
  let chain = null;
  addEventListener("click", (ev) => {
    if (!ev.altKey || !ev.shiftKey || ev.composedPath().includes(host)) return;
    const el = ev.target instanceof Element ? ev.target.closest("[data-pw-src]") : null;
    if (!el) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    chain = [];
    for (let e = el; e; e = e.parentElement) if (e.dataset && e.dataset.pwSrc) chain.push(e.dataset.pwSrc);
    const r = el.getBoundingClientRect();
    ask.innerHTML = `<p><b>&lt;${esc(el.localName)}&gt;</b> <span class="muted">${esc(el.dataset.pwSrc.split("/").pop())}</span></p>` +
      `<textarea placeholder="What should change? e.g. make this a card with a delete button" aria-label="What should change"></textarea>` +
      `<button class="btn go" data-edit>Edit with AI</button><button class="btn" data-cancel>Cancel</button>`;
    ask.style.left = Math.max(12, Math.min(r.left, innerWidth - 400)) + "px";
    ask.style.top = Math.max(12, Math.min(r.bottom + 8, innerHeight - 200)) + "px";
    ask.classList.add("open");
    ask.querySelector("textarea").focus();
  }, true);
  async function edit() {
    const instruction = ask.querySelector("textarea").value.trim();
    if (!instruction) return;
    ask.classList.remove("open");
    thinking("Asking for the change");
    result(await post("edit", { chain, instruction }));
  }

  root.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { ask.classList.remove("open"); close(); }
    if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey) && ask.classList.contains("open")) edit();
  });
  root.addEventListener("click", async (ev) => {
    const t = ev.target.closest("button");
    if (!t) return;
    if (t === fab) fix();
    else if (t.hasAttribute("data-close")) close();
    else if (t.hasAttribute("data-cancel")) ask.classList.remove("open");
    else if (t.hasAttribute("data-edit")) edit();
    else if (t.hasAttribute("data-copy") && current) { navigator.clipboard && navigator.clipboard.writeText(current.prompt); t.textContent = "Copied"; }
    else if (t.hasAttribute("data-apply") && current) {
      const d = await post("apply", { id: current.id });
      if (d.error) { result(d); return; }
      try { sessionStorage.setItem(UNDO, JSON.stringify({ id: d.undo, file: d.file, at: Date.now() })); } catch { /* storage off */ }
      close();
      offerUndo(d.undo, d.file);              // the save reloads the page in place; the toast comes back after a full load
    } else if (t.dataset.undo) {
      const d = await post("undo", { id: t.dataset.undo });
      try { sessionStorage.removeItem(UNDO); } catch { /* storage off */ }
      note(d.error ? `<span class="bad">${esc(d.error)}</span>` : `Put ${esc(d.file)} back`, 5000);
    }
  });

  try {
    const u = JSON.parse(sessionStorage.getItem(UNDO) || "null");
    if (u && Date.now() - u.at < 30000) offerUndo(u.id, u.file);
  } catch { /* storage off */ }
  (document.body || document.documentElement).appendChild(host);
})();
