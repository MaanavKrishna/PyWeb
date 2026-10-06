// Live updates for PyWeb pages: subscribe(), live queries and presence (join).
// Loaded only by pages that use them.

import { batch, onCleanup, reportError } from "./runtime.js";

// `feed` is a signed token made on the server with
// channel(name). Every subscription on the page shares one WebSocket
// (/__pyweb/ws); it reconnects with jittered backoff and resumes after the
// last message it saw. Where WebSockets don't get through (some proxies,
// ASGI servers without them) each subscription uses Server-Sent Events,
// and polling as a last resort. Each message runs `onMsg` like an event
// handler. Returns an unsubscribe function; the subscription also ends with
// the page or component that made it.
const WS = { sock: null, subs: new Map(), next: 1, open: false, tries: 0, broken: false, timer: 0 };

function wsSend(msg) { if (WS.open) WS.sock.send(JSON.stringify(msg)); }

function wsSub(sub) {
  if (sub.presence) wsSend({ t: "join", s: sub.id, feed: sub.feed, info: sub.info });
  else wsSend({ t: "sub", s: sub.id, feed: sub.feed, live: sub.live || undefined, since: sub.lastId });
}

function wsConnect() {
  if (WS.sock || WS.broken || !WS.subs.size) return;
  let sock;
  try { sock = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/__pyweb/ws"); }
  catch { WS.broken = true; for (const sub of WS.subs.values()) sub.fallback(); return; }
  WS.sock = sock;
  sock.onopen = () => { WS.open = true; WS.tries = 0; for (const sub of WS.subs.values()) wsSub(sub); };
  sock.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch { return; }
    const sub = WS.subs.get(m.s);
    if (!sub) return;
    if (m.t === "m") { sub.lastId = m.i; sub.deliver(m.d); }
    else if (m.t === "snap") { if (sub.onSnap) sub.onSnap(m.d); }
    else if (m.t === "r") { if (sub.resync) sub.resync(); }
    else if (m.t === "e") {
      WS.subs.delete(m.s);
      reportError(new Error(`pyweb: feed rejected (${m.status}): ${m.error}`));
    }
  };
  sock.onclose = (e) => {
    const wasOpen = WS.open;
    WS.sock = null; WS.open = false;
    if (!wasOpen && ++WS.tries >= 3) {                  // never got through: use event streams instead
      WS.broken = true;
      for (const sub of WS.subs.values()) sub.fallback();
      return;
    }
    if (e.code === 4001) { for (const sub of WS.subs.values()) sub.ended = true; }
    const delay = Math.min(30000, 500 * 2 ** Math.min(WS.tries, 6)) * (0.5 + Math.random() / 2);
    if (wasOpen) WS.tries = Math.max(WS.tries, 1);
    clearTimeout(WS.timer);
    WS.timer = setTimeout(wsConnect, delay);
  };
}

export function subscribe(feed, onMsg, opts = {}) {
  let stopped = false;
  let src = null;
  const sub = { id: WS.next++, feed, live: opts.live, lastId: opts.lastId || 0, onSnap: opts.onSnap, resync: null };
  const q = () => `feed=${encodeURIComponent(feed)}` + (opts.live ? `&live=${encodeURIComponent(opts.live)}` : "");
  sub.deliver = (raw) => {
    if (stopped) return;
    let v = raw;
    if (typeof raw === "string") { try { v = JSON.parse(raw); } catch { /* plain text */ } }
    try {
      const r = batch(() => onMsg(v));
      if (r && typeof r.then === "function") r.then(null, reportError);
    } catch (e) { reportError(e); }
  };
  // Ask for a live query's whole result (after a missed change).
  sub.resync = () => {
    if (WS.open && WS.subs.has(sub.id)) { wsSend({ t: "snap", s: sub.id }); return; }
    fetch(`/__pyweb/live?${q()}`, { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null)).then((d) => { if (d && !stopped && sub.onSnap) sub.onSnap(d); }, () => {});
  };
  if (opts.handle) opts.handle.resync = () => sub.resync();
  const stop = () => {
    stopped = true;
    if (src) src.close();
    if (WS.subs.delete(sub.id)) wsSend({ t: "unsub", s: sub.id });
  };
  onCleanup(stop);
  async function poll() {
    while (!stopped) {
      try {
        const res = await fetch(`/__pyweb/poll?${q()}&since=${sub.lastId}`, { credentials: "same-origin" });
        if (res.status === 403 || res.status === 400) { reportError(new Error(`pyweb: feed rejected (${res.status}); reload the page`)); return; }
        if (!res.ok) throw new Error(`poll ${res.status}`);
        const data = await res.json();
        for (const msg of data.messages || []) { sub.lastId = msg.id; sub.deliver(msg.data); }
      } catch { /* retry below */ }
      await new Promise((r) => setTimeout(r, opts.interval || 2500));
    }
  }
  sub.fallback = () => {
    WS.subs.delete(sub.id);
    if (stopped) return;
    if (typeof EventSource !== "undefined" && !opts.poll) {
      src = new EventSource(`/__pyweb/events?${q()}`);
      src.onmessage = (e) => { if (e.lastEventId) sub.lastId = Number(e.lastEventId) || sub.lastId; sub.deliver(e.data); };
      // CONNECTING means the browser is reconnecting by itself; CLOSED means it gave up.
      src.onerror = () => { if (src.readyState === 2 && !stopped) { src = null; poll(); } };
    } else {
      poll();
    }
  };
  if (opts.poll) poll();
  else if (typeof WebSocket !== "undefined" && !WS.broken) {
    WS.subs.set(sub.id, sub);
    if (WS.open) wsSub(sub); else wsConnect();
  } else sub.fallback();
  return stop;
}

/** Apply a live-query patch: unchanged rows stay the same objects, so their DOM is kept. */
export function applyPatch(rows, ops, key) {
  let out = rows.slice();
  for (const op of ops) {
    if (op[0] === "d") out = out.filter((r) => r[key] !== op[1]);
    else if (op[0] === "u") out = out.map((r) => (r[key] === op[1][key] ? op[1] : r));
    else if (op[0] === "i") out.splice(op[1], 0, op[2]);
    else if (op[0] === "o") { const by = new Map(out.map((r) => [r[key], r])); out = op[1].map((k) => by.get(k)); }
  }
  return out;
}

/** Keep page variable `sig` in step with a live query (`meta` comes from the server's live()). */
export function live(sig, meta) {
  if (!meta || !meta.feed) return;
  let version = meta.version;
  const set = (d) => { if (d.version !== version) { version = d.version; sig(d.rows); } };
  const handle = {};
  subscribe(meta.feed, (msg) => {
    if (!msg || msg.version === version) return;
    if (msg.rows) set(msg);
    else if (msg.ops && msg.prev === version) { version = msg.version; sig(applyPatch(sig.peek(), msg.ops, msg.key)); }
    else handle.resync();                             // missed a change: fetch the whole result
  }, { live: meta.spec, onSnap: set, handle });
}

/** Join a presence room (token from presence() on the server). `onMembers(list)` runs whenever
 * the room changes; `onCast(data, from)` for messages others cast. Returns `{cast, leave}`. */
export function join(room, info, onMembers, onCast) {
  let stopped = false;
  const sub = { id: WS.next++, feed: room, lastId: 0, presence: true, info: info || {} };
  sub.deliver = (d) => {
    if (stopped || !d) return;
    try {
      if (d.members && onMembers) batch(() => onMembers(d.members));
      else if ("cast" in d && onCast) batch(() => onCast(d.cast, d.from));
    } catch (e) { reportError(e); }
  };
  sub.fallback = () => { WS.subs.delete(sub.id); if (onMembers && !stopped) onMembers([]); };
  const leave = () => {
    if (stopped) return;
    stopped = true;
    if (WS.subs.delete(sub.id)) wsSend({ t: "leave", s: sub.id });
  };
  onCleanup(leave);
  if (typeof WebSocket !== "undefined" && !WS.broken) {
    WS.subs.set(sub.id, sub);
    if (WS.open) wsSub(sub); else wsConnect();
  } else sub.fallback();
  return { cast: (data) => { if (!stopped) wsSend({ t: "cast", s: sub.id, d: data }); }, leave };
}
