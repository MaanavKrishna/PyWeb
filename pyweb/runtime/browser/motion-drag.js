// PyWeb motion: drag. `drag` (both axes) or `drag="x"`/`"y"`; drag_constraints={{"left": -100,
// "right": 100}} (px from where it started) or "parent"; drag_elastic (0 hard … 1 loose, how far
// past the edge it gives); drag_momentum (keeps coasting after a throw, default on);
// drag_snap_back (returns home when let go). Fires dragstart, drag and dragend with
// {x, y, velocity}. Focusable draggables move with the arrow keys too.

function bounds(el, s, api, axis) {
  const c = s.spec.drag_constraints;
  if (!c) return null;
  const x = api.current(el, s, "x").n, y = api.current(el, s, "y").n;
  if (c === "parent" && el.parentElement) {
    const p = el.parentElement.getBoundingClientRect(), r = el.getBoundingClientRect();
    return { left: x + p.left - r.left, right: x + p.right - r.right, top: y + p.top - r.top, bottom: y + p.bottom - r.bottom };
  }
  void axis;
  return { left: c.left ?? -Infinity, right: c.right ?? Infinity, top: c.top ?? -Infinity, bottom: c.bottom ?? Infinity };
}

function elastic(v, lo, hi, e) {
  if (v < lo) return lo - (lo - v) * e;
  if (v > hi) return hi + (v - hi) * e;
  return v;
}

export function setup(el, s, api) {
  const axis = s.spec.drag === "x" || s.spec.drag === "y" ? s.spec.drag : null;
  el.style.touchAction = axis === "x" ? "pan-y" : axis === "y" ? "pan-x" : "none";
  el.style.userSelect = "none";
  let drag = null;
  const fire = (name, detail) => el.dispatchEvent(new CustomEvent(name, { detail }));
  const release = (x, y, vx, vy, b) => {
    const spec = s.spec;
    const tr = { ...(spec.transition || {}) };
    if (spec.drag_snap_back) {
      return api.animateTo(el, { x: 0, y: 0 }, { ...tr, x: { type: "spring", velocity: vx }, y: { type: "spring", velocity: vy } });
    }
    if (spec.drag_momentum === false) {
      if (!b) return null;
      return api.animateTo(el, { x: Math.min(Math.max(x, b.left), b.right), y: Math.min(Math.max(y, b.top), b.bottom) },
        { ...tr, x: { type: "spring", velocity: vx }, y: { type: "spring", velocity: vy } });
    }
    const ix = { type: "inertia", velocity: vx, min: b ? b.left : undefined, max: b ? b.right : undefined };
    const iy = { type: "inertia", velocity: vy, min: b ? b.top : undefined, max: b ? b.bottom : undefined };
    const target = {};
    if (axis !== "y") target.x = x;
    if (axis !== "x") target.y = y;
    return api.animateTo(el, target, { ...tr, x: ix, y: iy });
  };

  el.addEventListener("pointerdown", (e) => {
    if (e.button || (e.target instanceof Element && e.target.closest("input,textarea,select,button,a[href]") && e.target !== el)) return;
    api.stop(el, ["x", "y"]);
    try { el.setPointerCapture(e.pointerId); } catch { /* the pointer is already gone */ }
    // Follow the pointer on the window (captured events bubble there): WebKit can stop sending a
    // captured pointer's events to an element that moves under it.
    addEventListener("pointermove", move);
    addEventListener("pointerup", end);
    addEventListener("pointercancel", end);
    const x = api.current(el, s, "x").n, y = api.current(el, s, "y").n;
    drag = { id: e.pointerId, px: e.clientX, py: e.clientY, x, y, b: bounds(el, s, api, axis), samples: [[performance.now(), x, y]] };
    s.active.add("drag");
    api.refresh(el, s);
    fire("dragstart", { x, y, velocity: { x: 0, y: 0 } });
  });
  const move = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const ela = s.spec.drag_elastic === false ? 0 : s.spec.drag_elastic ?? 0.35;
    let x = axis === "y" ? drag.x : drag.x + e.clientX - drag.px;
    let y = axis === "x" ? drag.y : drag.y + e.clientY - drag.py;
    if (drag.b) { x = elastic(x, drag.b.left, drag.b.right, ela); y = elastic(y, drag.b.top, drag.b.bottom, ela); }
    api.set(el, { x, y });
    drag.samples.push([performance.now(), x, y]);
    if (drag.samples.length > 6) drag.samples.shift();
    fire("drag", { x, y });
  };
  function end(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const d = drag;
    drag = null;
    removeEventListener("pointermove", move);
    removeEventListener("pointerup", end);
    removeEventListener("pointercancel", end);
    const [t1, x1, y1] = d.samples[d.samples.length - 1];
    const old = d.samples.find(([t]) => t1 - t < 100) || d.samples[0];
    const dt = Math.max((t1 - old[0]) / 1000, 1 / 120);
    const vx = (x1 - old[1]) / dt, vy = (y1 - old[2]) / dt;
    s.active.delete("drag");
    api.refresh(el, s);
    release(x1, y1, axis === "y" ? 0 : vx, axis === "x" ? 0 : vy, d.b);
    fire("dragend", { x: x1, y: y1, velocity: { x: vx, y: vy } });
  }
  el.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 50 : 10;
    const dx = { ArrowLeft: -step, ArrowRight: step }[e.key] || 0, dy = { ArrowUp: -step, ArrowDown: step }[e.key] || 0;
    if ((!dx && !dy) || (axis === "x" && !dx) || (axis === "y" && !dy) || e.target !== el) return;
    e.preventDefault();
    const b = bounds(el, s, api, axis);
    let x = api.current(el, s, "x").n + dx, y = api.current(el, s, "y").n + dy;
    if (b) { x = Math.min(Math.max(x, b.left), b.right); y = Math.min(Math.max(y, b.top), b.bottom); }
    api.animateTo(el, { x, y }, s.spec.transition || {});
  });
}
