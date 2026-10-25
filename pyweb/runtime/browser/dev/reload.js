// pyweb dev only: apply each save to the open page without losing what you were doing.
//
// Stylesheet changes swap the <link> in place. Code changes swap the page in place through the
// runtime's client navigation (no reload, no flash) and put back every page and component variable
// (signals the compiler named), form fields, scroll and focus. When the page's structure changed too
// much to swap, it reloads, and the same state comes back from sessionStorage. ?fresh starts over.
(() => {
  const KEY = "pyweb-dev-state:" + location.pathname + location.search;
  const G = window.__pyweb || (window.__pyweb = { mounts: new Map(), disposers: new Map(), navState: new Map(), nav: false });
  if (!G.navState) G.navState = new Map();
  const kind = (v) => (v === null ? "null" : Array.isArray(v) ? "array" : typeof v);
  const SKIP = ["password", "file", "hidden", "submit", "button", "reset", "image"];
  const isPage = () => !!document.querySelector("[data-pw-root]");

  // Every named signal, keyed "name#k": the k-th one with that name since the page mounted, so two
  // instances of a component keep their own values. A pending restore is applied as each is created.
  let kept = new Map();
  let counts = new Map();
  let pending = null;
  G.keep = (name, s) => {
    const k = counts.get(name) || 0;
    counts.set(name, k + 1);
    const key = name + "#" + k;
    s.$devKey = key;                  // the timeline records changes by this key
    kept.set(key, s);
    if (pending && key in pending) {
      const value = pending[key];
      const now = s.peek();
      if (now === null || value === null || kind(now) === kind(value)) s(value); // a changed type starts over
    }
  };
  G.devSignals = () => kept;          // the toolbar's inspector and timeline read these

  const keyOf = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    if (!el.name) return null;
    const base = `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
    return el.type === "radio" ? `${base}[value="${CSS.escape(el.value)}"]` : base;
  };

  function snapshot() {
    const signals = {};
    for (const [key, s] of kept) {
      try {
        const json = JSON.stringify(s.peek());
        if (json !== undefined) signals[key] = JSON.parse(json);
      } catch { /* not JSON: starts over */ }
    }
    const fields = [];
    for (const el of document.querySelectorAll("input, textarea, select")) {
      if (SKIP.includes((el.type || "").toLowerCase())) continue;
      const key = keyOf(el);
      if (key) fields.push([key, el.type === "checkbox" || el.type === "radio" ? { checked: el.checked } : { value: el.value }]);
    }
    const a = document.activeElement;
    const focus = a && a !== document.body ? keyOf(a) : null;
    let selection = null;
    try { if (focus && a.selectionStart != null) selection = [a.selectionStart, a.selectionEnd]; } catch { /* not a text field */ }
    return { signals, fields, scroll: [scrollX, scrollY], focus, selection };
  }

  function restoreRest(saved) {
    for (const [key, v] of saved.fields || []) {
      const el = document.querySelector(key);
      if (!el) continue;
      if ("checked" in v) el.checked = v.checked;
      else if (el.value !== v.value) el.value = v.value;
    }
    scrollTo(saved.scroll[0], saved.scroll[1]);
    const f = saved.focus && document.querySelector(saved.focus);
    if (f) {
      f.focus({ preventScroll: true });
      try { if (saved.selection) f.setSelectionRange(saved.selection[0], saved.selection[1]); } catch { /* not a text field */ }
    }
    document.documentElement.setAttribute("data-pw-dev-restored", "");
  }

  function whenReady(fn) {
    const started = Date.now();
    const check = () => {
      const roots = document.querySelectorAll("[data-pw-root]").length;
      const ready = document.querySelectorAll("[data-pw-ready]").length;
      if (ready >= roots || Date.now() - started > 3000) requestAnimationFrame(fn);
      else setTimeout(check, 20);
    };
    check();
  }

  function toast(text) {
    const el = document.createElement("div");
    el.textContent = text;
    el.setAttribute("data-pw-dev-toast", "");
    Object.assign(el.style, {
      position: "fixed", left: "12px", bottom: "12px", zIndex: 2147483646, padding: "6px 10px",
      font: "12px/1.4 system-ui, sans-serif", color: "#fff", background: "rgba(20,20,24,.85)",
      borderRadius: "6px", pointerEvents: "none", transition: "opacity .4s", opacity: "1",
    });
    document.body.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; }, 900);
    setTimeout(() => el.remove(), 1400);
  }

  // After a full load: state saved before it (a reload, or a swap that had to fall back to one).
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (saved && isPage()) sessionStorage.removeItem(KEY);   // an error page keeps it for after the fix
  } catch { saved = null; }
  const fresh = new URLSearchParams(location.search).has("fresh");
  if (fresh) saved = null;
  if (saved && isPage()) {
    pending = saved.signals || {};
    G.nohydrate = true;               // the server's HTML has the old values: render once on the client
    const done = () => { pending = null; G.nohydrate = false; restoreRest(saved); };
    if (document.readyState === "complete") whenReady(done);
    else addEventListener("load", () => whenReady(done), { once: true });
  }

  function swapStyles(urls, version) {
    for (const link of document.querySelectorAll('link[rel="stylesheet"]')) {
      const url = new URL(link.href, location.href);
      if (!urls.includes(url.pathname)) continue;
      const fresh = link.cloneNode();
      url.searchParams.set("v", "dev" + version);
      fresh.href = url.pathname + url.search;
      fresh.addEventListener("load", () => link.remove(), { once: true });
      link.after(fresh);
    }
  }

  let swapping = false;
  async function hotSwap() {
    const state = snapshot();
    if (isPage()) {
      try { sessionStorage.setItem(KEY, JSON.stringify(state)); } catch { /* storage full or off */ }
    }
    if (!G.navigate || !isPage()) { location.reload(); return; }
    swapping = true;
    pending = fresh ? null : state.signals;
    G.nohydrate = !fresh;
    kept = new Map();
    counts = new Map();
    G.at = null;                      // set again only when navigate swaps in place
    try {
      await G.navigate(location.href, { push: false, scroll: state.scroll });
    } finally {
      swapping = false;
    }
    if (G.at === null) return;        // it fell back to a full load: the saved state is for that page
    whenReady(() => {
      pending = null;
      G.nohydrate = false;
      try { sessionStorage.removeItem(KEY); } catch { /* storage off */ }
      if (!fresh) restoreRest(state);
      window.dispatchEvent(new CustomEvent("pyweb:hot", { detail: { kind: "code" } }));
      toast("updated");
    });
  }

  let version = null;
  async function poll() {
    try {
      const change = await (await fetch("/__pyweb/dev/changes")).json();
      if (version !== null && change.version !== version && !swapping) {
        version = change.version;
        if (change.kind === "css" && change.css.length) {
          swapStyles(change.css, change.version);
          window.dispatchEvent(new CustomEvent("pyweb:hot", { detail: { kind: "css" } }));
          toast("styles updated");
        } else {
          await hotSwap();
        }
      } else {
        version = change.version;
      }
    } catch { /* the server is restarting */ }
    setTimeout(poll, 250);
  }
  poll();
})();
