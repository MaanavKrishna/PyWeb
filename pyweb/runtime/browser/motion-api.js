// PyWeb motion for browser code: from pyweb.motion import animate, timeline, stagger,
// scroll_progress, use_transform.
import { computed, signal } from "./runtime.js";
import { stagger as staggerFn } from "./motion-math.js";
import { animateTo, CFG, normAll, stop } from "./motion.js";

function targets(t) {
  if (typeof t === "string") return Array.from(document.querySelectorAll(t));
  if (t instanceof Element) return [t];
  return Array.from(t || []);
}

/** animate(element or selector, {"x": 100}, {"type": "spring", "delay": stagger(0.05)}) */
export function animate(target, keyframes, options = {}) {
  const els = targets(target);
  const o = normAll(options) || {};
  const ps = els.map((el, i) => animateTo(el, keyframes, { ...o, delay: typeof o.delay === "function" ? o.delay(i, els.length) : o.delay }));
  const finished = Promise.all(ps);
  return { finished, then: (a, b) => finished.then(a, b), stop: () => els.forEach((el) => stop(el)) };
}

/** timeline([[target, keyframes, options], ...]): one after another ({"at": seconds} to place one). */
export function timeline(steps) {
  let chain = Promise.resolve();
  const all = [];
  for (const [target, keyframes, options = {}] of steps) {
    const o = normAll(options) || {};
    if (o.at !== undefined) {
      all.push(new Promise((ok) => setTimeout(ok, o.at * 1000 / (CFG.speed || 1))).then(() => animate(target, keyframes, o).finished));
    } else {
      chain = chain.then(() => animate(target, keyframes, o).finished);
      all.push(chain);
    }
  }
  const finished = Promise.all(all);
  return { finished, then: (a, b) => finished.then(a, b) };
}

export const stagger = staggerFn;

/** How far the page (or `target`, an element that scrolls) has scrolled: 0 to 1, as a signal. */
export function scroll_progress(target) {
  const el = typeof target === "string" ? document.querySelector(target) : target;
  const s = signal(0);
  let frame = 0;
  const read = () => {
    frame = 0;
    const box = el || document.documentElement;
    const max = box.scrollHeight - box.clientHeight;
    s(max > 0 ? Math.min(1, Math.max(0, box.scrollTop / max)) : 0);
  };
  (el || window).addEventListener("scroll", () => { if (!frame) frame = requestAnimationFrame(read); }, { passive: true });
  read();
  return () => s();
}

/** use_transform(progress, [0, 1], [0, 200]): a value mapped from one range to another. */
export function use_transform(source, input, output, options = {}) {
  const clamp = options.clamp !== false;
  return computed(() => {
    const v = typeof source === "function" ? source() : source;
    let i = 1;
    while (i < input.length - 1 && v > input[i]) i++;
    let p = (v - input[i - 1]) / (input[i] - input[i - 1] || 1);
    if (clamp) p = Math.min(1, Math.max(0, p));
    return output[i - 1] + (output[i] - output[i - 1]) * p;
  });
}
