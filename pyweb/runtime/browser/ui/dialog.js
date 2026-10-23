// Dialog, AlertDialog and Sheet (WAI-ARIA APG "Dialog (Modal)" and "Alert Dialog").
// Built on the native <dialog>: top layer, the rest of the page inert. Adds exit animations,
// closing on Escape and on a click outside (not for alert dialogs), focus going back to the
// trigger, scroll lock, and a controlled `open` (data-open on the root).
import { fire, lockScroll, part, partsAll, pushLayer, setOpen, trapFocus, truthy, uid, watchAttr } from "./core.js";

export default function setup(root) {
  const content = part(root, "content");
  if (!content) return null;
  const alert = root.dataset.role === "alertdialog";
  const triggers = partsAll(root, "trigger");
  const title = part(root, "title"), desc = part(root, "description");
  content.setAttribute("role", alert ? "alertdialog" : "dialog");
  content.setAttribute("aria-modal", "true");
  if (title) { title.id ||= uid("pw-dialog-title"); content.setAttribute("aria-labelledby", title.id); }
  if (desc) { desc.id ||= uid("pw-dialog-desc"); content.setAttribute("aria-describedby", desc.id); }
  content.id ||= uid("pw-dialog");
  for (const t of triggers) {
    t.setAttribute("aria-haspopup", "dialog");
    t.setAttribute("aria-controls", content.id);
    t.setAttribute("aria-expanded", "false");
  }
  let release = [];

  function open() {
    if (root.dataset.state === "open") return;
    if (typeof content.showModal === "function" && !content.open) content.showModal();
    else content.setAttribute("open", "");
    root.dataset.state = "open";
    setOpen(content, true);
    for (const t of triggers) t.setAttribute("aria-expanded", "true");
    release = [
      lockScroll(),
      pushLayer({ el: content, dismiss: () => close(), outside: !alert }),
      trapFocus(content, { initial: alert ? '[data-part="cancel"],[data-part="close"]' : undefined }),
    ];
    fire(root, "openchange", { open: true });
  }

  function close() {
    if (root.dataset.state !== "open") return;
    root.dataset.state = "closed";
    for (const t of triggers) t.setAttribute("aria-expanded", "false");
    const [unlock, unlayer, untrap] = release;
    release = [];
    if (unlayer) unlayer();
    if (untrap) untrap();
    setOpen(content, false, () => {
      if (typeof content.close === "function" && content.open) content.close();
      else content.removeAttribute("open");
      if (unlock) unlock();
    });
    fire(root, "openchange", { open: false });
  }

  const onTrigger = (e) => { e.preventDefault(); open(); };
  for (const t of triggers) t.addEventListener("click", onTrigger);
  const onClick = (e) => {
    const el = e.target instanceof Element ? e.target.closest('[data-part="close"],[data-part="cancel"],[data-part="action"]') : null;
    if (el && content.contains(el)) {
      // An action's own handler runs first; it can keep the dialog open with event.preventDefault().
      setTimeout(() => { if (!e.defaultPrevented) close(); }, 0);
    }
  };
  content.addEventListener("click", onClick);
  // A click on the backdrop lands on <dialog> itself, outside its box.
  const onDown = (e) => {
    if (alert || e.target !== content) return;
    const r = content.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close();
  };
  content.addEventListener("pointerdown", onDown);
  // The browser's own Escape handling would skip the exit animation: go through close().
  const onCancel = (e) => { e.preventDefault(); close(); };
  content.addEventListener("cancel", onCancel);
  const unwatch = watchAttr(root, "data-open", (v) => (truthy(v) ? open() : close()));
  if (root.hasAttribute("data-open") && truthy(root.getAttribute("data-open"))) open();

  root.pwOpen = open;
  root.pwClose = close;
  return () => {
    unwatch();
    for (const t of triggers) t.removeEventListener("click", onTrigger);
    content.removeEventListener("click", onClick);
    content.removeEventListener("pointerdown", onDown);
    content.removeEventListener("cancel", onCancel);
    for (const r of release) if (r) r(false);
  };
}
