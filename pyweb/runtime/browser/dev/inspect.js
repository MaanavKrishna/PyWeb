// pyweb dev only: hold Alt to see where an element comes from; Alt-click opens that line in your editor
// and shows the inspector (the component chain and its live variables, which you can change).
(() => {
  if (window.__pywebInspect) return;
  window.__pywebInspect = true;
  const G = window.__pyweb || (window.__pyweb = { mounts: new Map(), disposers: new Map(), navState: new Map(), nav: false });
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const short = (src) => { const i = src.lastIndexOf("/ui/kit/"); return i >= 0 ? "kit/" + src.slice(i + 8) : src; };

  const host = document.createElement("pyweb-inspect");
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
:host{all:initial}*{box-sizing:border-box;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.box{position:fixed;pointer-events:none;z-index:2147483645;border:2px solid #818cf8;background:rgba(129,140,248,.12);border-radius:3px;display:none}
.tip{position:fixed;pointer-events:none;z-index:2147483646;background:#111827;color:#e5e7eb;padding:3px 8px;border-radius:5px;display:none;white-space:nowrap}
.tip b{color:#a5b4fc;font-weight:600}
.panel{position:fixed;top:12px;right:12px;width:min(380px,calc(100vw - 24px));max-height:70vh;overflow:auto;z-index:2147483647;
 background:#0b1020;color:#e5e7eb;border:1px solid #374151;border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.35);padding:10px 12px;display:none}
.panel.open{display:block}h4{margin:8px 0 4px;color:#a5b4fc;font-weight:600}.muted{color:#6b7280}
.x{float:right;background:none;border:0;color:#9ca3af;cursor:pointer}
table{border-collapse:collapse;width:100%}td{padding:2px 4px;vertical-align:middle;border-bottom:1px solid #1f2937}
input{width:100%;background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:5px;padding:2px 5px}
input.bad{border-color:#f87171}a,button.link{color:#93c5fd;background:none;border:0;cursor:pointer;padding:0;text-align:left}
</style><div class="box"></div><div class="tip"></div><div class="panel" role="dialog" aria-label="PyWeb inspector"></div>`;
  const box = root.querySelector(".box"), tip = root.querySelector(".tip"), panel = root.querySelector(".panel");

  const target = (el) => (el instanceof Element ? el.closest("[data-pw-src]") : null);
  const chainOf = (el) => { const out = []; for (let e = el; e; e = e.parentElement) if (e.dataset && e.dataset.pwSrc) out.push(e.dataset.pwSrc); return out; };
  const components = (el) => {
    const out = [];
    for (let e = el; e; e = e.parentElement) {
      const name = e.getAttribute && e.getAttribute("data-pw-comp");
      if (!name) continue;
      const index = [...document.querySelectorAll(`[data-pw-comp="${CSS.escape(name)}"]`)].indexOf(e);
      out.push({ name, src: e.dataset.pwSrc, index });
    }
    return out;
  };

  function hide() { box.style.display = tip.style.display = "none"; }
  function show(el) {
    const r = el.getBoundingClientRect();
    Object.assign(box.style, { display: "block", left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });
    const comps = components(el).map((c) => c.name);
    tip.innerHTML = `<b>${esc(comps.reverse().join(" › ") || el.localName)}</b> ${esc(short(el.dataset.pwSrc))}`;
    tip.style.display = "block";
    tip.style.left = Math.max(4, Math.min(r.left, innerWidth - tip.offsetWidth - 4)) + "px";
    tip.style.top = (r.top > 28 ? r.top - 26 : r.bottom + 4) + "px";
  }

  // ------------------------------------------------------------ the inspector panel
  let picked = null, timer = null, note = "";
  function signalsFor(comp) {
    const all = G.devSignals ? [...G.devSignals()] : [];
    if (comp === null) return all.filter(([k]) => !k.split("#")[0].includes("."));
    return all.filter(([k]) => k.startsWith(comp.name + ".") && k.endsWith("#" + comp.index));
  }
  const show_ = (v) => { try { return JSON.stringify(v); } catch { return String(v); } };
  function rows(list) {
    if (!list.length) return `<p class="muted">no variables</p>`;
    return `<table>` + list.map(([k, s]) => `<tr><td>${esc(k.split("#")[0].split(".").pop())}</td>` +
      `<td><input data-key="${esc(k)}" value="${esc(show_(s.peek()))}" aria-label="${esc(k)}"></td></tr>`).join("") + `</table>`;
  }
  function render() {
    if (!picked || !picked.isConnected) { close(); return; }
    const comps = components(picked);
    let h = `<button class="x" title="close (Esc)">✕</button><div><b>&lt;${esc(picked.localName)}&gt;</b> ` +
      `<button class="link" data-open="${esc(picked.dataset.pwSrc)}">${esc(short(picked.dataset.pwSrc))}</button></div>`;
    if (note) h += `<p class="muted">${note}</p>`;
    for (const c of comps) {
      const isPage = !c.name.includes(".") && c === comps[comps.length - 1] && picked.closest("[data-pw-root]");
      h += `<h4>${esc(c.name)}${c.index > 0 ? ` <span class="muted">#${c.index + 1}</span>` : ""}</h4>` +
        (c.src ? `<button class="link" data-open="${esc(c.src)}">${esc(short(c.src))}</button>` : "") +
        rows(isPage ? signalsFor(c).concat(signalsFor(null)) : signalsFor(c));
    }
    panel.innerHTML = h;
    panel.classList.add("open");
  }
  function refresh() {
    if (!panel.classList.contains("open")) return;
    for (const input of panel.querySelectorAll("input[data-key]")) {
      if (input === root.activeElement) continue;
      const s = G.devSignals && G.devSignals().get(input.dataset.key);
      if (s) input.value = show_(s.peek());
    }
  }
  function close() { panel.classList.remove("open"); picked = null; clearInterval(timer); timer = null; }

  async function open(chain) {
    try {
      const r = await fetch("/__pyweb/dev/open", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chain }) });
      const d = await r.json();
      if (d.opened) note = `opened ${esc(d.file)}:${d.line}`;
      else if (d.url) note = `<a href="${esc(d.url)}">open ${esc(d.file)}:${d.line} in VS Code</a>${d.error ? " · " + esc(d.error) : ""}`;
      else note = esc(d.error || "could not open");
    } catch { note = "the dev server isn't answering"; }
    if (picked) render();
    window.dispatchEvent(new CustomEvent("pyweb:open", { detail: { chain, note } }));
  }

  // ------------------------------------------------------------ events
  addEventListener("mousemove", (ev) => {
    const el = ev.altKey && !ev.composedPath().includes(host) ? target(ev.target) : null;
    if (el) show(el); else hide();
  }, true);
  addEventListener("keyup", (ev) => { if (ev.key === "Alt") hide(); }, true);
  addEventListener("keydown", (ev) => { if (ev.key === "Escape" && panel.classList.contains("open")) close(); }, true);
  addEventListener("click", (ev) => {
    if (!ev.altKey || ev.shiftKey || ev.composedPath().includes(host)) return;
    const el = target(ev.target);
    if (!el) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    hide();
    picked = el;
    note = "";
    render();
    clearInterval(timer);
    timer = setInterval(refresh, 300);
    open(chainOf(el));
  }, true);
  root.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-open],.x");
    if (!t) return;
    if (t.classList.contains("x")) close();
    else open([t.dataset.open]);
  });
  root.addEventListener("change", (ev) => {
    const input = ev.target;
    const s = input.dataset && input.dataset.key && G.devSignals && G.devSignals().get(input.dataset.key);
    if (!s) return;
    try { s(JSON.parse(input.value)); input.classList.remove("bad"); }
    catch { input.classList.add("bad"); }
  });
  addEventListener("pyweb:hot", () => { if (picked && !picked.isConnected) close(); else if (picked) render(); });
  G.inspect = (el) => { picked = target(el); if (picked) render(); };   // for the toolbar and tests
  (document.body || document.documentElement).appendChild(host);
})();
