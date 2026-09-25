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
