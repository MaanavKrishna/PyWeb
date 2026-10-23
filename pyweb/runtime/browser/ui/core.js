// PyWeb UI kit: shared behaviour for the primitives (focus, layers, positioning, keyboard).
// No HTML is ever parsed here: everything works under the strict CSP and Trusted Types.

let _id = 0;
export const uid = (prefix = "pw") => `${prefix}-${(++_id).toString(36)}${Math.random().toString(36).slice(2, 6)}`;

export function fire(el, name, detail) {
  return el.dispatchEvent(new CustomEvent(name, { bubbles: true, cancelable: true, detail }));
}

export const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

// ------------------------------------------------------------------ focus

const TABBABLE = 'a[href],area[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),' +
  'select:not([disabled]),textarea:not([disabled]),iframe,[tabindex],[contenteditable="true"]';

export function tabbables(root) {
  return Array.from(root.querySelectorAll(TABBABLE)).filter((el) =>
    el.tabIndex >= 0 && !el.closest("[inert]") && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
}

/** Keep Tab inside `container` until the returned function is called; focus goes back where it was. */
export function trapFocus(container, { initial } = {}) {
  const before = document.activeElement;
  const onKey = (e) => {
    if (e.key !== "Tab") return;
    const items = tabbables(container);
    if (!items.length) { e.preventDefault(); container.focus(); return; }
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || !container.contains(document.activeElement))) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  };
  container.addEventListener("keydown", onKey);
  requestAnimationFrame(() => {
    const target = (initial && container.querySelector(initial)) || container.querySelector("[autofocus]") ||
      tabbables(container)[0] || container;
    if (target === container && !container.hasAttribute("tabindex")) container.setAttribute("tabindex", "-1");
    target.focus({ preventScroll: true });
  });
  return (restore = true) => {
    container.removeEventListener("keydown", onKey);
    if (restore && before && typeof before.focus === "function" && document.contains(before)) before.focus({ preventScroll: true });
  };
}

// ------------------------------------------------------------------ dismiss layers

const layers = [];

function topLayer() { return layers[layers.length - 1]; }

function onKeydown(e) {
  const layer = topLayer();
  if (e.key === "Escape" && layer && layer.escape !== false) {
    e.preventDefault();
    e.stopPropagation();
    layer.dismiss("escape");
  }
}

function onPointerDown(e) {
  const layer = topLayer();
  if (!layer || layer.outside === false) return;
  const t = e.target;
  if (layer.el.contains(t) || (layer.keep && layer.keep.some((k) => k && k.contains(t)))) return;
  // A click inside a nested layer (a menu opened from this dialog) isn't "outside".
  if (layers.some((l) => l !== layer && l.el.contains(t))) return;
  layer.dismiss("outside");
}

/** Register an open layer; Escape and clicks outside it (not in `keep`) call `dismiss`. */
export function pushLayer(layer) {
  if (!layers.length) {
    document.addEventListener("keydown", onKeydown, true);
    document.addEventListener("pointerdown", onPointerDown, true);
  }
  layers.push(layer);
  return () => {
    const i = layers.indexOf(layer);
    if (i >= 0) layers.splice(i, 1);
    if (!layers.length) {
      document.removeEventListener("keydown", onKeydown, true);
      document.removeEventListener("pointerdown", onPointerDown, true);
    }
  };
}

let locks = 0, saved = null;
/** Stop the page behind a modal from scrolling (without the layout jumping). */
export function lockScroll() {
  if (!locks++) {
    const html = document.documentElement;
    const gap = innerWidth - html.clientWidth;
    saved = [html.style.overflow, html.style.paddingRight];
    html.style.overflow = "hidden";
    if (gap > 0) html.style.paddingRight = `${gap}px`;
  }
  let released = false;
  return () => {
    if (released) return;
    released = true;
    if (!--locks && saved) {
      document.documentElement.style.overflow = saved[0];
      document.documentElement.style.paddingRight = saved[1];
    }
  };
}

// ------------------------------------------------------------------ presence (enter/exit)

/** Show `el` (data-state="open"); hiding waits for its exit animation, then calls `done`. */
export function setOpen(el, open, done) {
  el.dataset.state = open ? "open" : "closed";
  if (open) { if (done) done(); return; }
  const anims = typeof el.getAnimations === "function" && !reducedMotion() ? el.getAnimations({ subtree: true }) : [];
  if (!anims.length) { if (done) done(); return; }
  let finished = false;
  const finish = () => { if (!finished) { finished = true; if (el.dataset.state === "closed" && done) done(); } };
  Promise.all(anims.map((a) => a.finished.catch(() => {}))).then(finish);
  setTimeout(finish, 600);       // never leave something half-closed
}

// ------------------------------------------------------------------ roving focus and typeahead

/**
 * Arrow keys move focus among `selector` items in `root` (one item is tabbable at a time);
 * Home/End jump; printable keys jump to the next item starting with what was typed.
 */
export function roving(root, selector, { orientation = "vertical", loop = true, onMove, activate } = {}) {
  let typed = "", timer = 0;
  const items = () => Array.from(root.querySelectorAll(selector)).filter((el) => !el.hasAttribute("disabled") &&
    el.getAttribute("aria-disabled") !== "true" && !el.closest("[hidden]"));
  const focusItem = (el) => {
    if (!el) return;
    for (const it of items()) it.tabIndex = it === el ? 0 : -1;
    el.focus({ preventScroll: false });
    if (onMove) onMove(el);
  };
  const prevKeys = orientation === "horizontal" ? ["ArrowLeft"] : orientation === "both" ? ["ArrowUp", "ArrowLeft"] : ["ArrowUp"];
  const nextKeys = orientation === "horizontal" ? ["ArrowRight"] : orientation === "both" ? ["ArrowDown", "ArrowRight"] : ["ArrowDown"];
  const onKey = (e) => {
    const list = items();
    if (!list.length) return;
    const i = list.indexOf(document.activeElement);
    if (nextKeys.includes(e.key)) {
      e.preventDefault();
      focusItem(list[i < 0 ? 0 : i + 1 < list.length ? i + 1 : loop ? 0 : i]);
    } else if (prevKeys.includes(e.key)) {
      e.preventDefault();
      focusItem(list[i < 0 ? list.length - 1 : i > 0 ? i - 1 : loop ? list.length - 1 : 0]);
    } else if (e.key === "Home" || e.key === "PageUp") {
      e.preventDefault(); focusItem(list[0]);
    } else if (e.key === "End" || e.key === "PageDown") {
      e.preventDefault(); focusItem(list[list.length - 1]);
    } else if ((e.key === "Enter" || e.key === " ") && activate && i >= 0) {
      e.preventDefault(); activate(list[i], e);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && e.key !== " ") {
      clearTimeout(timer);
      typed += e.key.toLowerCase();
      timer = setTimeout(() => { typed = ""; }, 600);
      const start = i < 0 ? 0 : (typed.length === 1 ? i + 1 : i);
      const order = list.slice(start).concat(list.slice(0, start));
      const hit = order.find((el) => (el.dataset.textValue || el.textContent || "").trim().toLowerCase().startsWith(typed));
      if (hit) focusItem(hit);
    }
  };
  root.addEventListener("keydown", onKey);
  const list = items();
  if (list.length && !list.some((el) => el.tabIndex === 0)) list.forEach((el, n) => { el.tabIndex = n === 0 ? 0 : -1; });
  return { items, focus: focusItem, first: () => focusItem(items()[0]), last: () => focusItem(items().slice(-1)[0]),
    stop: () => root.removeEventListener("keydown", onKey) };
}

// ------------------------------------------------------------------ positioning

const OPPOSITE = { top: "bottom", bottom: "top", left: "right", right: "left" };

/**
 * Place `floating` next to `anchor` (position: fixed): on `side` ("top", "bottom", "left",
 * "right"), aligned "start", "center" or "end", `offset` px away. Flips to the other side when
 * there isn't room, shifts to stay `padding` px inside the viewport, and sizes itself to the
 * space available (--pw-available-height/width). An element with data-part="arrow" inside
 * points at the anchor.
 */
export function place(anchor, floating, { side = "bottom", align = "center", offset = 6, padding = 8, point } = {}) {
  const a = point ? { left: point.x, top: point.y, right: point.x, bottom: point.y, width: 0, height: 0 } : anchor.getBoundingClientRect();
  const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
  floating.style.position = "fixed";
  floating.style.top = "0px";
  floating.style.left = "0px";
  floating.style.setProperty("--pw-anchor-width", `${a.width}px`);
  floating.style.setProperty("--pw-anchor-height", `${a.height}px`);
  const f = floating.getBoundingClientRect();
  const space = { top: a.top - padding, bottom: vh - a.bottom - padding, left: a.left - padding, right: vw - a.right - padding };
  const need = side === "top" || side === "bottom" ? f.height + offset : f.width + offset;
  if (space[side] < need && space[OPPOSITE[side]] > space[side]) side = OPPOSITE[side];
  let x, y;
  const vertical = side === "top" || side === "bottom";
  if (vertical) {
    y = side === "bottom" ? a.bottom + offset : a.top - offset - f.height;
    x = align === "start" ? a.left : align === "end" ? a.right - f.width : a.left + a.width / 2 - f.width / 2;
    x = Math.min(Math.max(x, padding), vw - padding - f.width);
  } else {
    x = side === "right" ? a.right + offset : a.left - offset - f.width;
    y = align === "start" ? a.top : align === "end" ? a.bottom - f.height : a.top + a.height / 2 - f.height / 2;
    y = Math.min(Math.max(y, padding), vh - padding - f.height);
  }
  floating.style.left = `${Math.round(x)}px`;
  floating.style.top = `${Math.round(y)}px`;
  floating.style.setProperty("--pw-available-height", `${Math.max(0, vertical ? space[side] - offset : vh - 2 * padding)}px`);
  floating.style.setProperty("--pw-available-width", `${Math.max(0, vertical ? vw - 2 * padding : space[side] - offset)}px`);
  floating.dataset.side = side;
  floating.dataset.align = align;
  // Animations start from the anchor (scale/slide "out of" the trigger).
  const ox = vertical ? a.left + a.width / 2 - x : side === "right" ? 0 : f.width;
  const oy = vertical ? (side === "bottom" ? 0 : f.height) : a.top + a.height / 2 - y;
  floating.style.setProperty("--pw-transform-origin", `${Math.round(ox)}px ${Math.round(oy)}px`);
  const arrow = floating.querySelector('[data-part="arrow"]');
  if (arrow) {
    const size = arrow.offsetWidth || 10;
    if (vertical) {
      arrow.style.left = `${Math.round(Math.min(Math.max(a.left + a.width / 2 - x - size / 2, 6), f.width - size - 6))}px`;
      arrow.style.top = "";
    } else {
      arrow.style.top = `${Math.round(Math.min(Math.max(a.top + a.height / 2 - y - size / 2, 6), f.height - size - 6))}px`;
      arrow.style.left = "";
    }
  }
  return { side, x, y };
}

/** Keep `floating` placed while it's open (scrolling, resizing); returns a stop function. */
export function autoPlace(anchor, floating, opts) {
  let frame = 0;
  const update = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => place(anchor, floating, opts)); };
  place(anchor, floating, opts);
  addEventListener("scroll", update, true);
  addEventListener("resize", update);
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(update) : null;
  if (ro) { ro.observe(floating); if (anchor) ro.observe(anchor); }
  return () => {
    cancelAnimationFrame(frame);
    removeEventListener("scroll", update, true);
    removeEventListener("resize", update);
    if (ro) ro.disconnect();
  };
}

// ------------------------------------------------------------------ helpers for the primitives

/** The parts of a component: `parts(root, "trigger")` finds data-part="trigger" that belongs to `root`. */
export function part(root, name) {
  const kind = root.dataset.pwUi;
  for (const el of root.querySelectorAll(`[data-part="${name}"]`)) {
    const owner = el.closest(`[data-pw-ui="${kind}"]`);
    if (owner === root) return el;
  }
  return null;
}

export function partsAll(root, name) {
  const kind = root.dataset.pwUi;
  return Array.from(root.querySelectorAll(`[data-part="${name}"]`)).filter((el) => el.closest(`[data-pw-ui="${kind}"]`) === root);
}

/** Call `cb(value)` when attribute `name` of `el` changes (controlled components: open={...}). */
export function watchAttr(el, name, cb) {
  const mo = new MutationObserver(() => cb(el.getAttribute(name)));
  mo.observe(el, { attributes: true, attributeFilter: [name] });
  return () => mo.disconnect();
}

export function truthy(v) { return v !== null && v !== "false" && v !== "0"; }
