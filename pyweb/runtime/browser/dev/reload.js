// pyweb dev only: reload the page when the app changes, keeping what you were doing.
// Before reloading it saves page variables (signals the compiler named), form fields, scroll and focus
// in sessionStorage; after the reload it hands the variables to the page as its starting state (only
// when the type still matches) and puts the rest back once the page is ready. ?fresh skips restoring.
(() => {
  const KEY = "pyweb-dev-state:" + location.pathname + location.search;
  const G = window.__pyweb || (window.__pyweb = { mounts: new Map(), disposers: new Map(), navState: new Map(), nav: false });
  if (!G.navState) G.navState = new Map();
  const kept = new Map();
  G.keep = (name, s) => kept.set(name, s);
  const kind = (v) => (v === null ? "null" : Array.isArray(v) ? "array" : typeof v);
  const SKIP = ["password", "file", "hidden", "submit", "button", "reset", "image"];
  const keyOf = (el) => {
    if (el.id) return "#" + CSS.escape(el.id);
    if (!el.name) return null;
    const base = `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
    return el.type === "radio" ? `${base}[value="${CSS.escape(el.value)}"]` : base;
  };

  function snapshot() {
    const signals = {};
    for (const [name, s] of kept) {
      try {
        const json = JSON.stringify(s.peek());
        if (json !== undefined) signals[name] = JSON.parse(json);
      } catch { /* not JSON: starts over */ }
    }
    const fields = [];
    for (const el of document.querySelectorAll("input, textarea, select")) {
      if (SKIP.includes((el.type || "").toLowerCase())) continue;
      const key = keyOf(el);
      if (!key) continue;
      fields.push([key, el.type === "checkbox" || el.type === "radio" ? { checked: el.checked } : { value: el.value }]);
    }
    const a = document.activeElement;
    const focus = a && a !== document.body ? keyOf(a) : null;
    let selection = null;
    try { if (focus && a.selectionStart != null) selection = [a.selectionStart, a.selectionEnd]; } catch { /* not a text field */ }
    return { signals, fields, scroll: [scrollX, scrollY], focus, selection };
  }

  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(KEY) || "null");
    sessionStorage.removeItem(KEY);
  } catch { saved = null; }
  if (new URLSearchParams(location.search).has("fresh")) saved = null;

  if (saved) {
    // Page variables: the page reads its starting state from G.navState before the <script> JSON.
    for (const el of document.querySelectorAll('script[type="application/json"][id^="pw-state"]')) {
      let state;
      try { state = JSON.parse(el.textContent || "{}"); } catch { continue; }
      let changed = false;
      for (const [name, value] of Object.entries(saved.signals || {})) {
        if (name in state && state[name] !== null && kind(state[name]) !== kind(value)) continue; // its type changed
        state[name] = value;
        changed = true;
      }
      if (changed) G.navState.set(el.id, JSON.stringify(state));
    }
    const restore = () => {
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
    };
    const started = Date.now();
    const whenReady = () => {
      const roots = document.querySelectorAll("[data-pw-root]").length;
      const ready = document.querySelectorAll("[data-pw-ready]").length;
      if (ready >= roots || Date.now() - started > 3000) requestAnimationFrame(restore);
      else setTimeout(whenReady, 20);
    };
    if (document.readyState === "complete") whenReady();
    else addEventListener("load", whenReady, { once: true });
  }

  let version = null;
  async function poll() {
    try {
      const now = await (await fetch("/__pyweb/dev/version")).text();
      if (version !== null && now !== version) {
        try { sessionStorage.setItem(KEY, JSON.stringify(snapshot())); } catch { /* storage full or off */ }
        location.reload();
        return;
      }
      version = now;
    } catch { /* the server is restarting */ }
    setTimeout(poll, 600);
  }
  poll();
})();
