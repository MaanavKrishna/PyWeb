// Carousel (WAI-ARIA APG "Carousel"): a scroll-snap track, so touch swipes and trackpads work
// natively. Previous/Next buttons and the arrow keys move one slide; dots jump; data-loop wraps;
// data-autoplay="5000" advances on a timer, paused while hovered or focused (and never with
// reduced motion). Fires "slidechange" with the index.
import { fire, part, partsAll, reducedMotion } from "./core.js";

export default function setup(root) {
  const track = part(root, "content");
  if (!track) return null;
  const vertical = root.dataset.orientation === "vertical";
  const slides = () => partsAll(root, "item");
  const prev = part(root, "previous"), next = part(root, "next"), dots = part(root, "dots");
  const loop = root.dataset.loop !== undefined;
  root.setAttribute("role", "region");
  root.setAttribute("aria-roledescription", "carousel");
  let index = 0, timer = 0, paused = false;

  const at = () => {
    const pos = vertical ? track.scrollTop : track.scrollLeft;
    let best = 0, dist = Infinity;
    slides().forEach((s, i) => {
      const d = Math.abs((vertical ? s.offsetTop - track.offsetTop : s.offsetLeft - track.offsetLeft) - pos);
      if (d < dist) { dist = d; best = i; }
    });
    return best;
  };

  function sync() {
    const all = slides(), i = at();
    all.forEach((s, n) => {
      s.setAttribute("role", "group");
      s.setAttribute("aria-roledescription", "slide");
      s.setAttribute("aria-label", `${n + 1} of ${all.length}`);
      s.dataset.state = n === i ? "active" : "inactive";
    });
    const end = vertical ? track.scrollTop + track.clientHeight >= track.scrollHeight - 2
      : Math.abs(track.scrollLeft) + track.clientWidth >= track.scrollWidth - 2;
    if (prev) prev.disabled = !loop && i === 0;
    if (next) next.disabled = !loop && end;
    if (dots) {
      while (dots.children.length < all.length) {
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.part = "dot";
        dots.appendChild(b);
      }
      while (dots.children.length > all.length) dots.lastChild.remove();
      Array.from(dots.children).forEach((b, n) => {
        b.setAttribute("aria-label", `Go to slide ${n + 1}`);
        b.setAttribute("aria-current", n === i ? "true" : "false");
      });
    }
    if (i !== index) { index = i; fire(root, "slidechange", { index: i }); }
  }

  function go(i) {
    const all = slides();
    if (!all.length) return;
    const n = loop ? (i + all.length) % all.length : Math.max(0, Math.min(i, all.length - 1));
    const s = all[n];
    const behavior = reducedMotion() ? "auto" : "smooth";
    track.scrollTo(vertical ? { top: s.offsetTop - track.offsetTop, behavior } : { left: s.offsetLeft - track.offsetLeft, behavior });
  }
  const step = (d) => {
    const end = vertical ? track.scrollTop + track.clientHeight >= track.scrollHeight - 2
      : Math.abs(track.scrollLeft) + track.clientWidth >= track.scrollWidth - 2;
    go(d > 0 && end && loop ? 0 : at() + d);
  };

  const onClick = (e) => {
    const el = e.target instanceof Element ? e.target.closest("button") : null;
    if (!el) return;
    if (el === prev) step(-1);
    else if (el === next) step(1);
    else if (el.dataset.part === "dot" && dots && dots.contains(el)) go(Array.from(dots.children).indexOf(el));
  };
  const onKey = (e) => {
    const k = vertical ? { ArrowUp: -1, ArrowDown: 1 } : { ArrowLeft: -1, ArrowRight: 1 };
    if (k[e.key] && !(e.target instanceof HTMLInputElement)) { e.preventDefault(); step(k[e.key]); }
  };
  let frame = 0;
  const onScroll = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(sync); };
  root.addEventListener("click", onClick);
  root.addEventListener("keydown", onKey);
  track.addEventListener("scroll", onScroll, { passive: true });
  const auto = Number(root.dataset.autoplay || 0);
  const pause = (on) => () => { paused = on; };
  const enter = pause(true), leave = pause(false);
  if (auto > 0 && !reducedMotion()) {
    timer = setInterval(() => { if (!paused && !document.hidden) step(1); }, auto);
    for (const ev of ["pointerenter", "focusin"]) root.addEventListener(ev, enter);
    for (const ev of ["pointerleave", "focusout"]) root.addEventListener(ev, leave);
  }
  sync();
  root.pwGo = go;
  return () => {
    clearInterval(timer);
    cancelAnimationFrame(frame);
    root.removeEventListener("click", onClick);
    root.removeEventListener("keydown", onKey);
    track.removeEventListener("scroll", onScroll);
    for (const ev of ["pointerenter", "focusin"]) root.removeEventListener(ev, enter);
    for (const ev of ["pointerleave", "focusout"]) root.removeEventListener(ev, leave);
  };
}
