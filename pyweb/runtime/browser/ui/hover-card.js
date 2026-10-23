// HoverCard: a preview card for sighted pointer users, opened by hovering (or focusing) a link.
// The link itself stays the way to the content, so nothing is lost without a pointer.
import { autoPlace, part, uid } from "./core.js";
import { floating } from "./popover.js";

export default function setup(root) {
  const trigger = part(root, "trigger");
  const content = part(root, "content");
  if (!trigger || !content) return null;
  const layer = floating(root, content);
  content.id ||= uid("pw-hovercard");
  const openDelay = Number(root.dataset.openDelay || 600), closeDelay = Number(root.dataset.closeDelay || 250);
  let openTimer = 0, closeTimer = 0, stop = null;
  function open() {
    if (root.dataset.state === "open") return;
    root.dataset.state = "open";
    content.dataset.state = "open";
    layer.show();
    stop = autoPlace(trigger, content, { side: root.dataset.side || "bottom", align: root.dataset.align || "center", offset: 8 });
  }
  function close() {
    if (root.dataset.state !== "open") return;
    root.dataset.state = "closed";
    content.dataset.state = "closed";
    if (stop) stop();
    layer.hide();
  }
  const enter = () => { clearTimeout(closeTimer); openTimer = setTimeout(open, openDelay); };
  const leave = () => { clearTimeout(openTimer); closeTimer = setTimeout(close, closeDelay); };
  for (const el of [trigger, content]) {
    el.addEventListener("pointerenter", enter);
    el.addEventListener("pointerleave", leave);
  }
  trigger.addEventListener("focus", enter);
  trigger.addEventListener("blur", leave);
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  return () => { close(); document.removeEventListener("keydown", onKey); };
}
