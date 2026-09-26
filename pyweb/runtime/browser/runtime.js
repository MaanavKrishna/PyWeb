// PyWeb browser runtime (~3KB): signals, computed, bindings, rpc, mutate.
let _qid = 0;
const _effects = new Set();

export function sig(value) {
  let v = value;
  const subs = new Set();
  function fn(next) {
    if (arguments.length === 0) return v;
    v = next;
    subs.forEach((s) => s(v));
    _effects.forEach((e) => e());
    return v;
  }
  fn.subscribe = (s) => { subs.add(s); return () => subs.delete(s); };
  fn.peek = () => v;
  return fn;
}

export function computed(fn) {
  let cached; let dirty = true;
  const c = () => { if (dirty) { cached = fn(); dirty = false; } return cached; };
  _effects.add(() => { dirty = true; });
  return c;
}

export function effect(fn) { _effects.add(fn); fn(); return () => _effects.delete(fn); }

export function resource(loader) {
  const r = { pending: false, error: null, result: null };
  r.load = async () => {
    r.pending = true;
    try { r.result = await loader(); } catch (e) { r.error = e; }
    r.pending = false; return r.result;
  };
  return r;
}

function _walkText(root, cb) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (w.nextNode()) nodes.push(w.currentNode);
  nodes.forEach(cb);
}

// Bind {expr} text: codegen emits one bind_text per dynamic expression with a
// thunk reading current signal values; we re-evaluate on any signal change.
export function bind_text(root, code, thunk) {
  const el = root.querySelector ? root : document;
  let targets;
  try {
    targets = el.querySelectorAll(`[pw-bind="${CSS.escape(code)}"]`);
  } catch { targets = []; }
  if (!targets || targets.length === 0) return;
  const render = () => {
    let v; try { v = thunk(); } catch { return; }
    if (typeof v === "function") { try { v = v(); } catch { return; } }
    const s = v == null ? "" : String(v);
    targets.forEach((t) => { if (t.textContent !== s) t.textContent = s; });
  };
  _effects.add(render); render();
}

export function bind_attr() {}

// --- Keyed live list: appending one item creates exactly one row; existing
// rows keep their DOM nodes. Ported from Track A (compiler-runtime).
function _liveRowEls(row) {
  if (!row) return [];
  if (row.els) return row.els;
  if (row.el) return [row.el];
  return [];
}

export function liveList(anchor, getItems, renderRow, keyFn) {
  keyFn = keyFn || ((item, i) => `i:${i}`);
  const rows = new Map();
  let order = [];
  function makeRow(item, i, key) {
    const row = renderRow(item, i, key) || { els: [] };
    row.item = item;
    row.i = i;
    return row;
  }
  function killRow(key) {
    const row = rows.get(key);
    if (!row) return;
    rows.delete(key);
    if (row.dispose) { try { row.dispose(); } catch { /* noop */ } }
    _liveRowEls(row).forEach((e) => { if (e.parentNode) e.parentNode.removeChild(e); });
  }
  function sync() {
    const items = getItems() || [];
    const parent = anchor.parentNode;
    const seen = {};
    const nextOrder = [];
    for (let i = 0; i < items.length; i++) {
      const key = String(keyFn(items[i], i));
      seen[key] = true;
      nextOrder.push(key);
      const row = rows.get(key);
      if (!row) {
        rows.set(key, makeRow(items[i], i, key));
      } else if (row.item !== items[i]) {
        killRow(key);
        rows.set(key, makeRow(items[i], i, key));
      }
    }
    const victims = [];
    rows.forEach((_row, key) => { if (!seen[key]) victims.push(key); });
    victims.forEach(killRow);
    order = nextOrder;
    let ref = anchor;
    for (let j = order.length - 1; j >= 0; j--) {
      const els = _liveRowEls(rows.get(order[j]));
      for (let q = els.length - 1; q >= 0; q--) {
        if (els[q].nextSibling !== ref) parent.insertBefore(els[q], ref);
        ref = els[q];
      }
    }
  }
  const dispose = effect(sync);
  return { sync, dispose };
}

// --- Live conditional: swaps branch DOM in place.
export function liveIf(anchor, pick, branches) {
  const state = { idx: -1, nodes: [] };
  function sync() {
    const idx = pick();
    if (idx === state.idx) return;
    state.nodes.forEach((n) => { if (n.parentNode) n.parentNode.removeChild(n); });
    state.nodes = [];
    state.idx = idx;
    if (idx < 0 || idx >= branches.length) return;
    const parent = anchor.parentNode;
    (branches[idx]() || []).forEach((n) => {
      parent.insertBefore(n, anchor);
      state.nodes.push(n);
    });
  }
  const dispose = effect(sync);
  return { sync, dispose };
}

// --- SSR handoff: replace <!--pw:hid--> ... <!--/pw:hid--> with a live anchor.
function _findComment(root, text) {
  const stack = [root];
  while (stack.length) {
    const n = stack.pop();
    const kids = (n && n.childNodes) || [];
    for (let i = 0; i < kids.length; i++) {
      const k = kids[i];
      if (k.nodeType === 8 && k.nodeValue === text) return k;
      if (k.childNodes && k.childNodes.length) stack.push(k);
    }
  }
  return null;
}

export function takeover(parent, hid) {
  const doc = parent.ownerDocument || document;
  const anchor = doc.createComment("pw-live");
  const start = _findComment(parent, `pw:${hid}`);
  if (!start) {
    parent.appendChild(anchor);
    return anchor;
  }
  const host = start.parentNode || parent;
  host.insertBefore(anchor, start);
  let cur = start;
  while (cur) {
    const next = cur.nextSibling;
    host.removeChild(cur);
    if (cur.nodeType === 8 && cur.nodeValue === `/pw:${hid}`) break;
    cur = next;
  }
  return anchor;
}

export function dynText(node, fn) {
  const sync = () => { if (node) node.textContent = fn() == null ? "" : String(fn()); };
  return effect(sync);
}

// SSR emits data-pw-id on interactive elements; resolve handlers/inputs by id.
const _marks = new Map();
export function _mark_el(root, id, line) { return _resolve(root, id); }

function _resolve(root, id) {
  if (_marks.has(id)) return _marks.get(id);
  const scope = root && root.querySelector ? root : (typeof document !== "undefined" ? document : null);
  if (!scope) return null;
  const el = scope.querySelector(`[data-pw-id="${CSS.escape(id)}"]`);
  if (el) _marks.set(id, el);
  return el;
}

export function bind_input(root, name, id) {
  const scope = window.__pyweb_scope || {};
  const s = scope[name];
  if (!s) return;
  const input = id ? _resolve(root, id) : null;
  const targets = input ? [input]
    : Array.from((root === document ? document : root).querySelectorAll("input,textarea,select"));
  targets.forEach((input) => {
    input.value = s.peek ? s.peek() ?? "" : "";
    input.addEventListener("input", () => s(input.value));
    if (s.subscribe) s.subscribe((v) => { if (input.value !== String(v ?? "")) input.value = v ?? ""; });
  });
}

export function on(root, event, handler, id) {
  const scope = window.__pyweb_scope || {};
  const fn = typeof handler === "function" ? handler : scope[handler];
  if (typeof fn !== "function") return;
  const el = id ? _resolve(root, id) : null;
  if (el) { el.addEventListener(event, (e) => fn(e)); return; }
  root.addEventListener(event, (e) => fn(e));
}

export async function rpc(name, args = {}, opts = {}) {
  const ctrl = new AbortController();
  const timer = opts.timeout ? setTimeout(() => ctrl.abort(), opts.timeout) : null;
  try {
    const res = await fetch(`/__pyweb/rpc/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-PyWeb-Trace": String(Date.now()) + "-" + (++_qid) },
      body: JSON.stringify({ args, trace: _qid }),
      signal: ctrl.signal,
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`RPC ${name} failed: ${res.status}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    return data.result;
  } finally { if (timer) clearTimeout(timer); }
}

// Realtime: SSE at /__pyweb/events?channel=NAME with poll fallback.
// subscribe(channel, onMsg, {lastId}) returns an unsubscribe function.
export function subscribe(channel, onMsg, opts = {}) {
  let stopped = false;
  let lastId = opts.lastId || 0;
  if (typeof EventSource !== "undefined" && !opts.poll) {
    const src = new EventSource(`/__pyweb/events?channel=${encodeURIComponent(channel)}`);
    src.addEventListener(channel, (e) => {
      if (e.lastEventId) lastId = e.lastEventId;
      try { onMsg(JSON.parse(e.data)); } catch { onMsg(e.data); }
    });
    src.onerror = () => { src.close(); if (!stopped) poll(); };
    return () => { stopped = true; src.close(); };
  }
  async function poll() {
    while (!stopped) {
      try {
        const res = await fetch(`/__pyweb/poll?channel=${encodeURIComponent(channel)}&since=${lastId}`);
        if (!res.ok) throw new Error(`poll ${res.status}`);
        const data = await res.json();
        for (const m of data.messages || []) { lastId = m.id; onMsg(m.data); }
      } catch { /* retry below */ }
      await new Promise((r) => setTimeout(r, opts.interval || 2500));
    }
  }
  poll();
  return () => { stopped = true; };
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
