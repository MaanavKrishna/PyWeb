// Toasts, like sonner: stacked in a corner (newest in front, the rest peeking behind, spread out on
// hover), dismissed by swiping or after a while (paused while hovered or focused), announced to
// screen readers (politely; alerts for danger). Built with DOM methods only: text is never HTML.
//
// toast({title, description, tone: "success"|"danger"|"warning"|"info", duration, action: {label, href}})
// From the server: pyweb.ui.toast("Saved") in a server function or form action.

const GAP = 10, PEEK = 14, VISIBLE = 3;
let region = null;

function ensureRegion() {
  if (!region || !region.isConnected) region = document.querySelector('[data-pw-ui="toaster"]');
  if (!region) {
    region = document.createElement("section");
    region.dataset.pwUi = "toaster";
    region.className = "pw-toaster";
    region.dataset.position = "bottom-right";
    document.body.appendChild(region);
  }
  region.setAttribute("aria-label", region.getAttribute("aria-label") || "Notifications");
  if (!region.querySelector("ol")) {
    const ol = document.createElement("ol");
    ol.className = "pw-toast-list";
    region.appendChild(ol);
    region.addEventListener("pointerenter", () => { region.dataset.expanded = ""; layout(); pauseAll(true); });
    region.addEventListener("pointerleave", () => { delete region.dataset.expanded; layout(); pauseAll(false); });
  }
  return region;
}

const items = [];

function layout() {
  const expanded = region && region.hasAttribute("data-expanded");
  const top = region && region.dataset.position && region.dataset.position.startsWith("top");
  let offset = 0;
  items.forEach((t, i) => {
    const el = t.el;
    const h = el.offsetHeight;
    el.style.setProperty("--pw-toast-index", String(i));
    el.dataset.front = i === 0 ? "true" : "false";
    el.dataset.hidden = i >= VISIBLE ? "true" : "false";
    const y = expanded ? offset : i * PEEK;
    const scale = expanded ? 1 : 1 - i * 0.05;
    el.style.setProperty("--pw-toast-y", `${top ? y : -y}px`);
    el.style.setProperty("--pw-toast-scale", String(scale));
    offset += h + GAP;
  });
}

function pauseAll(paused) { for (const t of items) t.pause(paused); }

function remove(t) {
  const i = items.indexOf(t);
  if (i < 0) return;
  items.splice(i, 1);
  clearTimeout(t.timer);
  t.el.dataset.state = "closed";
  t.el.style.setProperty("--pw-toast-x", t.swiped ? `${t.swiped}px` : "0px");
  setTimeout(() => t.el.remove(), 220);
  layout();
}

export function toast(opts) {
  if (typeof opts === "string") opts = { title: opts };
  const r = ensureRegion();
  const list = r.querySelector("ol");
  const el = document.createElement("li");
  el.className = "pw-toast";
  el.dataset.part = "toast";
  el.dataset.state = "open";
  if (opts.tone) el.dataset.tone = String(opts.tone);
  el.setAttribute("role", opts.tone === "danger" ? "alert" : "status");
  el.setAttribute("aria-live", opts.tone === "danger" ? "assertive" : "polite");
  el.setAttribute("aria-atomic", "true");
  el.tabIndex = 0;
  const body = document.createElement("div");
  body.dataset.part = "body";
  const title = document.createElement("div");
  title.dataset.part = "title";
  title.textContent = String(opts.title ?? "");
  body.appendChild(title);
  if (opts.description) {
    const d = document.createElement("div");
    d.dataset.part = "description";
    d.textContent = String(opts.description);
    body.appendChild(d);
  }
  el.appendChild(body);
  const t = { el, timer: 0, left: Number(opts.duration ?? 5000), started: 0, swiped: 0 };
  if (opts.action && opts.action.label) {
    const a = document.createElement(opts.action.href ? "a" : "button");
    a.className = "pw-button";
    a.dataset.size = "sm";
    a.dataset.variant = "outline";
    a.dataset.part = "action";
    a.textContent = String(opts.action.label);
    if (opts.action.href && /^(\/(?!\/)|https?:)/.test(String(opts.action.href))) a.href = String(opts.action.href);
    else a.type = "button";
    a.addEventListener("click", () => { if (typeof opts.action.onclick === "function") opts.action.onclick(); remove(t); });
    el.appendChild(a);
  }
  const x = document.createElement("button");
  x.type = "button";
  x.className = "pw-toast-x";
  x.dataset.part = "close";
  x.setAttribute("aria-label", "Dismiss");
  x.textContent = "×";
  x.addEventListener("click", () => remove(t));
  el.appendChild(x);

  t.pause = (paused) => {
    if (!Number.isFinite(t.left) || t.left <= 0) return;
    if (paused) { clearTimeout(t.timer); t.left -= Date.now() - t.started; }
    else { t.started = Date.now(); t.timer = setTimeout(() => remove(t), Math.max(t.left, 800)); }
  };
  el.addEventListener("focusin", () => t.pause(true));
  el.addEventListener("focusout", () => t.pause(false));
  el.addEventListener("keydown", (e) => { if (e.key === "Escape" || e.key === "Delete") remove(t); });

  // Swipe sideways to dismiss.
  let startX = null;
  el.addEventListener("pointerdown", (e) => {
    if (e.target instanceof Element && e.target.closest("button,a")) return;
    startX = e.clientX;
    el.setPointerCapture(e.pointerId);
    el.dataset.swiping = "";
  });
  el.addEventListener("pointermove", (e) => {
    if (startX === null) return;
    const dx = e.clientX - startX;
    el.style.setProperty("--pw-toast-x", `${dx}px`);
    el.style.opacity = String(Math.max(0, 1 - Math.abs(dx) / 220));
  });
  const end = (e) => {
    if (startX === null) return;
    const dx = e.clientX - startX;
    startX = null;
    delete el.dataset.swiping;
    if (Math.abs(dx) > 80) { t.swiped = dx > 0 ? 400 : -400; remove(t); }
    else { el.style.setProperty("--pw-toast-x", "0px"); el.style.opacity = ""; }
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);

  list.prepend(el);
  items.unshift(t);
  while (items.length > 12) remove(items[items.length - 1]);
  requestAnimationFrame(layout);
  if (!r.hasAttribute("data-expanded")) t.pause(false);
  return { dismiss: () => remove(t) };
}

export default function setup(root) {
  region = root;
  ensureRegion();
  return null;
}
