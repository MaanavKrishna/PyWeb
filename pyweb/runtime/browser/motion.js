// PyWeb motion: animations written as attributes (initial=, animate=, exit=, while_hover=,
// layout, drag...), in the spirit of motion.dev. The compiler turns them into one
// data-pw-motion attribute (JSON); this module animates every element that has one.
//
// Values are animated with the Web Animations API, on the compositor where the browser can
// (opacity and the translate/scale/rotate properties). Springs, tweens and inertia are computed
// here (motion-math.js), so an animation interrupted halfway passes its velocity on.
// Exit animations: the runtime asks G.exit before removing a node. Layout animations and drag
// load their own modules only on pages that use them.
import { inertia, spring, springFor, tween } from "./motion-math.js";

const ATTR = "data-pw-motion", SEL = `[${ATTR}]`;
// Shared with the runtime (this may load first: same shape as the runtime makes it).
const G = window.__pyweb || (window.__pyweb = { mounts: new Map(), disposers: new Map(), navState: new Map(), nav: false });
export const CFG = { feel: "snappy", speed: 1, reduced: "auto", presets: {} };
try {
  const meta = document.querySelector('meta[name="pyweb-motion"]');
  if (meta) Object.assign(CFG, JSON.parse(meta.content));
} catch (_) { /* defaults */ }
const mq = matchMedia("(prefers-reduced-motion: reduce)");
export const reduced = () => CFG.reduced === "always" || (CFG.reduced === "auto" && mq.matches);

const FEEL = {
  snappy: { stiffness: 500, damping: 32, duration: 0.25 },
  gentle: { stiffness: 140, damping: 22, duration: 0.45 },
  bouncy: { stiffness: 400, damping: 14, duration: 0.35 },
};

// ------------------------------------------------------------------ values

const PROP = { x: "translate", y: "translate", z: "translate", scale: "scale", scale_x: "scale", scale_y: "scale",
  rotate: "rotate", rotate_x: "transform", rotate_y: "transform", lx: "transform", ly: "transform", lsx: "transform",
  lsy: "transform", opacity: "opacity", blur: "filter" };
const DEFAULT = { x: 0, y: 0, z: 0, scale: 1, scale_x: 1, scale_y: 1, rotate: 0, rotate_x: 0, rotate_y: 0, lx: 0,
  ly: 0, lsx: 1, lsy: 1, blur: 0 };
const UNIT = { x: "px", y: "px", z: "px", lx: "px", ly: "px", rotate: "deg", rotate_x: "deg", rotate_y: "deg", blur: "px" };
const PHYSICAL = new Set(["x", "y", "z", "scale", "scale_x", "scale_y", "rotate", "rotate_x", "rotate_y", "lx", "ly", "lsx", "lsy"]);
const norm = (k) => String(k).replace(/-/g, "_").replace(/[A-Z]/g, (m) => "_" + m.toLowerCase());
const camel = (k) => k.replace(/_([a-z])/g, (_m, c) => c.toUpperCase());
const propOf = (k) => PROP[k] || camel(k);
export const normAll = (o) => (o && typeof o === "object" && !Array.isArray(o)
  ? Object.fromEntries(Object.entries(o).map(([k, v]) => [norm(k), v])) : o);

function parse(v, k) {
  if (typeof v === "number") return { n: v, u: UNIT[k] || "" };
  const m = typeof v === "string" && /^(-?[\d.]+)([a-z%]*)$/i.exec(v.trim());
  return m ? { n: parseFloat(m[1]), u: m[2] || UNIT[k] || "" } : v;
}
const isNum = (v) => v && typeof v === "object" && typeof v.n === "number";
const fmt = (v) => (isNum(v) ? `${Math.round(v.n * 1000) / 1000}${v.u}` : String(v));

// ------------------------------------------------------------------ element state

const S = new WeakMap();
export function state(el) {
  let s = S.get(el);
  if (!s) S.set(el, (s = { v: {}, run: {}, anims: {}, active: new Set(), spec: {}, home: {}, touched: new Set() }));
  return s;
}
export const now = () => performance.now() / 1000;

function settled(el, s, k) {
  if (k in s.v) return s.v[k];
  if (k in DEFAULT) return { n: DEFAULT[k], u: UNIT[k] || "" };
  const cs = getComputedStyle(el)[camel(k)];
  return parse(cs, k);
}
export function current(el, s, k, t = now()) {
  const r = s.run[k];
  return r ? { n: r.g.value(t - r.t0), u: r.u } : settled(el, s, k);
}

function css(el, s, prop, t) {
  const g = (k) => current(el, s, k, t);
  const n = (k) => g(k).n;
  if (prop === "translate") return `${fmt(g("x"))} ${fmt(g("y"))}` + ("z" in s.v || s.run.z ? ` ${fmt(g("z"))}` : "");
  if (prop === "scale") return `${n("scale") * n("scale_x")} ${n("scale") * n("scale_y")}`;
  if (prop === "rotate") return fmt(g("rotate"));
  if (prop === "filter") return `blur(${fmt(g("blur"))})`;
  if (prop === "transform") {
    let out = `translate(${n("lx")}px, ${n("ly")}px) scale(${n("lsx")}, ${n("lsy")})`;
    if (n("rotate_x") || n("rotate_y")) out += ` perspective(800px) rotateX(${n("rotate_x")}deg) rotateY(${n("rotate_y")}deg)`;
    return out;
  }
  return fmt(g(norm(prop)));
}

function keysOf(s, prop) { return Object.keys(s.run).filter((k) => propOf(k) === prop); }

/** Write `values` at once (no animation). */
export function set(el, values) {
  const s = state(el);
  const props = new Set();
  for (let [k, v] of Object.entries(values)) {
    k = norm(k);
    if (k === "transition" || v == null) continue;
    if (Array.isArray(v)) v = v[v.length - 1];
    delete s.run[k];
    s.v[k] = parse(resolveAuto(el, k, v), k);
    props.add(propOf(k));
  }
  for (const p of props) {
    if (s.anims[p]) { s.anims[p].cancel(); delete s.anims[p]; }
    for (const k of keysOf(s, p)) { s.v[k] = current(el, s, k); delete s.run[k]; }
    el.style[p] = css(el, s, p);
  }
}

function resolveAuto(el, k, v) {
  if (v !== "auto" || (k !== "height" && k !== "width")) return v;
  const old = el.style[k];
  el.style[k] = "auto";
  const px = el.getBoundingClientRect()[k];
  el.style[k] = old;
  return px;
}

// ------------------------------------------------------------------ animating

function transitionFor(tr, k) {
  const base = normAll(tr) || {};
  const own = base[k] && typeof base[k] === "object" && !Array.isArray(base[k]) ? normAll(base[k]) : null;
  return own ? { ...base, ...own } : base;
}

function instant(t, k) {
  return CFG.feel === "none" || t.type === "instant" || t.duration === 0 || (reduced() && PHYSICAL.has(k));
}

function generator(t, values, v0, k) {
  const last = values[values.length - 1];
  if (instant(t, k)) return { duration: 0, value: () => last, velocity: () => 0 };
  const feel = FEEL[CFG.feel] || FEEL.snappy;
  const type = t.type || (values.length > 2 ? "keyframes" : PHYSICAL.has(k) ? "spring" : "tween");
  let g;
  if (type === "spring") {
    const p = t.duration && !t.stiffness ? springFor(t.duration, t.bounce ?? 0.25)
      : { stiffness: t.stiffness ?? feel.stiffness, damping: t.damping ?? feel.damping, mass: t.mass ?? 1 };
    g = spring({ from: values[0], to: last, velocity: t.velocity ?? v0, ...p, restDelta: t.rest_delta, restSpeed: t.rest_speed });
  } else if (type === "inertia") {
    g = inertia({ from: values[0], velocity: t.velocity ?? v0, power: t.power, timeConstant: t.time_constant,
      min: t.min, max: t.max });
  } else {
    g = tween({ values, duration: t.duration ?? (values.length > 2 ? 0.5 : feel.duration), ease: t.ease, times: t.times });
  }
  const sp = CFG.speed || 1;
  let out = { duration: g.duration / sp, value: (x) => g.value(x * sp), velocity: (x) => g.velocity(x * sp) * sp };
  const reps = t.repeat === "infinite" || t.repeat >= 1e9 ? Infinity : t.repeat;
  if (reps && out.duration > 0) {
    const d = out.duration, inner = out, alt = t.repeat_type === "reverse" || t.repeat_type === "mirror";
    const local = (x) => {
      if (x <= 0) return 0;
      const i = Math.floor(x / d);
      if (i > reps) return alt && reps % 2 === 1 ? 0 : d;
      const r = x - i * d;
      return alt && i % 2 ? d - r : r;
    };
    out = { duration: reps === Infinity ? Infinity : d * (reps + 1), value: (x) => inner.value(local(x)),
      velocity: (x) => inner.velocity(local(x)), cycle: d * (alt ? 2 : 1) };
  }
  return out;
}

/** Animate `el` to `target` ({key: value or [keyframes]}) with `tr` (a transition). */
export function animateTo(el, target, tr = {}) {
  const s = state(el);
  const t0 = now();
  const props = new Set(), pending = [];
  for (let [k, raw] of Object.entries(target || {})) {
    k = norm(k);
    if (k === "transition" || raw == null) continue;
    const t = transitionFor(tr, k);
    const delay = Number(t.delay || 0) / (CFG.speed || 1);
    const prop = propOf(k);
    const cur = current(el, s, k, t0);
    const list = (Array.isArray(raw) ? raw : [null, raw]).map((v, i) => (v == null && i === 0 ? cur : parse(resolveAuto(el, k, v), k)));
    const to = list[list.length - 1];
    const run = s.run[k];
    if (!Array.isArray(raw) && run && run.to === fmt(to) && !t.type) continue;          // already heading there
    if (!Array.isArray(raw) && !run && !t.type && isNum(cur) && isNum(to) && cur.n === to.n && cur.u === to.u) continue;
    if (!list.every(isNum)) {                                   // colours etc.: the browser interpolates
      pending.push(cssTween(el, s, prop, k, list.map(fmt), t, delay));
      continue;
    }
    const unit = list.find((v) => v.n !== 0)?.u ?? to.u;
    const values = list.map((v) => (v.u === unit || v.n === 0 ? v.n : NaN));
    if (values.some(Number.isNaN)) { set(el, { [k]: fmt(to) }); continue; }  // px → %: jump
    const v0 = run ? run.g.velocity(t0 - run.t0) : 0;
    s.run[k] = { g: generator(t, values, v0, k), t0: t0 + delay, u: unit, to: fmt(to) };
    props.add(prop);
  }
  for (const p of props) pending.push(play(el, s, p));
  if (!pending.length) return Promise.resolve();
  fire(el, "motionstart");
  return Promise.all(pending).then(() => fire(el, "motioncomplete"));
}

function play(el, s, prop) {
  const keys = keysOf(s, prop), runs = keys.map((k) => s.run[k]);
  const t0 = now();
  let end = t0, cycle = 0;
  for (const r of runs) {
    const e = r.t0 + r.g.duration;
    if (e === Infinity) cycle = Math.max(cycle, r.t0 - t0 + r.g.cycle);
    else end = Math.max(end, e);
  }
  if (s.anims[prop]) s.anims[prop].cancel();
  const finish = () => {
    keys.forEach((k, i) => {
      if (s.run[k] !== runs[i]) return;
      s.v[k] = current(el, s, k, Infinity);
      delete s.run[k];
    });
    el.style[prop] = css(el, s, prop, Infinity);
  };
  const span = cycle || end - t0;
  if (span < 1 / 120) { finish(); return Promise.resolve(); }
  const frames = [];
  const n = Math.min(600, Math.max(2, Math.ceil(span * 60) + 1));
  for (let i = 0; i < n; i++) frames.push({ [prop]: css(el, s, prop, t0 + (span * i) / (n - 1)) });
  const a = el.animate(frames, { duration: span * 1000, easing: "linear", fill: "forwards", iterations: cycle ? Infinity : 1 });
  s.anims[prop] = a;
  return a.finished.then(() => {
    if (s.anims[prop] !== a) return;
    finish();
    a.cancel();
    delete s.anims[prop];
  }, () => {});
}

function cssTween(el, s, prop, k, values, t, delay) {
  if (s.anims[prop]) s.anims[prop].cancel();
  const done = () => { s.v[k] = values[values.length - 1]; el.style[prop] = values[values.length - 1]; };
  if (instant(t, k)) { done(); return Promise.resolve(); }
  if (values[0] === "null" || values[0] === "undefined") values[0] = getComputedStyle(el)[prop];
  const ease = Array.isArray(t.ease) ? `cubic-bezier(${t.ease.join(",")})` : (t.ease || "ease-out").replace(/_/g, "-");
  const a = el.animate(values.map((v) => ({ [prop]: v })), { duration: (t.duration ?? 0.3) * 1000 / (CFG.speed || 1),
    delay: delay * 1000, easing: ease, fill: "forwards" });
  s.anims[prop] = a;
  return a.finished.then(() => { if (s.anims[prop] === a) { done(); a.cancel(); delete s.anims[prop]; } }, () => {});
}

/** Stop animating `el` where it is now. */
export function stop(el, keys) {
  const s = state(el);
  const vals = {};
  for (const k of keys || Object.keys(s.run)) vals[k] = fmt(current(el, s, k));
  set(el, vals);
}

function fire(el, name, detail) { el.dispatchEvent(new CustomEvent(name, { bubbles: false, detail })); }

// ------------------------------------------------------------------ presets and variants

const P = (initial, animate, exit, transition) => ({ initial, animate, exit: exit ?? initial, transition });
const PRESETS = {
  fade: P({ opacity: 0 }, { opacity: 1 }),
  "fade-up": P({ opacity: 0, y: 16 }, { opacity: 1, y: 0 }),
  "fade-down": P({ opacity: 0, y: -16 }, { opacity: 1, y: 0 }),
  "fade-left": P({ opacity: 0, x: 16 }, { opacity: 1, x: 0 }),
  "fade-right": P({ opacity: 0, x: -16 }, { opacity: 1, x: 0 }),
  "slide-up": P({ y: "100%" }, { y: 0 }),
  "slide-down": P({ y: "-100%" }, { y: 0 }),
  "slide-left": P({ x: "100%" }, { x: 0 }),
  "slide-right": P({ x: "-100%" }, { x: 0 }),
  scale: P({ opacity: 0, scale: 0.95 }, { opacity: 1, scale: 1 }),
  pop: P({ opacity: 0, scale: 0.6 }, { opacity: 1, scale: 1 }, { opacity: 0, scale: 0.8 }, { type: "spring", stiffness: 400, damping: 15 }),
  "blur-in": P({ opacity: 0, blur: 8 }, { opacity: 1, blur: 0 }),
  flip: P({ opacity: 0, rotate_x: -90 }, { opacity: 1, rotate_x: 0 }),
  collapse: P({ height: 0, opacity: 0 }, { height: "auto", opacity: 1 }),
  shake: { animate: { x: [0, -8, 8, -6, 6, -3, 3, 0] }, transition: { duration: 0.5 } },
  pulse: { animate: { scale: [1, 1.06, 1] }, transition: { duration: 0.6 } },
};
export const presets = () => ({ ...PRESETS, ...CFG.presets });

function preset(name) { return CFG.presets[name] || PRESETS[name]; }

/** A label (variant or preset), dict, or nothing, as [target, transition]. */
function resolve(s, v, kind) {
  if (v == null || typeof v === "boolean") return [null, null];
  if (typeof v === "string") {
    const vars = s.spec.variants;
    if (vars && vars[v]) return split(vars[v]);
    const p = preset(v);
    if (!p) return [null, null];
    const t = kind === "while" ? p.animate : p[kind] ?? (kind === "exit" ? p.initial : null);
    return [t ? normAll(t) : null, p.transition || null];
  }
  return split(v);
}
function split(v) {
  const t = { ...normAll(v) };
  const tr = t.transition || null;
  delete t.transition;
  return [t, tr];
}

function exitOf(s) {
  const e = s.spec.exit;
  if (e === false) return [null, null];
  if (e !== undefined) return resolve(s, e, "exit");
  const a = s.spec.animate;
  return typeof a === "string" && !(s.spec.variants || {})[a] && preset(a) ? resolve(s, a, "exit") : [null, null];
}

// ------------------------------------------------------------------ elements

const ORDER = ["in_view", "focus", "hover", "tap", "drag"];

function motionParent(el) { return el.parentElement && el.parentElement.closest(SEL); }

function readSpec(el) {
  try { return normAll(JSON.parse(el.getAttribute(ATTR) || "{}")) || {}; } catch (_) { return {}; }
}

function wake(el) {
  const raw = el.getAttribute(ATTR);
  const s = state(el);
  if (s.raw === raw) return;
  const first = !s.seen;
  const before = s.raw === undefined ? undefined : JSON.stringify(s.spec.animate);
  s.seen = true;
  s.raw = raw;
  s.spec = readSpec(el);
  const spec = s.spec;
  if (first) {
    let init = spec.initial;
    const parent = motionParent(el), ps = parent && S.get(parent);
    if (init === undefined && typeof spec.animate === "string" && !(spec.variants || {})[spec.animate]) init = spec.animate;
    if (init === undefined && spec.variants && spec.animate === undefined && ps) init = ps.spec.initial;
    const [t] = init === false ? [null] : resolve(s, init, "initial");
    if (t) { set(el, t); s.home = t; }
    gestures(el, s);
    if (spec.layout || spec.layout_id) mod("./motion-layout.js").then((m) => m.watch(el, s, API));
    if (spec.drag) mod("./motion-drag.js").then((m) => m.setup(el, s, API));
    if (spec.animate === "count") { mod("./motion-count.js").then((m) => m.counter(el, API)); return; }
    if (spec.variants && spec.animate === undefined && ps && typeof ps.label === "string") {
      animateTo(el, resolve(s, ps.label, "animate")[0], spec.transition);
      return;
    }
  }
  if (first || JSON.stringify(spec.animate) !== before) refresh(el, s);
}

/** Animate to `animate` plus whatever gestures are active (later ones win). */
export function refresh(el, s = state(el)) {
  const spec = s.spec;
  const [base, btr] = resolve(s, spec.animate, "animate");
  const target = { ...(base || {}) };
  let tr = { ...(normAll(spec.transition) || {}), ...(btr || {}) };
  for (const g of ORDER) {
    if (!s.active.has(g)) continue;
    const [t, gtr] = resolve(s, spec["while_" + g], "while");
    if (t) Object.assign(target, t);
    if (gtr) tr = { ...tr, ...gtr };
  }
  for (const k of s.touched) if (!(k in target)) target[k] = s.home[k] ?? DEFAULT[k];
  if (s.active.has("drag")) { delete target.x; delete target.y; }
  s.touched = new Set(Object.keys(target));
  s.label = typeof spec.animate === "string" ? spec.animate : null;
  const p = animateTo(el, target, tr);
  if (s.label) propagate(el, s, s.label, tr);
  return p;
}

/** Children with variants (and no animate of their own) follow the parent's label, staggered. */
function propagate(el, s, label, tr) {
  const kids = Array.from(el.querySelectorAll(SEL)).filter((c) => motionParent(c) === el);
  const t = normAll(tr) || {};
  const each = Number(t.stagger_children || 0), first = Number(t.delay_children || 0);
  const followers = kids.filter((c) => { wake(c); const cs = state(c); return cs.spec.variants && cs.spec.animate === undefined; });
  const order = t.stagger_direction === -1 ? followers.slice().reverse() : followers;
  order.forEach((c, i) => {
    const cs = state(c);
    cs.label = label;
    const [target, ctr] = resolve(cs, label, "animate");
    animateTo(c, target, { ...(normAll(cs.spec.transition) || {}), ...(ctr || {}), delay: first + i * each });
    propagate(c, cs, label, ctr || cs.spec.transition);
  });
}

function gestures(el, s) {
  const spec = s.spec;
  const on = (g, yes) => { if (yes ? s.active.has(g) : !s.active.has(g)) return; yes ? s.active.add(g) : s.active.delete(g); refresh(el, s); };
  if (spec.while_hover) {
    el.addEventListener("pointerenter", (e) => { if (e.pointerType !== "touch") on("hover", true); });
    el.addEventListener("pointerleave", () => on("hover", false));
  }
  if (spec.while_tap) {
    el.addEventListener("pointerdown", (e) => {
      if (e.button) return;
      on("tap", true);
      const up = () => { on("tap", false); removeEventListener("pointerup", up); removeEventListener("pointercancel", up); };
      addEventListener("pointerup", up);
      addEventListener("pointercancel", up);
    });
    el.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && !e.repeat) on("tap", true); });
    el.addEventListener("keyup", () => on("tap", false));
    el.addEventListener("blur", () => on("tap", false));
  }
  if (spec.while_focus) {
    el.addEventListener("focus", () => { if (el.matches(":focus-visible")) on("focus", true); });
    el.addEventListener("blur", () => on("focus", false));
  }
  if (spec.while_in_view && typeof IntersectionObserver === "function") {
    const vp = normAll(spec.viewport) || {};
    const amount = vp.amount === "all" ? 1 : vp.amount === "some" || vp.amount == null ? 0 : Number(vp.amount);
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { on("in_view", true); if (vp.once) io.disconnect(); } else if (!vp.once) on("in_view", false);
    }, { threshold: amount, rootMargin: vp.margin || "0px" });
    io.observe(el);
  }
}

// ------------------------------------------------------------------ presence (exit animations)

G.exit = (node) => {
  const own = S.get(node);
  if (own && own.exiting) return true;
  const els = [node.matches(SEL) ? node : null, ...node.querySelectorAll(SEL)].filter((e) => {
    if (!e) return false;
    const s = state(e);
    if (!s.seen) s.spec = readSpec(e);
    return exitOf(s)[0];
  });
  if (!els.length) return false;
  state(node).exiting = true;
  node.inert = true;
  node.style.pointerEvents = "none";
  const gone = () => node.remove();
  Promise.all(els.map((e) => {
    const s = state(e);
    const [t, tr] = exitOf(s);
    return animateTo(e, t, { ...(normAll(s.spec.transition) || {}), ...(tr || {}) });
  })).then(gone, gone);
  setTimeout(gone, 4000);
  return true;
};

// ------------------------------------------------------------------ page transitions

G.swap = (run) => {
  if (typeof document.startViewTransition !== "function" || reduced() || CFG.feel === "none") return run();
  const named = new Set();
  for (const el of document.querySelectorAll(SEL)) {           // shared elements morph across pages
    const id = state(el).spec.layout_id || readSpec(el).layout_id;
    if (id && !named.has(id)) { named.add(id); el.style.viewTransitionName = "pw-" + String(id).replace(/[^\w-]/g, "_"); }
  }
  G.vtNames = named;
  const vt = document.startViewTransition(run);
  vt.finished.finally(() => { G.vtNames = null; for (const el of document.querySelectorAll(SEL)) el.style.viewTransitionName = ""; });
  return vt.updateCallbackDone;
};

// ------------------------------------------------------------------ waking elements up

const mods = new Map();
function mod(path) {
  if (!mods.has(path)) mods.set(path, import(path));
  return mods.get(path);
}

function scan(node) {
  if (node.nodeType !== 1) return;
  if (node.matches(SEL)) wake(node);
  for (const el of node.querySelectorAll(SEL)) wake(el);
}
function named(node) {                                          // new page's shared elements, mid-transition
  if (!G.vtNames || node.nodeType !== 1) return;
  for (const el of [node, ...node.querySelectorAll(SEL)]) {
    const id = el.matches && el.matches(SEL) && readSpec(el).layout_id;
    if (id && G.vtNames.has(id)) el.style.viewTransitionName = "pw-" + String(id).replace(/[^\w-]/g, "_");
  }
}

new MutationObserver((records) => {
  for (const r of records) {
    if (r.type === "attributes") { if (r.target.isConnected) wake(r.target); continue; }
    for (const n of r.addedNodes) { named(n); scan(n); }
  }
}).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: [ATTR] });
scan(document.documentElement);

// ------------------------------------------------------------------ browser code API

const API = { animateTo, set, current, state, refresh, stop, reduced, CFG, now, normAll };

globalThis.pywebMotion = { animateTo, set, stop, presets, state, current, now };
