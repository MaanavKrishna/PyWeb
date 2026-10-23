// Collapsible: one <details> section that opens and closes with its height animated. A controlled
// open state comes in through data-open; changes go out as an "openchange" event.
import { animateDetails, disclosure } from "./accordion.js";
import { fire, truthy, watchAttr } from "./core.js";

export default function setup(root) {
  const stop = disclosure(root, (_d, open) => fire(root, "openchange", { open }));
  const unwatch = watchAttr(root, "data-open", (v) => {
    if ((root.dataset.state === "open") !== truthy(v)) animateDetails(root, truthy(v));
  });
  return () => { stop(); unwatch(); };
}
