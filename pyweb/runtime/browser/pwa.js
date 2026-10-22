// PyWeb installable-app support: registers the service worker and wires push buttons.
//
// The worker's URL goes through a Trusted Types policy ("pyweb-sw") that accepts only /sw.js,
// so the strict CSP (require-trusted-types-for 'script') holds.
//
// Push buttons need no code:
//   <button data-pw-push="subscribe">Turn on notifications</button>
//   <button data-pw-push="unsubscribe">Turn off</button>
// <html data-pw-push="..."> tells CSS the state: unsupported, default (not asked yet),
// denied, subscribed or unsubscribed. Each change also fires a "pyweb:push" event on document.

const SW_URL = "/sw.js";
let policy = null;
if (typeof trustedTypes !== "undefined" && trustedTypes.createPolicy) {
  policy = trustedTypes.createPolicy("pyweb-sw", {
    createScriptURL: (url) => {
      if (url !== SW_URL) throw new TypeError("pyweb-sw: only " + SW_URL + " can be a service worker");
      return url;
    },
  });
}

const supported = typeof navigator !== "undefined" && "serviceWorker" in navigator;
const pushSupported = supported && typeof window !== "undefined" && "PushManager" in window && "Notification" in window;

function standalone() {
  return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    window.navigator.standalone === true;
}

// The server can tell an installed app from a browser tab (pyweb.platform.pwa).
try {
  document.cookie = "pyweb_display=" + (standalone() ? "standalone" : "browser") + "; path=/; SameSite=Lax";
} catch (_) { /* cookies off */ }

const ready = supported
  ? navigator.serviceWorker.register(policy ? policy.createScriptURL(SW_URL) : SW_URL, { scope: "/", updateViaCache: "none" })
    .catch((err) => { console.warn("PyWeb: service worker not registered:", err); return null; })
  : Promise.resolve(null);

function csrfHeaders() {
  const m = document.cookie.match(/(?:^|;\s*)pyweb_csrf=([^;]+)/);
  const headers = { "Content-Type": "application/json" };
  if (m) headers["X-CSRF-Token"] = decodeURIComponent(m[1]);
  return headers;
}

function decode(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

function setState(state) {
  document.documentElement.dataset.pwPush = state;
  document.dispatchEvent(new CustomEvent("pyweb:push", { detail: { state } }));
  return state;
}

async function current() {
  if (!pushSupported) return setState("unsupported");
  if (Notification.permission === "denied") return setState("denied");
  const reg = await ready;
  const sub = reg && await reg.pushManager.getSubscription();
  if (sub) return setState("subscribed");
  return setState(Notification.permission === "default" ? "default" : "unsubscribed");
}

async function subscribe() {
  if (!pushSupported) return setState("unsupported");
  const permission = await Notification.requestPermission();       // only ever on a click
  if (permission !== "granted") return setState(permission === "denied" ? "denied" : "default");
  await ready;
  const reg = await navigator.serviceWorker.ready;
  const info = await (await fetch("/__pyweb/push/key", { credentials: "same-origin" })).json();
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decode(info.key) });
  const r = await fetch("/__pyweb/push/subscribe", {
    method: "POST", credentials: "same-origin", headers: csrfHeaders(), body: JSON.stringify(sub.toJSON()),
  });
  if (!r.ok) {
    await sub.unsubscribe();
    throw new Error("PyWeb: the server refused the subscription (" + r.status + ")");
  }
  return setState("subscribed");
}

async function unsubscribe() {
  if (!pushSupported) return setState("unsupported");
  const reg = await ready;
  const sub = reg && await reg.pushManager.getSubscription();
  if (sub) {
    await fetch("/__pyweb/push/unsubscribe", {
      method: "POST", credentials: "same-origin", headers: csrfHeaders(), body: JSON.stringify({ endpoint: sub.endpoint }),
    }).catch(() => {});
    await sub.unsubscribe();
  }
  return setState("unsubscribed");
}

async function forgetPages() {
  const reg = await ready;
  if (reg && reg.active) reg.active.postMessage("pyweb:forget-pages");
}

document.addEventListener("click", (event) => {
  const el = event.target instanceof Element ? event.target.closest("[data-pw-push]") : null;
  if (!el) return;
  event.preventDefault();
  const action = el.getAttribute("data-pw-push");
  const run = action === "unsubscribe" ? unsubscribe : action === "toggle"
    ? () => (document.documentElement.dataset.pwPush === "subscribed" ? unsubscribe() : subscribe())
    : subscribe;
  el.setAttribute("aria-busy", "true");
  run().catch((err) => console.warn(err)).finally(() => el.removeAttribute("aria-busy"));
});

if (supported) current().catch(() => {});

globalThis.pywebPush = { subscribe, unsubscribe, state: current, supported: pushSupported, ready, forgetPages };
