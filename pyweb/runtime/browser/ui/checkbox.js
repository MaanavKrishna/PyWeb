// Checkbox: a native checkbox; this only adds the "indeterminate" (mixed) state, which HTML can't
// set from markup: data-indeterminate="true". Clicking it makes it checked, as usual.
import { truthy, watchAttr } from "./core.js";

export default function setup(el) {
  const sync = () => { el.indeterminate = truthy(el.getAttribute("data-indeterminate")); };
  sync();
  return watchAttr(el, "data-indeterminate", sync);
}
