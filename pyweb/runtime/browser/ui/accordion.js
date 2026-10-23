// Accordion (WAI-ARIA APG "Accordion") on native <details>/<summary>: works without scripts, and
// find-in-page opens a closed section. This module adds the height animation, one-open-at-a-time
// (data-type="single"), arrow keys between headers, and a "valuechange" event.
import { fire, partsAll, reducedMotion, watchAttr } from "./core.js";

const running = new WeakMap();

function duration(el) {
  const v = getComputedStyle(el).getPropertyValue("--pw-duration-collapse").trim();
  return parseFloat(v) || 220;
}

/** Open or close a <details> with its content's height animated (interruptible). */
export function animateDetails(details, open) {
  const content = details.querySelector(':scope > [data-part="content"]');
  const mark = (on) => {
    details.dataset.state = on ? "open" : "closed";
    if (content) content.dataset.state = details.dataset.state;
  };
  const prev = running.get(details);
  const from = content ? content.getBoundingClientRect().height : 0;
  if (prev) prev.cancel();
  if (!content || reducedMotion() || typeof content.animate !== "function") {
    details.open = open; mark(open); return;
  }
  if (open) details.open = true;
  mark(open);
  const to = open ? content.scrollHeight : 0;
  const anim = content.animate([{ height: `${prev || !open ? from : 0}px`, overflow: "hidden" },
    { height: `${to}px`, overflow: "hidden" }], { duration: duration(details), easing: "cubic-bezier(.2,.8,.2,1)" });
  running.set(details, anim);
  anim.onfinish = () => {
    running.delete(details);
    if (!open) details.open = false;
  };
  anim.oncancel = () => running.delete(details);
}

export function disclosure(details, onToggle) {
  const summary = details.querySelector(":scope > summary");
  if (!summary) return () => {};
  details.dataset.state = details.open ? "open" : "closed";
  if (details.hasAttribute("data-disabled")) summary.setAttribute("aria-disabled", "true");
  const onClick = (e) => {
    e.preventDefault();
    if (details.hasAttribute("data-disabled") || summary.getAttribute("aria-disabled") === "true") return;
    const open = details.dataset.state !== "open";
    if (onToggle && onToggle(details, open) === false) return;
    animateDetails(details, open);
  };
  // Find-in-page (and anything else) can open it natively: keep the state in step.
  const onNative = () => { if (!running.has(details)) details.dataset.state = details.open ? "open" : "closed"; };
  summary.addEventListener("click", onClick);
  details.addEventListener("toggle", onNative);
  return () => { summary.removeEventListener("click", onClick); details.removeEventListener("toggle", onNative); };
}

export default function setup(root) {
  const single = root.dataset.type !== "multiple";
  const items = () => partsAll(root, "item");
  const value = () => items().filter((d) => d.dataset.state === "open").map((d) => d.dataset.value);
  const want = (root.dataset.value ?? root.dataset.default ?? "").split(",").filter(Boolean);
  if (want.length) for (const d of items()) { d.open = want.includes(d.dataset.value); }

  const stops = items().map((d) => disclosure(d, (details, open) => {
    if (single && open) for (const other of items()) if (other !== details && other.dataset.state === "open") animateDetails(other, false);
    if (single && !open && root.dataset.collapsible === "false") return false;
    queueMicrotask(() => fire(root, "valuechange", { value: single ? (value()[0] ?? null) : value() }));
    return true;
  }));
  const onKey = (e) => {
    const heads = items().map((d) => d.querySelector(":scope > summary")).filter(Boolean);
    const i = heads.indexOf(document.activeElement);
    if (i < 0) return;
    const go = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: heads.length - 1 }[e.key];
    if (go === undefined) return;
    e.preventDefault();
    heads[(go + heads.length) % heads.length].focus();
  };
  root.addEventListener("keydown", onKey);
  const unwatch = watchAttr(root, "data-value", (v) => {
    const open = (v || "").split(",");
    for (const d of items()) {
      const on = open.includes(d.dataset.value);
      if ((d.dataset.state === "open") !== on) animateDetails(d, on);
    }
  });
  return () => { for (const s of stops) s(); unwatch(); root.removeEventListener("keydown", onKey); };
}
