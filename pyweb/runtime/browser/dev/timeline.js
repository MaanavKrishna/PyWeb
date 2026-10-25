// pyweb dev only: time travel. Every change to a page or component variable is recorded with what
// caused it (a click, typing, a server call); the toolbar's Timeline tab steps back through them.
// While you look at an earlier step the page is paused (clicks and typing are held back); Resume puts
// every variable back to its latest value. Only the page's own state moves: server data stays as it is.
(() => {
  if (window.__pywebTimeline) return;
  const G = window.__pyweb || (window.__pyweb = { mounts: new Map(), disposers: new Map(), navState: new Map(), nav: false });
  const LIMIT = 500;
  const json = (v) => { try { return JSON.stringify(v); } catch { return undefined; } };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  let entries = [];        // {step, t, key, value (JSON), cause}
  let steps = [];          // {n, cause, t}
  const initial = new Map();   // key -> JSON value when it was first created
  let cause = null;        // {id, label, t}: the last thing the person did (each event is its own step)
  let seq = 0;
  let applying = false;    // setting values while scrubbing: don't record
  let viewing = null;      // the step shown while paused, or null when live
  let host = null;         // where the tab renders (the toolbar's panel)

  // ------------------------------------------------------------ record
  const keep0 = G.keep;
  if (keep0) {
    G.keep = (name, s) => {
      keep0(name, s);
      if (s.$devKey && !initial.has(s.$devKey)) initial.set(s.$devKey, json(s.peek()));
    };
  }
  const describe = (el) => {
    if (!(el instanceof Element)) return "";
    const id = el.id ? "#" + el.id : "";
    const text = (el.getAttribute("aria-label") || el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 24);
    return el.localName + id + (text ? ` “${text}”` : "");
  };
  const inToolbar = (ev) => ev.composedPath().some((n) => n.localName && n.localName.startsWith("pyweb-"));
  for (const type of ["click", "input", "change", "submit", "keydown"]) {
    addEventListener(type, (ev) => {
      if (inToolbar(ev)) return;
      if (viewing !== null) { ev.preventDefault(); ev.stopImmediatePropagation(); return; }  // paused
      if (type === "keydown" && !["Enter", "Escape", " "].includes(ev.key)) return;
      cause = { id: ++seq, label: (type === "keydown" ? "key " + ev.key : type) + " " + describe(ev.target), t: Date.now() };
    }, true);
  }
  const fetch0 = window.fetch;
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const m = /\/__pyweb\/rpc\/([\w.]+)/.exec(url);
    const p = fetch0.apply(this, arguments);
    if (m) p.then(() => { cause = { id: ++seq, label: "server " + m[1] + "()", t: Date.now() }; }, () => {});
    return p;
  };

  G.onset = (s) => {
    if (applying || !s.$devKey) return;
    const now = Date.now();
    if (!cause || now - cause.t > 1500) cause = { id: ++seq, label: "update", t: now };   // a timer, live data, ...
    const last = steps[steps.length - 1];
    if (!last || last.id !== cause.id) steps.push({ n: (last ? last.n : 0) + 1, id: cause.id, cause: cause.label, t: now });
    if (cause.label === "update") cause.t = now;
    const n = steps[steps.length - 1].n;
    entries.push({ step: n, t: now, key: s.$devKey, value: json(s.peek()), cause: cause.label });
    if (entries.length > LIMIT) {                 // the oldest changes become the starting values
      for (const e of entries.splice(0, entries.length - LIMIT)) initial.set(e.key, e.value);
      const first = entries[0].step;
      steps = steps.filter((st) => st.n >= first);
    }
    if (host && viewing === null) render();
  };

  // ------------------------------------------------------------ travel
  const first = () => (steps.length ? steps[0].n - 1 : 0);
  function valuesAt(n) {
    const out = new Map(initial);
    for (const e of entries) if (e.step <= n) out.set(e.key, e.value);
    return out;
  }
  function apply(values) {
    const live = G.devSignals ? G.devSignals() : new Map();
    applying = true;
    try {
      for (const [key, v] of values) {
        const s = live.get(key);
        if (s && v !== undefined && json(s.peek()) !== v) s(JSON.parse(v));
      }
    } finally { applying = false; }
  }
  function view(n) {
    const last = steps.length ? steps[steps.length - 1].n : 0;
    if (n >= last) { resume(); return; }
    viewing = Math.max(0, n);
    apply(valuesAt(viewing));
    document.documentElement.setAttribute("data-pw-timeline", String(viewing));
    if (host) render();
  }
  function resume() {
    const last = steps.length ? steps[steps.length - 1].n : 0;
    apply(valuesAt(last));
    viewing = null;
    document.documentElement.removeAttribute("data-pw-timeline");
    if (host) render();
  }
  function clear() { entries = []; steps = []; initial.clear(); for (const [k, s] of (G.devSignals ? G.devSignals() : [])) initial.set(k, json(s.peek())); if (host) render(); }

  // ------------------------------------------------------------ the toolbar tab
  function render() {
    if (!host || !host.isConnected) { host = null; return; }
    const last = steps.length ? steps[steps.length - 1].n : 0;
    const at = viewing === null ? last : viewing;
    const short = (v) => (v === undefined ? "?" : v.length > 40 ? v.slice(0, 39) + "…" : v);
    const rows = steps.slice(-60).reverse().map((st) => {
      const changes = entries.filter((e) => e.step === st.n);
      const names = [...new Set(changes.map((e) => e.key.split("#")[0]))];
      const final = names.map((nm) => { const e = changes.filter((c) => c.key.split("#")[0] === nm).pop(); return `${esc(nm)} = ${esc(short(e.value))}`; });
      return `<tr class="row ${st.n === at ? "sel" : ""}" data-step="${st.n}"><td class="n">${st.n}</td>` +
        `<td>${esc(st.cause)}</td><td><code>${final.join(", ")}</code></td></tr>`;
    }).join("");
    host.innerHTML =
      `<p>${viewing === null ? `<b>Live</b> <span class="muted">· ${entries.length} changes in ${last} steps</span>`
        : `<b class="warn">Paused at step ${viewing} of ${last}</b> <span class="muted">· clicks and typing are held back; server data isn't rolled back</span>`}</p>` +
      `<p><input type="range" min="${first()}" max="${last}" value="${at}" data-timeline-slider aria-label="Step" style="width:100%"></p>` +
      `<p><button class="btn ${viewing === null ? "" : "on"}" data-timeline-resume ${viewing === null ? "disabled" : ""}>Resume</button>` +
      `<button class="btn" data-timeline-back ${at <= first() ? "disabled" : ""}>◀ Step back</button>` +
      `<button class="btn" data-timeline-clear>Clear</button></p>` +
      (rows ? `<table>${rows}</table>` : `<p class="muted">Use the page: each change to its variables shows up here.</p>`);
  }
  function mount(el) {
    host = el;
    render();
    if (el.$timeline) return;
    el.$timeline = true;
    el.addEventListener("input", (ev) => { if (ev.target.hasAttribute("data-timeline-slider")) view(Number(ev.target.value)); });
    el.addEventListener("click", (ev) => {
      const t = ev.target.closest("[data-step],[data-timeline-resume],[data-timeline-back],[data-timeline-clear]");
      if (!t) return;
      if (t.dataset.step) view(Number(t.dataset.step));
      else if (t.hasAttribute("data-timeline-resume")) resume();
      else if (t.hasAttribute("data-timeline-back")) view((viewing === null ? (steps.length ? steps[steps.length - 1].n : 0) : viewing) - 1);
      else clear();
    });
  }
  addEventListener("pyweb:hot", () => { if (viewing !== null) resume(); });
  function unmount() { host = null; }
  window.__pywebTimeline = { mount, unmount, view, resume, clear, steps: () => steps.map((s) => ({ ...s })), entries: () => entries.slice() };
})();
