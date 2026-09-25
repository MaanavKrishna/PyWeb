import { sig, computed, bind_text, bind_input, on, rpc, mutate } from './runtime.js';

export const count = sig(0); // pyweb:count
export function increment(e) { count(count() + 1) } // pyweb-line:12
window.__pyweb_scope = { count, increment };
export function mount(root=document) {
  // static — no bindings
  on(root, 'click', increment); // line 20
  bind_text(root, "count", () => (count())); // line 21
}
