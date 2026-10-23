// PyWeb motion: layout animations. An element with `layout` that moves or resizes (a row
// reordered, a panel opened above it) animates from where it was to where it is: it keeps its
// DOM identity, so focus and input values survive. `layout_id` lets an element that appears take
// over from another with the same id (a tab underline moving between tabs, a card opening up).
// Positions are measured in a batch after every DOM change: all reads, then all writes.

const items = new Set();
const boxes = new WeakMap();
const ids = new Map();               // layout_id -> { box, t }
let api = null, queued = false;

function box(el, s) {
  const r = el.getBoundingClientRect();
  const c = (k, d) => (api ? api.current(el, s, k).n : d);
  const sx = c("lsx", 1) || 1, sy = c("lsy", 1) || 1;
  const w = r.width / sx, h = r.height / sy;
  return { cx: r.left + r.width / 2 - c("lx", 0) + scrollX, cy: r.top + r.height / 2 - c("ly", 0) + scrollY, w, h };
}

function check() {
  queued = false;
  const measured = [];
  for (const el of items) {
    if (!el.isConnected) { items.delete(el); continue; }
    const s = api.state(el);
    measured.push([el, s, boxes.get(el), box(el, s)]);
  }
  const t = performance.now();
  for (const [el, s, old, now] of measured) {
    boxes.set(el, now);
    const id = s.spec.layout_id;
    if (id != null) ids.set(id, { box: now, t });
    if (!old || api.reduced() || now.w === 0 || now.h === 0) continue;
    const dx = old.cx - now.cx, dy = old.cy - now.cy;
    const sizeToo = s.spec.layout !== "position";
    const sx = sizeToo ? old.w / now.w : 1, sy = sizeToo ? old.h / now.h : 1;
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) continue;
    const lx = api.current(el, s, "lx").n, ly = api.current(el, s, "ly").n;
    const tr = (s.spec.transition && (s.spec.transition.layout || s.spec.transition)) || {};
    api.animateTo(el, { lx: [dx + lx, 0], ly: [dy + ly, 0], lsx: [sx, 1], lsy: [sy, 1] }, tr);
  }
}

function schedule() {
  if (queued) return;
  queued = true;
  queueMicrotask(check);
}

let observing = false;
function observe() {
  if (observing) return;
  observing = true;
  new MutationObserver(schedule).observe(document.documentElement,
    { childList: true, subtree: true, attributes: true, characterData: true });
  addEventListener("resize", () => { for (const el of items) boxes.set(el, box(el, api.state(el))); });
}

/** Track `el` (called by motion.js for elements with `layout` or `layout_id`). */
export function watch(el, s, motion) {
  api = motion;
  observe();
  if (items.has(el)) return;
  items.add(el);
  const id = s.spec.layout_id;
  const prev = id != null && ids.get(id);
  // A new element taking over a layout_id starts where the last one was (seen within a second).
  if (prev && performance.now() - prev.t < 1000) boxes.set(el, prev.box);
  else boxes.set(el, box(el, s));
  schedule();
}
