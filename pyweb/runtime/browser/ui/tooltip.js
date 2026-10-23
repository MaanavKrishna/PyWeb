// Tooltip (WAI-ARIA APG "Tooltip"): shows on hover (after a short delay) and on keyboard focus,
// hides on Escape, blur or leaving; the pointer can move onto it without it closing (WCAG 1.4.13).
// Once one tooltip has shown, the next one opens without the delay.
import { part, place, uid } from "./core.js";
import { floating } from "./popover.js";

let warm = 0;

export default function setup(root) {
  const trigger = part(root, "trigger") || root.firstElementChild;
  const content = part(root, "content");
  if (!trigger || !content) return null;
  const layer = floating(root, content);
  content.id ||= uid("pw-tooltip");
  content.setAttribute("role", "tooltip");
  trigger.setAttribute("aria-describedby", content.id);
  const delay = Number(root.dataset.delay || 500);
  let showTimer = 0, hideTimer = 0;

  function show() {
    clearTimeout(hideTimer);
    if (root.dataset.state === "open") return;
    root.dataset.state = "open";
    content.dataset.state = "open";
    layer.show();
    place(trigger, content, { side: root.dataset.side || "top", align: root.dataset.align || "center", offset: 6 });
    document.addEventListener("keydown", onKey, true);
  }
  function hide() {
    clearTimeout(showTimer);
    if (root.dataset.state !== "open") return;
    root.dataset.state = "closed";
    content.dataset.state = "closed";
    layer.hide();
    warm = Date.now();
    document.removeEventListener("keydown", onKey, true);
  }
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); hide(); } };
  const enter = () => {
    clearTimeout(hideTimer);
    clearTimeout(showTimer);
    showTimer = setTimeout(show, Date.now() - warm < 600 ? 0 : delay);
  };
  const leave = () => { clearTimeout(showTimer); hideTimer = setTimeout(hide, 100); };
  const onFocus = () => { if (trigger.matches(":focus-visible")) show(); };
  trigger.addEventListener("pointerenter", enter);
  trigger.addEventListener("pointerleave", leave);
  trigger.addEventListener("focus", onFocus);
  trigger.addEventListener("blur", hide);
  trigger.addEventListener("pointerdown", hide);
  content.addEventListener("pointerenter", () => clearTimeout(hideTimer));
  content.addEventListener("pointerleave", leave);
  return () => {
    hide();
    trigger.removeEventListener("pointerenter", enter);
    trigger.removeEventListener("pointerleave", leave);
    trigger.removeEventListener("focus", onFocus);
    trigger.removeEventListener("blur", hide);
    trigger.removeEventListener("pointerdown", hide);
  };
}
