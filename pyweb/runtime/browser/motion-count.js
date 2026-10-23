// PyWeb motion: animate="count". When the element's number changes (a live total, a counter),
// it counts from the old number to the new one, keeping the new text's decimals and separators.
import { spring, tween } from "./motion-math.js";


/** animate="count": when the element's number changes, it counts to the new one. */
export function counter(el, api) {
  const { CFG, reduced, now, normAll, state } = api;
  let shown = num(el.textContent), written = null, gen = null, t0 = 0, frame = 0;
  const mo = new MutationObserver(() => {
    const text = el.textContent;
    if (text === written) return;
    const target = num(text);
    if (target == null || shown == null || reduced() || CFG.feel === "none") { shown = target; return; }
    const tnow = now();
    const from = gen ? gen.value(tnow - t0) : shown;
    const v0 = gen ? gen.velocity(tnow - t0) : 0;
    const t = normAll(state(el).spec.transition) || {};
    gen = t.type === "spring" ? spring({ from, to: target, velocity: v0, stiffness: t.stiffness ?? 120, damping: t.damping ?? 20 })
      : tween({ values: [from, target], duration: (t.duration ?? 0.8) / (CFG.speed || 1), ease: t.ease || "ease-out" });
    t0 = tnow;
    shown = target;
    const show = format(text);
    cancelAnimationFrame(frame);
    const tick = () => {
      const x = now() - t0;
      const node = textNode(el);
      if (!node) return;
      written = x >= gen.duration ? text : show(gen.value(x));
      if (node.data !== written) node.data = written;
      if (x < gen.duration) frame = requestAnimationFrame(tick); else gen = null;
    };
    tick();
  });
  mo.observe(el, { characterData: true, childList: true, subtree: true });
}
const NUM = /-?\d[\d,]*(\.\d+)?/;
function num(text) { const m = NUM.exec(text || ""); return m ? parseFloat(m[0].replace(/,/g, "")) : null; }
function format(text) {
  const m = NUM.exec(text);
  const decimals = m && m[1] ? m[1].length - 1 : 0, commas = m && m[0].includes(",");
  return (v) => text.replace(NUM, v.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals, useGrouping: commas }));
}
function textNode(el) {
  const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) if (NUM.test(n.data)) return n;
  return null;
}
