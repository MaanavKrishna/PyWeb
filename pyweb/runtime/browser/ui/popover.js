// Popover (WAI-ARIA APG "Disclosure" + non-modal dialog): a panel attached to its trigger.
// In the top layer (the Popover API) where available, placed with collision handling, closed by
// Escape or a click outside; focus moves into it and back to the trigger.
import { autoPlace, fire, part, partsAll, pushLayer, setOpen, tabbables, truthy, uid, watchAttr } from "./core.js";

export function floating(root, content) {
  if (content.popover !== undefined && !content.hasAttribute("popover")) content.setAttribute("popover", "manual");
  const top = () => typeof content.showPopover === "function" && content.hasAttribute("popover");
  return {
    show() {
      content.hidden = false;
      if (top()) { try { content.showPopover(); } catch (_) { /* already open */ } }
    },
    hide() {
      if (top()) { try { content.hidePopover(); } catch (_) { /* already closed */ } }
      content.hidden = true;
    },
  };
}

export default function setup(root) {
  const content = part(root, "content");
  const triggers = partsAll(root, "trigger");
  const anchor = part(root, "anchor") || triggers[0];
  if (!content || !anchor) return null;
  const layer = floating(root, content);
  content.id ||= uid("pw-popover");
  content.setAttribute("role", content.getAttribute("role") || "dialog");
  for (const t of triggers) {
    t.setAttribute("aria-haspopup", "dialog");
    t.setAttribute("aria-controls", content.id);
    t.setAttribute("aria-expanded", "false");
  }
  const opts = () => ({ side: root.dataset.side || "bottom", align: root.dataset.align || "center",
                        offset: Number(root.dataset.offset || 6) });
  let stop = [];

  function open() {
    if (root.dataset.state === "open") return;
    root.dataset.state = "open";
    layer.show();
    setOpen(content, true);
    for (const t of triggers) t.setAttribute("aria-expanded", "true");
    stop = [autoPlace(anchor, content, opts()), pushLayer({ el: content, keep: triggers, dismiss: () => close(true) })];
    if (root.dataset.focus !== "none") requestAnimationFrame(() => (tabbables(content)[0] || content).focus({ preventScroll: true }));
    fire(root, "openchange", { open: true });
  }

  function close(returnFocus) {
    if (root.dataset.state !== "open") return;
    root.dataset.state = "closed";
    for (const t of triggers) t.setAttribute("aria-expanded", "false");
    for (const s of stop) s();
    stop = [];
    const hadFocus = content.contains(document.activeElement);
    setOpen(content, false, () => layer.hide());
    if (returnFocus || hadFocus) anchor.focus({ preventScroll: true });
    fire(root, "openchange", { open: false });
  }

  const onTrigger = (e) => { e.preventDefault(); root.dataset.state === "open" ? close(true) : open(); };
  for (const t of triggers) t.addEventListener("click", onTrigger);
  const onClose = (e) => { if (e.target instanceof Element && e.target.closest('[data-part="close"]')) close(true); };
  content.addEventListener("click", onClose);
  const unwatch = watchAttr(root, "data-open", (v) => (truthy(v) ? open() : close(false)));
  if (root.hasAttribute("data-open") && truthy(root.getAttribute("data-open"))) open();
  root.pwOpen = open;
  root.pwClose = close;
  return () => {
    unwatch();
    for (const t of triggers) t.removeEventListener("click", onTrigger);
    content.removeEventListener("click", onClose);
    for (const s of stop) s();
  };
}
