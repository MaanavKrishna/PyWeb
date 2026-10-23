// PyWeb UI kit loader: wakes up components marked data-pw-ui="<kind>" when they appear
// (server-rendered, created in the browser, or brought in by client navigation), loading each
// kind's module once, only on pages that use it. Removed components are cleaned up. Plain
// components (Card, Alert, Table, Breadcrumb) carry a kind with no module: it only marks them as
// the owner of their parts, so a Card's "content" inside Tabs isn't taken for a tab panel.

const KINDS = new Set(["dialog", "popover", "tooltip", "hover-card", "menu", "context-menu", "menubar",
  "select", "combobox", "command", "toaster", "tabs", "accordion", "collapsible", "switch", "slider",
  "radio-group", "toggle-group", "toggle", "checkbox", "scroll-area", "resizable", "navigation-menu",
  "sidebar", "calendar", "carousel", "data-table", "otp", "theme-toggle"]);
const modules = new Map();
const live = new WeakMap();

function load(kind) {
  if (!modules.has(kind)) modules.set(kind, import(`./${kind}.js`).then((m) => m.default));
  return modules.get(kind);
}

async function wake(el) {
  if (live.has(el)) return;
  const kind = el.dataset.pwUi;
  if (!KINDS.has(kind)) return;
  live.set(el, null);
  try {
    const setup = await load(kind);
    if (!el.isConnected) return;
    const cleanup = setup(el);
    live.set(el, typeof cleanup === "function" ? cleanup : null);
    el.dataset.pwUiReady = "";
  } catch (err) {
    console.error(`PyWeb UI: ${kind} failed to start`, err);
  }
}

function sleep(el) {
  const cleanup = live.get(el);
  live.delete(el);
  if (cleanup) { try { cleanup(); } catch (err) { console.error(err); } }
}

function scan(node) {
  if (node.nodeType !== 1) return;
  if (node.matches("[data-pw-ui]")) wake(node);
  for (const el of node.querySelectorAll("[data-pw-ui]")) wake(el);
}

function unscan(node) {
  if (node.nodeType !== 1) return;
  if (live.has(node) && !node.isConnected) sleep(node);
  for (const el of node.querySelectorAll("[data-pw-ui]")) if (live.has(el) && !el.isConnected) sleep(el);
}

scan(document.documentElement);
new MutationObserver((records) => {
  for (const r of records) {
    for (const n of r.removedNodes) unscan(n);
    for (const n of r.addedNodes) scan(n);
  }
}).observe(document.documentElement, { childList: true, subtree: true });

// Toasts sent by the server: a header on server function answers, a cookie after navigations.
async function showToasts(raw) {
  let list;
  try { list = JSON.parse(decodeURIComponent(raw)); } catch (_) { return; }
  if (!Array.isArray(list) || !list.length) return;
  const { toast } = await import("./toaster.js");
  for (const t of list) toast(t);
}
const m = document.cookie.match(/(?:^|;\s*)pyweb_toast=([^;]+)/);
if (m) {
  document.cookie = "pyweb_toast=; Max-Age=0; path=/; SameSite=Lax";
  showToasts(m[1]);
}
if (typeof window.fetch === "function") {
  const fetchFn = window.fetch;
  window.fetch = async (...args) => {
    const res = await fetchFn(...args);
    try {
      const t = res.headers.get("X-PyWeb-Toast");
      if (t) showToasts(t);
    } catch (_) { /* opaque response */ }
    return res;
  };
}

globalThis.pywebUI = { wake, load, toast: (opts) => import("./toaster.js").then((m) => m.toast(opts)) };
