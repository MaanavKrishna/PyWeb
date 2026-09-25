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
  const el = root.getElementById ? root : document;
  const target = el.querySelector ? (el.querySelector(`[pw-bind="${CSS.escape(code)}"]`) || el) : document;
  const render = () => {
    let v; try { v = thunk(); } catch { return; }
    if (target === el) {
      _walkText(el, () => {});
    } else if (target) target.textContent = v == null ? "" : String(v);
  };
  _effects.add(render); render();
}

export function bind_attr() {}

export function bind_input(root, name) {
  const scope = window.__pyweb_scope || {};
  const s = scope[name];
  if (!s) return;
  root.querySelectorAll("input,textarea,select").forEach((input) => {
    input.value = s.peek ? s.peek() ?? "" : "";
    input.addEventListener("input", () => s(input.value));
    if (s.subscribe) s.subscribe((v) => { if (input.value !== String(v ?? "")) input.value = v ?? ""; });
  });
}

export function on(root, event, handlerName) {
  const scope = window.__pyweb_scope || {};
  const fn = scope[handlerName];
  if (typeof fn !== "function") return;
  root.addEventListener(event === "click" ? "click" : event, (e) => {
    const t = e.target.closest("button,a,input,[data-on]");
    if (t || event !== "click") fn(e);
  });
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
