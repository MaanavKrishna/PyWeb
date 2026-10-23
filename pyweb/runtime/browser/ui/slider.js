// Slider: a native range input (keyboard, forms and screen readers come from the browser). This
// keeps the filled part of the track (--pw-fill) and the value readout (data-part="value") in step.
export default function setup(root) {
  const input = root.matches("input") ? root : root.querySelector('input[type="range"]');
  if (!input) return null;
  const out = root.querySelector('[data-part="value"]');
  const sync = () => {
    const min = Number(input.min || 0), max = Number(input.max || 100);
    const pct = max > min ? ((Number(input.value) - min) / (max - min)) * 100 : 0;
    root.style.setProperty("--pw-fill", `${pct}%`);
    if (out) out.textContent = input.value;
  };
  sync();
  input.addEventListener("input", sync);
  // Programmatic changes (a controlled value={...}) don't fire "input".
  const mo = new MutationObserver(sync);
  mo.observe(input, { attributes: true, attributeFilter: ["value", "min", "max"] });
  return () => { input.removeEventListener("input", sync); mo.disconnect(); };
}
