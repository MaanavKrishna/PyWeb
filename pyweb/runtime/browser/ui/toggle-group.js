// ToggleGroup: a row of toggles. data-type="single" works like radio buttons (role=radio, arrow
// keys move and choose); "multiple" like checkboxes (aria-pressed). Fires "valuechange" with the
// value (single) or a list (multiple); a hidden input carries it in forms.
import { fire, partsAll, roving, watchAttr } from "./core.js";

export default function setup(root) {
  const single = root.dataset.type !== "multiple";
  const items = () => partsAll(root, "item");
  const input = root.querySelector(':scope > input[type="hidden"]');
  root.setAttribute("role", single ? "radiogroup" : "group");
  const current = () => (root.dataset.value || "").split(",").filter(Boolean);

  function show(values) {
    for (const el of items()) {
      const on = values.includes(el.dataset.value);
      if (single) { el.setAttribute("role", "radio"); el.setAttribute("aria-checked", on ? "true" : "false"); }
      else el.setAttribute("aria-pressed", on ? "true" : "false");
      el.dataset.state = on ? "on" : "off";
    }
    const v = values.join(",");
    if (root.dataset.value !== v) root.dataset.value = v;
    if (input) input.value = v;
  }

  function toggle(el) {
    if (el.disabled) return;
    const v = el.dataset.value, now = current();
    const next = single ? (now.includes(v) && root.dataset.required === undefined ? [] : [v])
      : now.includes(v) ? now.filter((x) => x !== v) : [...now, v];
    if (!fire(root, "valuechange", { value: single ? (next[0] ?? null) : next })) return;
    if (root.dataset.controlled === undefined) show(next);
  }

  show(current());
  const vertical = root.dataset.orientation === "vertical";
  const nav = roving(root, '[data-part="item"]', { orientation: vertical ? "vertical" : "horizontal",
    onMove: (el) => { if (single && root.dataset.value) toggle(el); } });
  const onClick = (e) => {
    const el = e.target instanceof Element ? e.target.closest('[data-part="item"]') : null;
    if (el && items().includes(el)) toggle(el);
  };
  root.addEventListener("click", onClick);
  const unwatch = watchAttr(root, "data-value", () => show(current()));
  return () => { nav.stop(); unwatch(); root.removeEventListener("click", onClick); };
}
