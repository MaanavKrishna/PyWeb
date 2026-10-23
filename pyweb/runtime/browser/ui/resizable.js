// Resizable panels (WAI-ARIA APG "Window Splitter"): drag a handle, or focus it and use the arrow
// keys (Home/End: smallest/largest, Enter: collapse/restore). Sizes are percentages, kept within
// each panel's data-min/data-max; data-save="name" remembers them in this browser.
import { fire, partsAll, uid } from "./core.js";

export default function setup(root) {
  const vertical = root.dataset.direction === "vertical";
  const panels = partsAll(root, "panel");
  const handles = partsAll(root, "handle");
  if (panels.length < 2) return null;
  const key = root.dataset.save ? `pyweb:resizable:${root.dataset.save}` : null;
  const lim = (p) => [Number(p.dataset.min || 0), Number(p.dataset.max || 100)];
  let sizes = panels.map((p) => Number(p.dataset.size) || 100 / panels.length);
  try {
    const saved = key && JSON.parse(localStorage.getItem(key) || "null");
    if (Array.isArray(saved) && saved.length === panels.length) sizes = saved.map(Number);
  } catch (_) { /* storage unavailable */ }
  const before = (h) => panels.filter((p) => h.compareDocumentPosition(p) & Node.DOCUMENT_POSITION_PRECEDING).length - 1;

  function apply(save) {
    panels.forEach((p, i) => {
      p.style.flex = `${sizes[i]} 1 0px`;
      p.id ||= uid("pw-panel");
      p.dataset.state = sizes[i] <= lim(p)[0] && p.dataset.collapsible !== undefined ? "collapsed" : "expanded";
    });
    handles.forEach((h) => {
      const i = before(h);
      h.setAttribute("role", "separator");
      h.setAttribute("aria-orientation", vertical ? "horizontal" : "vertical");
      h.setAttribute("aria-controls", panels[i].id);
      h.setAttribute("aria-valuenow", String(Math.round(sizes[i])));
      h.setAttribute("aria-valuemin", String(lim(panels[i])[0]));
      h.setAttribute("aria-valuemax", String(lim(panels[i])[1]));
      if (!h.hasAttribute("tabindex")) h.tabIndex = 0;
    });
    if (save) {
      try { if (key) localStorage.setItem(key, JSON.stringify(sizes)); } catch (_) { /* full or blocked */ }
      fire(root, "resize", { sizes: [...sizes] });
    }
  }

  /** Move the boundary after panel i by `delta` percent, within both panels' limits. */
  function move(i, delta, save = true) {
    const a = panels[i], b = panels[i + 1];
    const [amin, amax] = lim(a), [bmin, bmax] = lim(b);
    const total = sizes[i] + sizes[i + 1];
    const next = Math.min(Math.max(sizes[i] + delta, amin, total - bmax), amax, total - bmin);
    sizes[i] = next; sizes[i + 1] = total - next;
    apply(save);
  }

  let drag = null;
  const onDown = (e) => {
    const h = e.target instanceof Element ? e.target.closest('[data-part="handle"]') : null;
    if (!h || !handles.includes(h) || e.button !== 0) return;
    e.preventDefault();
    h.setPointerCapture(e.pointerId);
    const rect = root.getBoundingClientRect();
    drag = { h, i: before(h), start: vertical ? e.clientY : e.clientX, size: vertical ? rect.height : rect.width, base: [...sizes] };
    h.dataset.state = "drag";
    root.dataset.resizing = "";
  };
  const onMove = (e) => {
    if (!drag) return;
    sizes = [...drag.base];
    move(drag.i, (((vertical ? e.clientY : e.clientX) - drag.start) / drag.size) * 100, false);
  };
  const onUp = () => {
    if (!drag) return;
    delete drag.h.dataset.state;
    delete root.dataset.resizing;
    drag = null;
    apply(true);
  };
  const onKey = (e) => {
    const h = e.target instanceof Element ? e.target.closest('[data-part="handle"]') : null;
    if (!h || !handles.includes(h)) return;
    const i = before(h), step = e.shiftKey ? 10 : 2;
    const back = vertical ? "ArrowUp" : "ArrowLeft", fwd = vertical ? "ArrowDown" : "ArrowRight";
    if (e.key === back) move(i, -step);
    else if (e.key === fwd) move(i, step);
    else if (e.key === "Home") move(i, -100);
    else if (e.key === "End") move(i, 100);
    else if (e.key === "Enter") {
      const p = panels[i];
      if (sizes[i] > lim(p)[0]) { p.dataset.restore = String(sizes[i]); move(i, -100); }
      else move(i, Number(p.dataset.restore || 50) - sizes[i]);
    } else return;
    e.preventDefault();
  };
  root.addEventListener("pointerdown", onDown);
  root.addEventListener("pointermove", onMove);
  root.addEventListener("pointerup", onUp);
  root.addEventListener("pointercancel", onUp);
  root.addEventListener("keydown", onKey);
  apply(false);
  return () => {
    for (const [ev, fn] of [["pointerdown", onDown], ["pointermove", onMove], ["pointerup", onUp], ["pointercancel", onUp], ["keydown", onKey]])
      root.removeEventListener(ev, fn);
  };
}
