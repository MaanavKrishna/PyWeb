// PyWeb browser runtime: fine-grained signals, DOM rendering, Python
// semantics helpers (`py`), and typed RPC. No virtual DOM: every dynamic
// expression owns one effect that updates exactly the nodes it renders.

// ---------------------------------------------------------------- signals
//
// Dependency tracking: reading a signal inside an effect/computed records a
// dependency; writing it re-runs only the dependents. Effects created while
// another effect (or root) runs are *owned* by it and disposed when it
// re-runs or is disposed, so removed list rows and swapped branches never
// leak subscriptions.

let Listener = null;
let Owner = null;
let batchDepth = 0;
let flushing = false;
const pending = new Set();

function cleanNode(node) {
  if (node.sources) {
    for (const s of node.sources) s.observers.delete(node);
    node.sources.clear();
  }
  if (node.owned && node.owned.length) {
    const owned = node.owned;
    node.owned = [];
    for (const o of owned) disposeNode(o);
  }
  if (node.cleanups && node.cleanups.length) {
    const cbs = node.cleanups;
    node.cleanups = [];
    for (const c of cbs) {
      try { c(); } catch (e) { reportError(e); }
    }
  }
}

function disposeNode(node) {
  cleanNode(node);
  node.disposed = true;
  pending.delete(node);
}

function track(src) {
  if (Listener) {
    src.observers.add(Listener);
    Listener.sources.add(src);
  }
}

function notify(src) {
  for (const o of Array.from(src.observers)) {
    if (o.isComputed) {
      if (!o.stale) { o.stale = true; notify(o); }
    } else {
      pending.add(o);
    }
  }
  if (batchDepth === 0) flush();
}

function flush() {
  if (flushing) return;
  flushing = true;
  try {
    let guard = 0;
    while (pending.size) {
      if (++guard > 100000) throw new Error("pyweb: reactive update loop (an effect keeps changing its own inputs)");
      const e = pending.values().next().value;
      pending.delete(e);
      if (!e.disposed) runEffect(e);
    }
  } finally {
    flushing = false;
  }
}

function runEffect(e) {
  cleanNode(e);
  const pl = Listener; const po = Owner;
  Listener = e; Owner = e;
  try { e.fn(); } catch (err) {
    if (err instanceof Mismatch || H) throw err; // abort hydration; the client render reports it
    reportError(err);
  } finally { Listener = pl; Owner = po; }
}

/** A reactive value. `s()` reads (and tracks), `s(v)` writes. */
export function signal(value) {
  const node = { value, observers: new Set() };
  function s(next) {
    if (arguments.length === 0) { track(node); return node.value; }
    if (!Object.is(node.value, next)) { node.value = next; notify(node); }
    return next;
  }
  s.peek = () => node.value;
  s.touch = () => notify(node); // the value was changed in place (e.g. a library object)
  s.set = (v) => s(v);
  s.update = (fn) => s(fn(node.value));
  s.subscribe = (cb) => effect(() => cb(s()));
  s.$signal = true;
  return s;
}
export const sig = signal;

/** A lazily cached derivation; recomputes only after an input changed. */
export function computed(fn) {
  const node = { value: undefined, stale: true, isComputed: true,
    sources: new Set(), observers: new Set() };
  const c = () => {
    if (node.stale) {
      cleanNode(node);
      const pl = Listener;
      Listener = node;
      try { node.value = fn(); } finally { Listener = pl; }
      node.stale = false;
    }
    track(node);
    return node.value;
  };
  c.peek = () => untrack(c);
  c.$signal = true;
  return c;
}

/** Run `fn` now and again whenever a signal it read changes. */
export function effect(fn) {
  const e = { fn, sources: new Set(), owned: [], cleanups: [], disposed: false };
  if (Owner) Owner.owned.push(e);
  runEffect(e);
  return () => disposeNode(e);
}

export function onCleanup(fn) { if (Owner) Owner.cleanups.push(fn); }

/** Run `fn(dispose)` in a fresh ownership scope, detached from the caller. */
export function root(fn) {
  const r = { sources: new Set(), owned: [], cleanups: [] };
  const pl = Listener; const po = Owner;
  Listener = null; Owner = r;
  try { return fn(() => disposeNode(r)); } finally { Listener = pl; Owner = po; }
}

/** Apply several writes, then re-run each affected effect once. */
export function batch(fn) {
  batchDepth++;
  try { return fn(); } finally { if (--batchDepth === 0) flush(); }
}

export function untrack(fn) {
  const pl = Listener;
  Listener = null;
  try { return fn(); } finally { Listener = pl; }
}

export function resource(loader) {
  const r = { pending: false, error: null, result: null };
  r.load = async () => {
    r.pending = true;
    try { r.result = await loader(); } catch (e) { r.error = e; }
    r.pending = false;
    return r.result;
  };
  return r;
}

// ------------------------------------------------------------------ errors

export function reportError(err) {
  if (typeof console !== "undefined") console.error(err);
  if (typeof window !== "undefined" && typeof CustomEvent !== "undefined") {
    try { window.dispatchEvent(new CustomEvent("pyweb:error", { detail: err })); } catch { /* noop */ }
  }
}

// --------------------------------------------------------------- hydration
//
// The server already rendered the page. Instead of rebuilding it, the
// first render *adopts* those nodes: `h()` claims the next element, text
// holes claim (and split) the next text node, and region markers are
// inserted where the client needs them. Nothing is moved or replaced, so
// focus, caret position and anything typed before the script loaded
// survive. If the server DOM does not match what the client would render,
// hydration stops and the page is rendered from scratch instead.

let H = null; // {parent, next}: the next server node to claim, or null
let afterHydrate = [];

class Mismatch extends Error {}
function mismatch(what, node) {
  const found = node ? (node.nodeType === 1 ? `<${node.localName}>` : JSON.stringify(node.data)) : "nothing";
  throw new Mismatch(`pyweb: server HTML did not match (expected ${what}, found ${found}); rendering on the client`);
}

function isBlank(n) { return n.nodeType === 8 || (n.nodeType === 3 && !n.data.trim()); }

function place(node) {
  H.parent.insertBefore(node, H.next);
  return node;
}

function claimEl(tag) {
  let n = H.next;
  while (n && n.nodeType !== 1 && isBlank(n)) { const x = n.nextSibling; n.remove(); n = x; }
  if (!n || n.nodeType !== 1 || (n.localName !== tag && n.localName !== tag.toLowerCase())) mismatch(`<${tag}>`, n);
  H.next = n.nextSibling;
  return n;
}

function claimText(s) {
  if (s === "") return place(document.createTextNode(""));
  const n = H.next;
  if (!n || n.nodeType !== 3 || !n.data.startsWith(s)) mismatch(JSON.stringify(s), n);
  if (n.data.length > s.length) n.splitText(s.length);
  H.next = n.nextSibling;
  return n;
}

function claimChildren(el, kids) {
  const saved = H;
  H = { parent: el, next: el.firstChild };
  try {
    toNodes(typeof kids === "function" ? kids() : kids, []);
    while (H.next) {
      const n = H.next; H.next = n.nextSibling;
      if (el.localName === "textarea") continue; // its text is the value
      if (!isBlank(n)) mismatch("end of <" + el.localName + ">", n);
      n.remove();
    }
  } finally { H = saved; }
}

// --------------------------------------------------------------------- DOM

const SVG_NS = "http://www.w3.org/2000/svg";
const SVG_TAGS = new Set(["svg", "path", "circle", "rect", "line", "g", "polyline",
  "polygon", "ellipse", "defs", "use", "linearGradient", "radialGradient", "stop",
  "clipPath", "mask", "pattern", "symbol", "tspan", "foreignObject"]);
const PROPS = new Set(["value", "checked", "selected", "indeterminate"]);
const URL_ATTRS = new Set(["href", "src", "action", "formaction", "xlink:href"]);

/** Block `javascript:` (and similar) URLs from reaching the DOM. */
export function safeUrl(v) {
  const s = String(v);
  return /^\s*(javascript|vbscript|data:text\/html)/i.test(s.replace(/[\u0000-\u001f]/g, "")) ? "#" : s;
}

function kebab(k) { return k.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase()).replace(/_/g, "-"); }

export function clsx(v) {
  if (v == null || v === false) return "";
  if (Array.isArray(v)) return v.map(clsx).filter(Boolean).join(" ");
  if (typeof v === "object") return Object.keys(v).filter((k) => truth(v[k])).join(" ");
  return String(v);
}

function applyAttr(el, k, v) {
  if (k === "class" || k === "class_" || k === "className") {
    const c = clsx(v);
    if (c) el.setAttribute("class", c); else el.removeAttribute("class");
    return;
  }
  if (k === "style" && v && typeof v === "object") {
    el.removeAttribute("style");
    for (const key of Object.keys(v)) {
      if (v[key] != null && v[key] !== false) el.style.setProperty(kebab(key), String(v[key]));
    }
    return;
  }
  if (PROPS.has(k)) {
    el[k] = k === "value" ? (v == null ? "" : String(v)) : truth(v);
    return;
  }
  if (v == null || v === false) { el.removeAttribute(k); return; }
  if (v === true) { el.setAttribute(k, ""); return; }
  el.setAttribute(k, URL_ATTRS.has(k) ? safeUrl(v) : String(v));
}

function listen(el, ev, handler) {
  el.addEventListener(ev, (e) => {
    if (ev === "submit") e.preventDefault();
    let r;
    try { r = batch(() => handler(e)); } catch (err) { reportError(err); return; }
    if (r && typeof r.then === "function") r.then(null, reportError);
  });
}

/** Two-way binding between a form control and a signal. */
export function bind(el, s) {
  const type = (el.getAttribute("type") || "").toLowerCase();
  // While hydrating, a control the user already changed keeps its value and
  // pushes it into state once the page is live.
  let typed = false;
  if (H) {
    if (type === "checkbox" || type === "radio") typed = el.checked !== el.defaultChecked;
    else if (el.tagName === "SELECT") typed = Array.from(el.options).some((o) => o.selected !== o.defaultSelected);
    else typed = el.value !== el.defaultValue;
    if (typed) {
      const ev = el.tagName === "SELECT" || type === "checkbox" || type === "radio" ? "change" : "input";
      afterHydrate.push(() => el.dispatchEvent(new Event(ev)));
    }
  }
  const keep = () => { if (!typed) return false; typed = false; return true; };
  if (type === "checkbox") {
    effect(() => { const v = truth(s()); if (!keep()) el.checked = v; });
    el.addEventListener("change", () => s(el.checked));
    return;
  }
  if (type === "radio") {
    effect(() => { const v = el.value === String(s()); if (!keep()) el.checked = v; });
    el.addEventListener("change", () => { if (el.checked) s(el.value); });
    return;
  }
  const numeric = typeof s.peek() === "number";
  effect(() => {
    const v = s();
    const str = v == null ? "" : String(v);
    if (!keep() && el.value !== str) el.value = str;
  });
  el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => {
    if (numeric) {
      const t = el.value.trim();
      if (t !== "" && !Number.isNaN(Number(t))) s(Number(t));
    } else {
      s(el.value);
    }
  });
}

function setProp(el, k, v) {
  if (k.length > 2 && k[0] === "o" && k[1] === "n") {
    listen(el, k.slice(2).toLowerCase(), v);
  } else if (typeof v === "function") {
    effect(() => applyAttr(el, k, v()));
  } else {
    applyAttr(el, k, v);
  }
}

function toNodes(v, out) {
  if (v == null || v === false || v === true) return out;
  if (Array.isArray(v)) { for (const c of v) toNodes(c, out); return out; }
  if (typeof Node !== "undefined" && v instanceof Node) {
    if (v.nodeType === 11) out.push(...Array.from(v.childNodes)); else out.push(v);
    return out;
  }
  if (typeof v === "function") { out.push(...toNodes(dyn(v), [])); return out; }
  out.push(H ? claimText(String(v)) : document.createTextNode(String(v)));
  return out;
}

/** Static text: a string, or while hydrating the server's text node. */
export function t(s) { return H ? claimText(s) : s; }

function hasNode(v) {
  if (typeof Node !== "undefined" && v instanceof Node) return true;
  return Array.isArray(v) && v.some(hasNode);
}

/**
 * Create an element (or, while hydrating, adopt the server's). Function-valued
 * props and children are reactive. `children` is an array, or a function
 * returning one, called once after the element exists.
 */
export function h(tag, props, children) {
  const el = H ? claimEl(tag)
    : SVG_TAGS.has(tag) ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  // Bindings register first so state is current when on* handlers run;
  // <select> binds after its <option> children exist.
  const bound = props && props.$bind;
  if (bound && tag !== "select") { if (props.type) el.setAttribute("type", props.type); bind(el, bound); }
  if (props) {
    for (const k of Object.keys(props)) if (k !== "$bind" && k !== "$ref") setProp(el, k, props[k]);
    if (props.$ref) props.$ref(el);
  }
  if (H) claimChildren(el, children || []);
  else if (children) for (const n of toNodes(typeof children === "function" ? children() : children, [])) el.appendChild(n);
  if (bound && tag === "select") bind(el, bound);
  return el;
}

// Every dynamic region lives between two comment markers, so its content
// can change shape (text → nodes, branch swaps, nested lists) while the
// enclosing row or branch can still move/remove it as one unit.
function region(label) {
  if (H) return [null, place(document.createComment(label)), null];
  const frag = document.createDocumentFragment();
  const start = document.createComment(label);
  const end = document.createComment("/" + label);
  frag.appendChild(start);
  frag.appendChild(end);
  return [frag, start, end];
}

function rangeNodes(start, end) {
  const out = [];
  for (let n = start; n; n = n.nextSibling) { out.push(n); if (n === end) break; }
  return out;
}

function clearBetween(start, end) {
  let n = start.nextSibling;
  while (n && n !== end) { const next = n.nextSibling; n.parentNode.removeChild(n); n = next; }
}

function insertAll(before, nodes) {
  const parent = before.parentNode;
  for (const n of nodes) parent.insertBefore(n, before);
}

/** A reactive hole: text when `fn()` returns a value, nodes when it returns nodes. */
export function dyn(fn) {
  const hydrating = !!H;
  const [frag, start] = region("pw");
  let end = frag ? start.nextSibling : null;
  let textNode = null;
  effect(() => {
    const v = fn();
    if (!end) {
      if (hasNode(v)) toNodes(v, []); else textNode = claimText(text(v));
      end = place(document.createComment("/pw"));
      return;
    }
    if (hasNode(v)) {
      clearBetween(start, end);
      textNode = null;
      insertAll(end, toNodes(v, []));
      return;
    }
    const s = text(v);
    if (textNode) { if (textNode.data !== s) textNode.data = s; return; }
    clearBetween(start, end);
    textNode = document.createTextNode(s);
    insertAll(end, [textNode]);
  });
  return hydrating ? rangeNodes(start, end) : frag;
}

const _ids = new WeakMap();
let _idSeq = 0;
function keyOf(item) {
  if (item === null || (typeof item !== "object" && typeof item !== "function")) return item;
  if (Array.isArray(item)) return "t:" + item.map((x) => {
    if (x === null || typeof x !== "object") return typeof x + ":" + String(x);
    if (!_ids.has(x)) _ids.set(x, ++_idSeq);
    return "o:" + _ids.get(x);
  }).join("|");
  return item;
}

/** Keyed list: unchanged items keep their DOM; only added/removed rows render. */
export function list(getItems, renderRow) {
  const hydrating = !!H;
  const [frag, start] = region("pw-for");
  let end = frag ? start.nextSibling : null;
  let entries = [];
  onCleanup(() => { for (const e of entries) e.dispose(); entries = []; });
  effect(() => {
    const items = iter(getItems());
    untrack(() => {
      if (!end) {
        entries = items.map((item, i) => {
          const rs = place(document.createComment("pw-row"));
          const dispose = root((d) => { toNodes(renderRow(item, i), []); return d; });
          return { key: keyOf(item), start: rs, end: place(document.createComment("/pw-row")), dispose, frag: null };
        });
        end = place(document.createComment("/pw-for"));
        return;
      }
      const pool = new Map();
      for (const e of entries) {
        const q = pool.get(e.key);
        if (q) q.push(e); else pool.set(e.key, [e]);
      }
      const next = items.map((item, i) => {
        const key = keyOf(item);
        const q = pool.get(key);
        if (q && q.length) return q.shift();
        const [rf, rs, re] = region("pw-row");
        const dispose = root((d) => { const ns = toNodes(renderRow(item, i), []); for (const n of ns) rf.insertBefore(n, re); return d; });
        return { key, start: rs, end: re, dispose, frag: rf };
      });
      for (const q of pool.values()) {
        for (const e of q) { e.dispose(); for (const n of rangeNodes(e.start, e.end)) n.parentNode.removeChild(n); }
      }
      const parent = end.parentNode;
      let ref = end;
      for (let j = next.length - 1; j >= 0; j--) {
        const e = next[j];
        if (e.frag) { parent.insertBefore(e.frag, ref); e.frag = null; }
        else if (e.end.nextSibling !== ref) {
          const ns = rangeNodes(e.start, e.end);
          for (const n of ns) parent.insertBefore(n, ref);
        }
        ref = e.start;
      }
      entries = next;
    });
  });
  return hydrating ? rangeNodes(start, end) : frag;
}

/** Conditional region: renders `yes()` or `no()` and swaps only on change. */
export function when(test, yes, no) {
  const hydrating = !!H;
  const [frag, start] = region("pw-if");
  let end = frag ? start.nextSibling : null;
  let cur = -1;
  let disposeBranch = null;
  onCleanup(() => { if (disposeBranch) disposeBranch(); });
  effect(() => {
    const idx = truth(test()) ? 0 : 1;
    if (idx === cur) return;
    cur = idx;
    untrack(() => {
      if (disposeBranch) disposeBranch();
      if (end) clearBetween(start, end);
      const branch = idx === 0 ? yes : no;
      let nodes = [];
      disposeBranch = root((d) => { if (branch) nodes = toNodes(branch(), []); return d; });
      if (end) insertAll(end, nodes); else end = place(document.createComment("/pw-if"));
    });
  });
  return hydrating ? rangeNodes(start, end) : frag;
}

/** Run `fn` once the current page/component is in the document. */
export function onMount(fn) {
  const run = () => {
    try {
      const r = batch(fn);
      if (r && typeof r.then === "function") r.then(null, reportError);
    } catch (e) { reportError(e); }
  };
  queueMicrotask(run);
}

// Pages and layouts. Each is a reactive root mounted into its server-rendered
// host element ([data-pw-root]). Shared through `window` so a page module that
// loaded a different copy of this runtime still mounts with its own copy.
const G = typeof window !== "undefined"
  ? (window.__pyweb || (window.__pyweb = { mounts: new Map(), disposers: new Map(), nav: false }))
  : { mounts: new Map(), disposers: new Map(), nav: false };

/** Where a layout puts its page: adopts the server's element (and the page inside it). */
export function slot(name) {
  if (H) return claimEl("div");
  const existing = document.querySelector(`[data-pw-slot="${name}"]`);
  if (existing) return existing; // re-rendering the layout keeps the page
  const el = document.createElement("div");
  el.setAttribute("data-pw-slot", name);
  el.style.display = "contents";
  return el;
}

/** Render a page (or layout) into its server-rendered host element. */
export function mount(name, page) {
  const run = () => start(name, page);
  G.mounts.set(name, run);
  if (G.nav || typeof document === "undefined") return; // navigation mounts it after swapping HTML in
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
}

function start(name, page) {
  const host = document.querySelector(`[data-pw-root="${name}"]`) || document.body;
  const el = document.getElementById(host.hasAttribute("data-pw-layout") ? "pw-state-" + name : "pw-state");
  let state = {};
  if (el) { try { state = JSON.parse(el.textContent || "{}"); } catch (e) { reportError(e); } }
  const old = G.disposers.get(name);
  if (old) { G.disposers.delete(name); old(); }
  let mode = "rendered";
  let dispose = null;
  if (host.firstChild && host !== document.body && !host.hasAttribute("data-pw-no-hydrate")) {
    batchDepth++; // effects queued while adopting run once it is complete
    try {
      root((d) => {
        dispose = d;
        try { claimChildren(host, () => page(state)); } finally { H = null; }
      });
      mode = "hydrated";
    } catch (e) {
      H = null;
      afterHydrate = [];
      if (dispose) dispose();
      // A real error in page code surfaces again from the client render below.
      if (e instanceof Mismatch && typeof console !== "undefined") console.warn(e.message);
    } finally {
      if (--batchDepth === 0) flush();
    }
  }
  if (mode === "hydrated") {
    const typed = afterHydrate;
    afterHydrate = [];
    for (const f of typed) f();
  } else {
    root((d) => {
      dispose = d;
      const nodes = toNodes(page(state), []);
      host.replaceChildren(...nodes);
    });
  }
  G.disposers.set(name, dispose);
  host.setAttribute("data-pw-mode", mode);
  host.setAttribute("data-pw-ready", "");
  markActive();
  startRouter();
}

// ------------------------------------------------------- current links

/** `aria-current` for a link: "page" (this page), "true" (a parent section) or null. */
export function linkCurrent(href, path) {
  if (!href || /^(#|mailto:|tel:|javascript:)/i.test(href)) return null;
  let url;
  try { url = new URL(href, location.href); } catch (e) { return null; }
  if (url.origin !== location.origin) return null;
  const target = url.pathname;
  if (target === path) return "page";
  if (target !== "/" && path.startsWith(target.replace(/\/$/, "") + "/")) return "true";
  return null;
}

function markActive() {
  if (typeof document === "undefined") return;
  const here = location.pathname;
  for (const a of document.querySelectorAll("a[href]")) {
    const cur = a.getAttribute("aria-current");
    if (cur && cur !== "page" && cur !== "true") continue; // set by the app: leave it
    const v = linkCurrent(a.getAttribute("href"), here);
    if (v) { if (cur !== v) a.setAttribute("aria-current", v); } else if (cur) a.removeAttribute("aria-current");
  }
}

/** Adopt the server's element while hydrating, else create one (for add-on modules like markdown.js). */
export function element(tag) { return H ? claimEl(tag) : document.createElement(tag); }
export { setProp as prop };

// --------------------------------------------------- client navigation
// Same-origin links load the next page's server HTML with fetch, swap it in
// below the layouts both pages share, and mount the new page's module. Any
// surprise (not HTML, another app, new npm packages, an error) falls back to
// a normal page load.

const prefetched = new Map(); // url -> {at, promise}
let navSeq = 0;

function startRouter() {
  if (G.router || typeof window === "undefined" || !window.history || !history.pushState) return;
  const meta = document.querySelector('meta[name="pw-nav"]');
  if (meta && meta.content === "off") return;
  G.router = true;
  G.at = location.pathname + location.search;
  try { history.scrollRestoration = "manual"; } catch (e) { /* older browsers */ }
  const saved = history.state && history.state.pwScroll;
  if (saved) requestAnimationFrame(() => scrollTo(saved[0], saved[1]));
  document.addEventListener("click", onClick);
  for (const ev of ["mouseover", "focusin", "touchstart"]) document.addEventListener(ev, onIntent, { passive: true });
  window.addEventListener("popstate", onPop);
  window.addEventListener("pagehide", saveScroll);
}

function navTarget(a) {
  if (!a || !a.hasAttribute("href")) return null;
  const target = a.getAttribute("target");
  if ((target && target !== "_self") || a.hasAttribute("download") || a.hasAttribute("data-pw-reload")) return null;
  if (/\bexternal\b/.test(a.getAttribute("rel") || "")) return null;
  let url;
  try { url = new URL(a.getAttribute("href"), location.href); } catch (e) { return null; }
  if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)) return null;
  if (url.pathname.startsWith("/static/") || url.pathname.startsWith("/__pyweb/")) return null;
  if (url.pathname === location.pathname && url.search === location.search && url.hash) return null; // same-page anchor
  return url;
}

function onClick(ev) {
  if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
  const a = ev.target && ev.target.closest ? ev.target.closest("a") : null;
  const url = navTarget(a);
  if (!url) return;
  ev.preventDefault();
  navigate(url.href, { push: true });
}

function onIntent(ev) {
  const a = ev.target && ev.target.closest ? ev.target.closest("a") : null;
  if (!a || a.getAttribute("data-pw-prefetch") === "false") return;
  if (navigator.connection && navigator.connection.saveData) return;
  const url = navTarget(a);
  if (url) prefetch(url.href);
}

function onPop(ev) {
  const at = location.pathname + location.search;
  if (at === G.at) return; // only the hash changed
  const s = ev.state && ev.state.pwScroll;
  navigate(location.href, { push: false, scroll: s || [0, 0] });
}

function saveScroll() {
  try { history.replaceState({ ...(history.state || {}), pwScroll: [scrollX, scrollY] }, ""); } catch (e) { /* ignore */ }
}

function fetchPage(url) {
  return fetch(url, { headers: { Accept: "text/html", "X-PyWeb-Navigate": "1" }, credentials: "same-origin" })
    .then(async (res) => {
      const type = res.headers.get("Content-Type") || "";
      if (!type.includes("text/html")) throw new Error("not a page");
      return { url: res.url || url, html: await res.text() };
    });
}

/** Start loading `url` (a page link the user is about to click). */
export function prefetch(url) {
  const hit = prefetched.get(url);
  if (hit && Date.now() - hit.at < 15000) return hit.promise;
  const promise = fetchPage(url);
  promise.catch(() => prefetched.delete(url));
  prefetched.set(url, { at: Date.now(), promise });
  if (prefetched.size > 30) prefetched.delete(prefetched.keys().next().value);
  return promise;
}

function load(url) {
  const hit = prefetched.get(url);
  prefetched.delete(url);
  return hit && Date.now() - hit.at < 15000 ? hit.promise : fetchPage(url);
}

function importMap(doc) {
  const el = doc.querySelector('script[type="importmap"]');
  try { return el ? JSON.parse(el.textContent).imports || {} : {}; } catch (e) { return {}; }
}

function fullLoad(url, push) {
  if (push) location.assign(url); else location.reload();
}

/** Go to `url` without a full page load (falls back to one when it can't). */
export async function navigate(url, opts = {}) {
  const push = opts.push !== false;
  if (!G.router) return fullLoad(url, push); // client navigation is off (or no page here is interactive)
  const seq = ++navSeq;
  if (push) saveScroll();
  let res, doc;
  try {
    res = await load(url);
    if (seq !== navSeq) return;
    doc = new DOMParser().parseFromString(res.html, "text/html");
    const ok = doc.querySelector("body > [data-pw-root]") && document.querySelector("body > [data-pw-root]");
    const have = importMap(document);
    const need = importMap(doc);
    if (!ok || Object.keys(need).some((k) => have[k] !== need[k])) return fullLoad(url, push);
  } catch (e) {
    if (seq === navSeq) fullLoad(url, push);
    return;
  }
  const hash = new URL(url, location.href).hash;
  const final = new URL(res.url, location.href);
  if (!final.hash && hash) final.hash = hash;
  if (push) {
    if (final.href === location.href) history.replaceState({ pwScroll: null }, "", final.href);
    else history.pushState({ pwScroll: null }, "", final.href);
  }
  G.at = location.pathname + location.search;
  try {
    await swap(doc);
  } catch (e) {
    reportError(e);
    location.reload();
    return;
  }
  if (seq !== navSeq) return;
  markActive();
  const target = final.hash && document.getElementById(decodeURIComponent(final.hash.slice(1)));
  if (opts.scroll) scrollTo(opts.scroll[0], opts.scroll[1]);
  else if (target) target.scrollIntoView();
  else scrollTo(0, 0);
  announce(document.title);
  if (document.activeElement && !document.activeElement.isConnected) document.body.focus();
  window.dispatchEvent(new CustomEvent("pyweb:navigate", { detail: { url: location.href } }));
}

function layoutChain(doc) {
  return Array.from(doc.querySelectorAll("[data-pw-layout]"), (el) => el.getAttribute("data-pw-layout"));
}

async function swap(doc) {
  // Keep the layouts both pages share (same code, data and markup); replace what's below them.
  const before = layoutChain(document);
  const after = layoutChain(doc);
  let keep = 0;
  while (keep < before.length && keep < after.length && before[keep] === after[keep]) keep++;
  let oldRegion, newRegion;
  if (keep) {
    const name = after[keep - 1].split(":")[0];
    oldRegion = document.querySelector(`[data-pw-slot="${name}"]`);
    newRegion = doc.querySelector(`[data-pw-slot="${name}"]`);
  }
  const oldTop = document.querySelector("body > [data-pw-root]");
  const newTop = doc.querySelector("body > [data-pw-root]");
  const gone = keep ? oldRegion.querySelectorAll("[data-pw-root]") : [oldTop, ...oldTop.querySelectorAll("[data-pw-root]")];
  for (const el of gone) {
    const name = el.getAttribute("data-pw-root");
    const d = G.disposers.get(name);
    if (d) { G.disposers.delete(name); try { d(); } catch (e) { reportError(e); } }
  }
  const fresh = keep ? Array.from(newRegion.querySelectorAll("[data-pw-root]")) : [newTop, ...newTop.querySelectorAll("[data-pw-root]")];
  // Head: title, description/social tags, and any new stylesheets.
  document.title = doc.title;
  for (const el of document.head.querySelectorAll("[data-pw-head]")) el.remove();
  for (const el of doc.head.querySelectorAll("[data-pw-head]")) document.head.appendChild(document.importNode(el, true));
  const sheets = new Set(Array.from(document.querySelectorAll('link[rel="stylesheet"]'), (l) => l.href));
  const loading = [];
  for (const l of doc.head.querySelectorAll('link[rel="stylesheet"]')) {
    if (sheets.has(new URL(l.getAttribute("href"), location.href).href)) continue;
    const copy = document.importNode(l, true);
    loading.push(new Promise((ok) => { copy.onload = copy.onerror = ok; }));
    document.head.appendChild(copy);
  }
  // Modules (and their state) for the new roots, in document order.
  const modules = [];
  for (const el of fresh) {
    const name = el.getAttribute("data-pw-root");
    const stateId = el.hasAttribute("data-pw-layout") ? "pw-state-" + name : "pw-state";
    const state = doc.getElementById(stateId);
    const script = state && state.nextElementSibling;
    if (!script || script.localName !== "script" || !script.getAttribute("src")) continue;
    modules.push({ name, stateId, json: state.textContent, src: new URL(script.getAttribute("src"), location.href).href });
  }
  G.nav = true;
  try {
    await Promise.all([...modules.map((m) => import(m.src)), ...loading]);
  } finally {
    G.nav = false;
  }
  if (keep) oldRegion.replaceChildren(...Array.from(newRegion.childNodes, (n) => document.importNode(n, true)));
  else oldTop.replaceWith(document.importNode(newTop, true));
  for (const m of modules) {
    let el = document.getElementById(m.stateId);
    if (!el) {
      el = document.createElement("script");
      el.type = "application/json";
      el.id = m.stateId;
      document.body.appendChild(el);
    }
    el.textContent = m.json;
    const run = G.mounts.get(m.name);
    if (run) run();
  }
}

function announce(text) {
  let el = document.getElementById("pw-announcer");
  if (!el) {
    el = document.createElement("div");
    el.id = "pw-announcer";
    el.setAttribute("aria-live", "assertive");
    el.setAttribute("role", "status");
    el.style.cssText = "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap";
    document.body.appendChild(el);
  }
  el.textContent = text;
}

// ----------------------------------------------------- Python semantics

export class PyError extends Error {
  constructor(type, message) {
    super(message);
    this.name = type;
    this.type = type;
  }
}
function err(type, msg) { return new PyError(type, msg); }

class KW { constructor(o) { Object.assign(this, o); } }
function kw(o) { return new KW(o); }
function splitKw(args) {
  if (args.length && args[args.length - 1] instanceof KW) return [args.slice(0, -1), args[args.length - 1]];
  return [args, {}];
}

function isDict(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v) && !(v instanceof Set)
    && !(v instanceof Map) && !(typeof Node !== "undefined" && v instanceof Node);
}

export function truth(v) {
  if (v == null || v === false || v === 0 || v === "" || Number.isNaN(v)) return false;
  if (Array.isArray(v)) return v.length > 0;
  if (v instanceof Set || v instanceof Map) return v.size > 0;
  if (isDict(v) && Object.getPrototypeOf(v) === Object.prototype) return Object.keys(v).length > 0;
  return true;
}

/** `async for` source: async iterables as-is, ordinary iterables one item at a time. */
function aiter(v) {
  if (v && typeof v[Symbol.asyncIterator] === "function") return v;
  return iter(v);
}

function iter(v) {
  if (Array.isArray(v)) return v;
  if (typeof v === "string") return Array.from(v);
  if (v instanceof Set) return Array.from(v);
  if (v instanceof Map) return Array.from(v.keys());
  if (v && typeof v[Symbol.iterator] === "function") return Array.from(v);
  if (isDict(v)) return Object.keys(v);
  throw err("TypeError", `'${typeName(v)}' object is not iterable`);
}

function typeName(v) {
  if (v == null) return "NoneType";
  if (typeof v === "string") return "str";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
  if (Array.isArray(v)) return "list";
  if (v instanceof Set) return "set";
  if (typeof v === "function") return "function";
  return "dict";
}

function repr(v) {
  if (v == null) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  if (typeof v === "string") {
    const q = v.includes("'") && !v.includes('"') ? '"' : "'";
    return q + v.replace(/\\/g, "\\\\").replace(new RegExp(q, "g"), "\\" + q).replace(/\n/g, "\\n") + q;
  }
  if (typeof v === "number") return numStr(v);
  if (Array.isArray(v)) return "[" + v.map(repr).join(", ") + "]";
  if (v instanceof Set) return v.size ? "{" + Array.from(v).map(repr).join(", ") + "}" : "set()";
  if (isDict(v)) return "{" + Object.keys(v).map((k) => repr(k) + ": " + repr(v[k])).join(", ") + "}";
  return String(v);
}

function numStr(n) {
  if (Number.isNaN(n)) return "nan";
  if (n === Infinity) return "inf";
  if (n === -Infinity) return "-inf";
  return String(n);
}

function str(v) {
  if (typeof v === "string") return v;
  if (v instanceof Error) return v.message;
  if (typeof v === "number") return numStr(v);
  return repr(v);
}

/** Text for a `{expr}` hole: like `str()`, except `None` renders nothing. */
export function text(v) { return v == null ? "" : str(v); }

function eq(a, b) {
  if (a === b) return true;
  if (a == null || b == null) return a == b;
  if (typeof a === "number" && typeof b === "number") return a === b;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => eq(x, b[i]));
  if (a instanceof Set && b instanceof Set) return a.size === b.size && Array.from(a).every((x) => b.has(x));
  if (isDict(a) && isDict(b)) {
    const ka = Object.keys(a); const kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && eq(a[k], b[k]));
  }
  return false;
}

function contains(container, x) {
  if (typeof container === "string") {
    if (typeof x !== "string") throw err("TypeError", "'in <string>' requires string as left operand");
    return container.includes(x);
  }
  if (Array.isArray(container)) return container.some((y) => eq(x, y));
  if (container instanceof Set || container instanceof Map) return container.has(x);
  if (isDict(container)) return Object.prototype.hasOwnProperty.call(container, x);
  throw err("TypeError", `argument of type '${typeName(container)}' is not iterable`);
}

function cmp(a, b) {
  if (Array.isArray(a) && Array.isArray(b)) {
    for (let i = 0; i < Math.min(a.length, b.length); i++) {
      const c = cmp(a[i], b[i]);
      if (c) return c;
    }
    return a.length - b.length;
  }
  return a < b ? -1 : a > b ? 1 : 0;
}

function len(v) {
  if (typeof v === "string" || Array.isArray(v)) return v.length;
  if (v instanceof Set || v instanceof Map) return v.size;
  if (isDict(v)) return Object.keys(v).length;
  throw err("TypeError", `object of type '${typeName(v)}' has no len()`);
}

function normIndex(seq, i) {
  if (typeof i !== "number" || !Number.isInteger(i)) throw err("TypeError", "indices must be integers");
  const n = i < 0 ? seq.length + i : i;
  if (n < 0 || n >= seq.length) throw err("IndexError", `${typeName(seq)} index out of range`);
  return n;
}

function at(obj, key) {
  if (obj == null) throw err("TypeError", "'NoneType' object is not subscriptable");
  if (Array.isArray(obj) || typeof obj === "string") return obj[normIndex(obj, key)];
  if (obj instanceof Map) {
    if (!obj.has(key)) throw err("KeyError", repr(key));
    return obj.get(key);
  }
  if (!Object.prototype.hasOwnProperty.call(obj, key)) throw err("KeyError", repr(key));
  return obj[key];
}

function slice(seq, lo, hi, step) {
  const n = seq.length;
  step = step == null ? 1 : step;
  if (step === 0) throw err("ValueError", "slice step cannot be zero");
  const clamp = (i, dflt) => {
    if (i == null) return dflt;
    if (i < 0) i += n;
    return step > 0 ? Math.min(Math.max(i, 0), n) : Math.min(Math.max(i, -1), n - 1);
  };
  const start = clamp(lo, step > 0 ? 0 : n - 1);
  const stop = clamp(hi, step > 0 ? n : -1);
  const out = [];
  for (let i = start; step > 0 ? i < stop : i > stop; i += step) out.push(seq[i]);
  return typeof seq === "string" ? out.join("") : out;
}

function add(a, b) {
  if (typeof a === "number" && typeof b === "number") return a + b;
  if (Array.isArray(a) && Array.isArray(b)) return a.concat(b);
  const ta = typeof a; const tb = typeof b;
  if ((ta === "string") !== (tb === "string") || ta === "object" || tb === "object") {
    throw err("TypeError", `unsupported operand type(s) for +: '${typeName(a)}' and '${typeName(b)}'`);
  }
  return a + b;
}

function mul(a, b) {
  if (typeof a === "number" && typeof b === "number") return a * b;
  if (typeof b === "string" || Array.isArray(b)) [a, b] = [b, a];
  if (typeof a === "string") return b > 0 ? a.repeat(b) : "";
  if (Array.isArray(a)) { let out = []; for (let i = 0; i < b; i++) out = out.concat(a); return out; }
  throw err("TypeError", `can't multiply '${typeName(a)}' by '${typeName(b)}'`);
}

function mod(a, b) {
  if (typeof a === "string") {
    const args = Array.isArray(b) ? b.slice() : [b];
    return a.replace(/%([sdrf%]|\.\d+f)/g, (m, t) => {
      if (t === "%") return "%";
      const v = args.shift();
      if (t === "s") return str(v);
      if (t === "r") return repr(v);
      if (t === "d") return String(Math.trunc(v));
      if (t === "f") return Number(v).toFixed(6);
      return Number(v).toFixed(Number(t.slice(1, -1)));
    });
  }
  if (b === 0) throw err("ZeroDivisionError", "integer modulo by zero");
  return ((a % b) + b) % b;
}

function floordiv(a, b) {
  if (b === 0) throw err("ZeroDivisionError", "integer division by zero");
  return Math.floor(a / b);
}

function div(a, b) {
  if (b === 0) throw err("ZeroDivisionError", "division by zero");
  return a / b;
}

function int(v, base) {
  if (typeof v === "number") return Math.trunc(v);
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "string") {
    const s = v.trim().replace(/_/g, "");
    const b = base || 10;
    const ok = b === 10 ? /^[+-]?\d+$/ : /^[+-]?[0-9a-z]+$/i;
    const n = parseInt(s, b);
    if (!ok.test(s) || Number.isNaN(n)) throw err("ValueError", `invalid literal for int() with base ${b}: ${repr(v)}`);
    return n;
  }
  throw err("TypeError", `int() argument must be a string or a number, not '${typeName(v)}'`);
}

function float(v) {
  if (typeof v === "number") return v;
  const n = Number(String(v).trim());
  if (String(v).trim() === "" || Number.isNaN(n)) throw err("ValueError", `could not convert string to float: ${repr(v)}`);
  return n;
}

function round(x, nd) {
  if (nd == null) {
    const f = Math.floor(x); const d = x - f;
    if (d === 0.5) return f % 2 === 0 ? f : f + 1;
    return Math.round(x);
  }
  const m = 10 ** nd;
  return Math.round(x * m) / m;
}

function range(a, b, step) {
  if (b == null) { b = a; a = 0; }
  step = step == null ? 1 : step;
  if (step === 0) throw err("ValueError", "range() arg 3 must not be zero");
  const out = [];
  for (let i = a; step > 0 ? i < b : i > b; i += step) out.push(i);
  return out;
}

function keyed(kwargs) {
  const key = kwargs.key || ((x) => x);
  return (x, y) => cmp(key(x), key(y));
}

function sorted(it, ...rest) {
  const [, kws] = splitKw(rest);
  const out = iter(it).slice().sort(keyed(kws));
  if (kws.reverse) out.reverse();
  return out;
}

function extreme(sign, args) {
  const [pos, kws] = splitKw(args);
  const items = pos.length === 1 ? iter(pos[0]) : pos;
  if (!items.length) {
    if ("default" in kws) return kws.default;
    throw err("ValueError", "arg is an empty sequence");
  }
  const c = keyed(kws);
  return items.reduce((best, x) => (sign * c(x, best) > 0 ? x : best));
}

function format(v, spec) {
  if (!spec) return str(v);
  const m = /^(?:(.)?([<>^]))?([+\- ])?(0)?(\d+)?(,)?(?:\.(\d+))?([sdfe%g])?$/.exec(spec);
  if (!m) return str(v);
  const [, fill = " ", align, sign, zero, width, comma, prec, type] = m;
  let s;
  if (type === "%") s = (v * 100).toFixed(prec == null ? 6 : +prec) + "%";
  else if (type === "f") s = Number(v).toFixed(prec == null ? 6 : +prec);
  else if (type === "e") s = Number(v).toExponential(prec == null ? 6 : +prec);
  else if (type === "d") s = String(Math.trunc(v));
  else if (prec != null && typeof v === "number") s = Number(v).toFixed(+prec);
  else if (prec != null) s = str(v).slice(0, +prec);
  else s = str(v);
  if (comma && typeof v === "number") {
    const [ip, fp] = s.split(".");
    s = ip.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (fp != null ? "." + fp : "");
  }
  if (sign === "+" && typeof v === "number" && v >= 0) s = "+" + s;
  if (width) {
    const w = +width;
    const f = zero ? "0" : fill;
    const al = align || (typeof v === "number" ? ">" : "<");
    if (s.length < w) {
      const pad = w - s.length;
      if (al === "<") s = s + f.repeat(pad);
      else if (al === "^") s = f.repeat(Math.floor(pad / 2)) + s + f.repeat(Math.ceil(pad / 2));
      else s = f.repeat(pad) + s;
    }
  }
  return s;
}

function strip(s, chars, left, right) {
  if (chars == null) {
    if (left && right) return s.trim();
    return left ? s.replace(/^\s+/, "") : s.replace(/\s+$/, "");
  }
  let a = 0; let b = s.length;
  while (left && a < b && chars.includes(s[a])) a++;
  while (right && b > a && chars.includes(s[b - 1])) b--;
  return s.slice(a, b);
}

const STR_METHODS = {
  upper: (s) => s.toUpperCase(),
  lower: (s) => s.toLowerCase(),
  strip: (s, c) => strip(s, c, true, true),
  lstrip: (s, c) => strip(s, c, true, false),
  rstrip: (s, c) => strip(s, c, false, true),
  split: (s, sep, max) => {
    if (sep == null) {
      const parts = s.trim().split(/\s+/).filter(Boolean);
      return max == null || max < 0 ? parts : parts.slice(0, max).concat(parts.length > max ? [parts.slice(max).join(" ")] : []);
    }
    const parts = s.split(sep);
    if (max == null || max < 0 || parts.length <= max + 1) return parts;
    return parts.slice(0, max).concat([parts.slice(max).join(sep)]);
  },
  splitlines: (s) => s.split(/\r?\n/).filter((x, i, a) => i < a.length - 1 || x !== ""),
  join: (s, items) => iter(items).map((x) => {
    if (typeof x !== "string") throw err("TypeError", `sequence item: expected str instance, ${typeName(x)} found`);
    return x;
  }).join(s),
  replace: (s, a, b, count) => {
    if (count == null || count < 0) return s.split(a).join(b);
    let out = s;
    let i = 0; let from = 0;
    while (i < count) {
      const at2 = out.indexOf(a, from);
      if (at2 < 0) break;
      out = out.slice(0, at2) + b + out.slice(at2 + a.length);
      from = at2 + b.length;
      i++;
    }
    return out;
  },
  startswith: (s, p) => (Array.isArray(p) ? p.some((x) => s.startsWith(x)) : s.startsWith(p)),
  endswith: (s, p) => (Array.isArray(p) ? p.some((x) => s.endsWith(x)) : s.endsWith(p)),
  find: (s, x) => s.indexOf(x),
  rfind: (s, x) => s.lastIndexOf(x),
  index: (s, x) => { const i = s.indexOf(x); if (i < 0) throw err("ValueError", "substring not found"); return i; },
  count: (s, x) => (x === "" ? s.length + 1 : s.split(x).length - 1),
  title: (s) => s.toLowerCase().replace(/(^|[^a-z0-9])([a-z])/g, (m, a, b) => a + b.toUpperCase()),
  capitalize: (s) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase(),
  isdigit: (s) => /^\d+$/.test(s),
  isnumeric: (s) => /^\d+$/.test(s),
  isalpha: (s) => /^[A-Za-z]+$/.test(s),
  isalnum: (s) => /^[A-Za-z0-9]+$/.test(s),
  isspace: (s) => /^\s+$/.test(s),
  isupper: (s) => /[A-Z]/.test(s) && s === s.toUpperCase(),
  islower: (s) => /[a-z]/.test(s) && s === s.toLowerCase(),
  zfill: (s, w) => (s.length >= w ? s : (s[0] === "-" ? "-" + s.slice(1).padStart(w - 1, "0") : s.padStart(w, "0"))),
  ljust: (s, w, f = " ") => s.padEnd(w, f),
  rjust: (s, w, f = " ") => s.padStart(w, f),
  center: (s, w, f = " ") => format(s, `${f}^${w}`),
  format: (s, ...args) => {
    const [pos, kws] = splitKw(args);
    let auto = 0;
    return s.replace(/\{\{|\}\}|\{([^{}:]*)(?::([^{}]*))?\}/g, (m, name, spec) => {
      if (m === "{{") return "{";
      if (m === "}}") return "}";
      const v = name === "" || name == null ? pos[auto++] : /^\d+$/.test(name) ? pos[+name] : kws[name];
      return format(v, spec);
    });
  },
};

const LIST_METHODS = {
  append: (a, x) => { a.push(x); },
  extend: (a, xs) => { a.push(...iter(xs)); },
  insert: (a, i, x) => { a.splice(i < 0 ? Math.max(0, a.length + i) : i, 0, x); },
  pop: (a, i) => {
    if (!a.length) throw err("IndexError", "pop from empty list");
    return a.splice(normIndex(a, i == null ? -1 : i), 1)[0];
  },
  remove: (a, x) => {
    const i = a.findIndex((y) => eq(x, y));
    if (i < 0) throw err("ValueError", "list.remove(x): x not in list");
    a.splice(i, 1);
  },
  index: (a, x) => {
    const i = a.findIndex((y) => eq(x, y));
    if (i < 0) throw err("ValueError", `${repr(x)} is not in list`);
    return i;
  },
  count: (a, x) => a.filter((y) => eq(x, y)).length,
  clear: (a) => { a.length = 0; },
  sort: (a, ...rest) => {
    const [, kws] = splitKw(rest);
    a.sort(keyed(kws));
    if (kws.reverse) a.reverse();
  },
  reverse: (a) => { a.reverse(); },
  copy: (a) => a.slice(),
};

const DICT_METHODS = {
  get: (d, k, dflt = null) => (Object.prototype.hasOwnProperty.call(d, k) ? d[k] : dflt),
  keys: (d) => Object.keys(d),
  values: (d) => Object.values(d),
  items: (d) => Object.entries(d),
  pop: (d, k, ...dflt) => {
    if (Object.prototype.hasOwnProperty.call(d, k)) { const v = d[k]; delete d[k]; return v; }
    if (dflt.length) return dflt[0];
    throw err("KeyError", repr(k));
  },
  update: (d, o) => { Object.assign(d, o instanceof KW ? { ...o } : o); },
  setdefault: (d, k, v = null) => { if (!Object.prototype.hasOwnProperty.call(d, k)) d[k] = v; return d[k]; },
  copy: (d) => ({ ...d }),
  clear: (d) => { for (const k of Object.keys(d)) delete d[k]; },
};

const SET_METHODS = {
  add: (s, x) => { s.add(x); },
  discard: (s, x) => { s.delete(x); },
  remove: (s, x) => { if (!s.delete(x)) throw err("KeyError", repr(x)); },
  clear: (s) => { s.clear(); },
  copy: (s) => new Set(s),
};

/** Call a Python method on a JS value (`obj.method(*args)`). */
function m(obj, name, ...args) {
  let table = null;
  if (typeof obj === "string") table = STR_METHODS;
  else if (Array.isArray(obj)) table = LIST_METHODS;
  else if (obj instanceof Set) table = SET_METHODS;
  else if (isDict(obj) && typeof obj[name] !== "function") table = DICT_METHODS;
  if (table && table[name]) {
    const r = table[name](obj, ...args);
    return r === undefined ? null : r;
  }
  if (obj != null && typeof obj[name] === "function") {
    const [pos] = splitKw(args);
    return obj[name](...pos);
  }
  throw err("AttributeError", `'${typeName(obj)}' object has no attribute '${name}'`);
}

// Objects from JavaScript libraries (class instances, DOM nodes) are
// changed in place; plain data is copied so keyed lists see new items.
function foreign(v) {
  if (!v || typeof v !== "object" || Array.isArray(v) || v instanceof Set || v instanceof Map) return false;
  const proto = Object.getPrototypeOf(v);
  return proto !== Object.prototype && proto !== null;
}

function clone(v) {
  if (Array.isArray(v)) return v.slice();
  if (v instanceof Set) return new Set(v);
  if (v instanceof Map) return new Map(v);
  if (foreign(v)) return v;
  if (v && typeof v === "object") return { ...v };
  return v;
}

function commit(s, prev, next) {
  if (next === prev) s.touch(); else s(next);
}

function isClass(f) {
  return typeof f === "function" && /^class[\s{]/.test(Function.prototype.toString.call(f));
}

/** Call a JavaScript library function or class (`npm(...)` exports) from Python. */
function call(f, args, options) {
  if (options !== undefined) args = [...args, options];
  if (typeof f !== "function") throw err("TypeError", `'${typeName(f)}' object is not callable`);
  return isClass(f) ? new f(...args) : f(...args);
}

function callm(obj, name, args, options) {
  const f = obj[name];
  if (options !== undefined) args = [...args, options];
  if (typeof f !== "function") throw err("AttributeError", `'${typeName(obj)}' object has no attribute '${name}'`);
  return isClass(f) ? new f(...args) : f.apply(obj, args);
}

function walk(rootVal, path) {
  let cur = rootVal;
  for (const k of path) {
    const idx = Array.isArray(cur) ? normIndex(cur, k) : k;
    if (Array.isArray(cur) || isDict(cur)) {
      if (!Array.isArray(cur) && !foreign(cur) && !Object.prototype.hasOwnProperty.call(cur, idx)) throw err("KeyError", repr(k));
      cur[idx] = clone(cur[idx]);
      cur = cur[idx];
    } else {
      throw err("TypeError", `'${typeName(cur)}' object is not subscriptable`);
    }
  }
  return cur;
}

/** Mutate state copy-on-write: `todos.append(x)` → new list, then notify. */
function mut(s, path, fn) {
  const prev = s.peek();
  const next = clone(prev);
  const r = fn(walk(next, path));
  commit(s, prev, next);
  return r === undefined ? null : r;
}

function setitem(obj, k, v) {
  if (Array.isArray(obj)) obj[normIndex(obj, k)] = v;
  else if (obj instanceof Map) obj.set(k, v);
  else obj[k] = v;
}

function delitem(obj, k) {
  if (Array.isArray(obj)) obj.splice(normIndex(obj, k), 1);
  else if (obj instanceof Map) obj.delete(k);
  else {
    if (!Object.prototype.hasOwnProperty.call(obj, k)) throw err("KeyError", repr(k));
    delete obj[k];
  }
}

/** `state[a][b] = v` with structural sharing so keyed rows see a new item. */
function setp(s, path, v) {
  if (!path.length) { s(v); return; }
  const prev = s.peek();
  const next = clone(prev);
  setitem(walk(next, path.slice(0, -1)), path[path.length - 1], v);
  commit(s, prev, next);
}

function getp(v, path) {
  for (const k of path) v = at(v, k);
  return v;
}

export const py = {
  go: (url) => navigate(String(url)),
  aiter,
  kw, truth, iter, str, repr, text, eq, contains, len, at, slice, add, mul, mod, div, floordiv,
  int, float, round, range, sorted, format, m, mut, setp, getp, setitem, delitem, cmp, call, callm,
  bool: (v) => truth(v),
  abs: (v) => Math.abs(v),
  min: (...a) => extreme(-1, a),
  max: (...a) => extreme(1, a),
  sum: (it, start = 0) => iter(it).reduce((acc, x) => add(acc, x), start),
  any: (it) => iter(it).some(truth),
  all: (it) => iter(it).every(truth),
  list: (it) => (it == null ? [] : iter(it).slice()),
  tuple: (it) => (it == null ? [] : iter(it).slice()),
  set: (it) => new Set(it == null ? [] : iter(it)),
  dict: (it, ...rest) => {
    const [, kws] = splitKw(rest);
    let base = {};
    if (it instanceof KW) base = { ...it };
    else if (isDict(it)) base = { ...it };
    else if (it != null) base = Object.fromEntries(iter(it));
    return Object.assign(base, kws);
  },
  reversed: (it) => iter(it).slice().reverse(),
  enumerate: (it, start = 0) => iter(it).map((x, i) => [i + start, x]),
  zip: (...its) => {
    const arrs = its.map(iter);
    const n = arrs.length ? Math.min(...arrs.map((a) => a.length)) : 0;
    return range(n).map((i) => arrs.map((a) => a[i]));
  },
  chr: (n) => String.fromCodePoint(n),
  ord: (c) => c.codePointAt(0),
  isinstance: (v, t) => (Array.isArray(t) ? t : [t]).some((x) => x === typeName(v)
    || (x === "float" && typeof v === "number" && !Number.isInteger(v)) || (x === "int" && typeof v === "boolean")
    || (x === "tuple" && Array.isArray(v))),
  print: (...a) => console.log(...splitKw(a)[0].map(str)),
  error: (type, msg) => err(type, msg == null ? "" : str(msg)),
  and: (a, f) => (truth(a) ? f() : a),
  or: (a, f) => (truth(a) ? a : f()),
  exc: (e, types) => types.includes("Exception") || types.includes("BaseException")
    || types.includes((e && (e.type || e.name)) || "Error"),
  delp: (s, path) => {
    const prev = s.peek();
    const next = clone(prev);
    delitem(walk(next, path.slice(0, -1)), path[path.length - 1]);
    commit(s, prev, next);
  },
};

// --------------------------------------------------------------------- RPC

// Typed RPC error: err.code is one of unauthenticated/forbidden/
// validation_error/not_found/rate_limited/timeout/conflict/csrf_failed/
// internal. err.status is the HTTP status; err.retryAfter on 429.
export class RPCError extends Error {
  constructor(name, code, message, status, details, retryAfter) {
    super(message || `${name}: ${code}`);
    this.name = "RPCError";
    this.type = "RPCError";
    this.rpc = name;
    this.code = code;
    this.status = status;
    this.details = details || {};
    if (retryAfter) this.retryAfter = retryAfter;
  }
}

let _qid = 0;
let _csrf = null;
export function setCsrfToken(token) { _csrf = token; }
export function getCsrfToken() {
  if (_csrf) return _csrf;
  if (typeof document === "undefined") return null;
  const m2 = document.cookie.match(/(?:^|;\s*)pyweb_csrf=([^;]+)/);
  return m2 ? decodeURIComponent(m2[1]) : null;
}

const RETRYABLE = new Set([502, 503, 504]);
function _sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

export async function rpc(name, args = {}, opts = {}) {
  const attempts = Math.max(1, opts.retries ?? opts.attempts ?? 1);
  const timeout = opts.timeout || 0;
  const external = opts.signal || null;
  let lastErr = null;
  for (let attempt = 0; attempt < attempts; attempt++) {
    const ctrl = new AbortController();
    const onAbort = () => ctrl.abort();
    if (external) {
      if (external.aborted) { const e = new DOMException("aborted", "AbortError"); e.rpc = name; throw e; }
      external.addEventListener("abort", onAbort, { once: true });
    }
    const timer = timeout ? setTimeout(() => ctrl.abort(), timeout) : null;
    try {
      const headers = {
        "Content-Type": "application/json",
        "X-PyWeb-Trace": String(Date.now()) + "-" + (++_qid),
        ...(opts.traceparent ? { traceparent: opts.traceparent } : {}),
      };
      const csrf = opts.csrf ?? getCsrfToken();
      if (csrf) headers["X-CSRF-Token"] = csrf;
      const res = await fetch(`/__pyweb/rpc/${encodeURIComponent(name)}`, {
        method: "POST",
        headers,
        body: JSON.stringify({ args }),
        signal: ctrl.signal,
        credentials: "same-origin",
      });
      const ctype = res.headers.get("Content-Type") || "";
      // Streaming RPC: application/x-ndjson, one {"chunk"} per line.
      if (res.ok && ctype.includes("x-ndjson")) {
        const body = await res.text();
        const chunks = [];
        for (const line of body.split("\n")) {
          if (!line.trim()) continue;
          try {
            const obj = JSON.parse(line);
            if ("chunk" in obj) { chunks.push(obj.chunk); if (typeof opts.onChunk === "function") opts.onChunk(obj.chunk); }
          } catch { /* skip malformed line */ }
        }
        return chunks;
      }
      let data = null;
      try { data = await res.json(); } catch { data = null; }
      if (!res.ok || (data && data.error)) {
        const e = (data && data.error) || {};
        const code = typeof e === "object" ? (e.code || `http_${res.status}`) : "internal";
        const msg = typeof e === "object" ? (e.message || `RPC ${name} failed: ${res.status}`) : String(e);
        const retryAfter = res.headers.get("Retry-After");
        const rerr = new RPCError(name, code, msg, res.status, (typeof e === "object" && e.details) || {}, retryAfter ? parseInt(retryAfter, 10) : null);
        const retryable = res.status === 429 || RETRYABLE.has(res.status);
        if (attempt + 1 < attempts && retryable) {
          lastErr = rerr;
          let delay = Math.min(100 * 2 ** attempt, 2000);
          if (res.status === 429 && retryAfter) delay = parseInt(retryAfter, 10) * 1000 || delay;
          await _sleep(delay + Math.random() * 100);
          continue;
        }
        throw rerr;
      }
      return data ? data.result : null;
    } catch (e) {
      if (e && e.name === "AbortError") { e.rpc = e.rpc || name; throw e; }
      if (e instanceof RPCError) throw e;
      lastErr = e;
      if (attempt + 1 < attempts && opts.retryNetwork !== false) {
        await _sleep(Math.min(100 * 2 ** attempt, 2000) + Math.random() * 100);
        continue;
      }
      throw e;
    } finally {
      if (timer) clearTimeout(timer);
      if (external) external.removeEventListener("abort", onAbort);
    }
  }
  throw lastErr;
}

/**
 * A call to a server function that `yield`s. Nothing is sent until it's
 * iterated (`for await`); each yielded value arrives as soon as the server
 * produces it. `cancel()` (or leaving the loop early) aborts the request,
 * which closes the generator on the server.
 */
export class RpcStream {
  constructor(name, args) {
    this.rpc = name;
    this.args = args;
    this.cancelled = false;
    this.done = false;
    this.ctrl = new AbortController();
  }

  cancel() {
    if (this.done) return;
    this.cancelled = true;
    this.ctrl.abort();
  }

  async *[Symbol.asyncIterator]() {
    const name = this.rpc;
    if (this.cancelled) return;
    const headers = { "Content-Type": "application/json", Accept: "application/x-ndjson" };
    const csrf = getCsrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
    let res;
    try {
      res = await fetch(`/__pyweb/rpc/${encodeURIComponent(name)}`, {
        method: "POST", headers, body: JSON.stringify({ args: this.args }),
        signal: this.ctrl.signal, credentials: "same-origin",
      });
    } catch (e) {
      if (this.cancelled) return;
      throw e;
    }
    const ctype = res.headers.get("Content-Type") || "";
    if (!res.ok || !ctype.includes("x-ndjson")) {
      let data = null;
      try { data = await res.json(); } catch { data = null; }
      const e = (data && data.error) || {};
      throw new RPCError(name, e.code || `http_${res.status}`, e.message || `RPC ${name} failed: ${res.status}`,
                         res.status, e.details || {});
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      for (;;) {
        let part;
        try { part = await reader.read(); } catch (e) { if (this.cancelled) return; throw e; }
        if (part.done) break;
        buf += decoder.decode(part.value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl);
          buf = buf.slice(nl + 1);
          if (!line.trim()) continue;
          const msg = JSON.parse(line);
          if ("chunk" in msg) yield msg.chunk;
          else if (msg.error) {
            this.done = true;
            throw new RPCError(name, msg.error.code || "internal", msg.error.message || "stream failed", 200,
                               msg.error.details || {});
          } else if (msg.done) { this.done = true; return; }
        }
      }
      if (!this.cancelled) throw new RPCError(name, "unavailable", "the connection closed before the stream ended", 0, {});
    } finally {
      if (!this.done) { this.done = true; this.ctrl.abort(); } // left the loop early: stop the server too
    }
  }
}

rpc.stream = (name, args) => new RpcStream(name, args);

// Live updates. `feed` is a signed token made on the server with
// channel(name). Messages arrive over Server-Sent Events (the browser
// reconnects and resumes on its own); if the stream can't be opened the
// page polls instead. Each message runs `onMsg` like an event handler.
// Returns an unsubscribe function; the subscription also ends with the
// page or component that made it.
export function subscribe(feed, onMsg, opts = {}) {
  let stopped = false;
  let lastId = opts.lastId || 0;
  let src = null;
  const q = `feed=${encodeURIComponent(feed)}` + (opts.live ? `&live=${encodeURIComponent(opts.live)}` : "");
  const deliver = (raw) => {
    let v = raw;
    if (typeof raw === "string") { try { v = JSON.parse(raw); } catch { /* plain text */ } }
    try {
      const r = batch(() => onMsg(v));
      if (r && typeof r.then === "function") r.then(null, reportError);
    } catch (e) { reportError(e); }
  };
  const stop = () => { stopped = true; if (src) src.close(); };
  onCleanup(stop);
  async function poll() {
    while (!stopped) {
      try {
        const res = await fetch(`/__pyweb/poll?${q}&since=${lastId}`, { credentials: "same-origin" });
        if (res.status === 403 || res.status === 400) { reportError(new Error(`pyweb: feed rejected (${res.status}); reload the page`)); return; }
        if (!res.ok) throw new Error(`poll ${res.status}`);
        const data = await res.json();
        for (const msg of data.messages || []) { lastId = msg.id; if (!stopped) deliver(msg.data); }
      } catch { /* retry below */ }
      await _sleep(opts.interval || 2500);
    }
  }
  if (typeof EventSource !== "undefined" && !opts.poll) {
    src = new EventSource(`/__pyweb/events?${q}`);
    src.onmessage = (e) => { if (e.lastEventId) lastId = Number(e.lastEventId) || lastId; deliver(e.data); };
    // CONNECTING means the browser is reconnecting by itself; CLOSED means it gave up.
    src.onerror = () => { if (src.readyState === 2 && !stopped) { src = null; poll(); } };
  } else {
    poll();
  }
  return stop;
}

/** Keep page variable `sig` in step with a live query (`meta` comes from the server's live()). */
export function live(sig, meta) {
  if (!meta || !meta.feed) return;
  let version = meta.version;
  subscribe(meta.feed, (msg) => {
    if (msg && msg.version !== version) { version = msg.version; sig(msg.rows); }
  }, { live: meta.spec });
}

// Optimistic mutation: apply local patch, run server call, reconcile/rollback.
export async function mutate({ apply, server, reconcile, rollback }) {
  const undo = apply ? apply() : null;
  try {
    const result = await server();
    if (reconcile) reconcile(result);
    return result;
  } catch (e) {
    if (rollback) rollback(undo, e); else if (typeof undo === "function") undo();
    throw e;
  }
}
