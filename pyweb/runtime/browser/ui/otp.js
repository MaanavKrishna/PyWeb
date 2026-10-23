// InputOTP: one real <input autocomplete="one-time-code"> (so paste, autofill from SMS and screen
// readers just work) drawn as separate boxes. The input fires "complete" when every box is filled.
import { fire } from "./core.js";

export default function setup(root) {
  const input = root.querySelector('[data-part="input"]');
  const slots = Array.from(root.querySelectorAll('[data-part="slot"]'));
  if (!input || !slots.length) return null;
  const allow = root.dataset.pattern === "alphanumeric" ? /[^a-z0-9]/gi : /\D/g;
  const sync = () => {
    const clean = input.value.replace(allow, "").slice(0, slots.length);
    if (clean !== input.value) input.value = clean;
    const focused = document.activeElement === input;
    const at = Math.min(input.selectionStart ?? clean.length, slots.length - 1);
    slots.forEach((s, i) => {
      s.textContent = clean[i] || "";
      s.dataset.state = focused && i === at ? "active" : clean[i] ? "filled" : "empty";
    });
    if (clean.length === slots.length && root.dataset.done !== clean) {
      root.dataset.done = clean;
      fire(input, "complete", { value: clean });
    } else if (clean.length < slots.length) delete root.dataset.done;
  };
  const events = ["input", "focus", "blur", "keyup", "select", "click"];
  for (const ev of events) input.addEventListener(ev, sync);
  document.addEventListener("selectionchange", sync);
  sync();
  return () => { for (const ev of events) input.removeEventListener(ev, sync); document.removeEventListener("selectionchange", sync); };
}
