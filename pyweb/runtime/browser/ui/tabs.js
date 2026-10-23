// Tabs (WAI-ARIA APG "Tabs"): arrow keys move between tabs and show them (data-activation="manual":
// Enter/Space shows), Home/End jump; each panel is labelled by its tab. A controlled value comes in
// through data-value; changes go out as a "valuechange" event. The underline follows the tab.
import { fire, partsAll, roving, uid, watchAttr } from "./core.js";

export default function setup(root) {
  const list = partsAll(root, "list")[0];
  if (!list) return null;
  const tabs = () => partsAll(root, "trigger");
  const panels = () => partsAll(root, "content");
  const vertical = root.dataset.orientation === "vertical";
  const manual = root.dataset.activation === "manual";
  list.setAttribute("role", "tablist");
  if (vertical) list.setAttribute("aria-orientation", "vertical");

  function indicator() {
    const t = tabs().find((x) => x.dataset.state === "active");
    if (!t) return;
    list.style.setProperty("--pw-tab-x", `${t.offsetLeft}px`);
    list.style.setProperty("--pw-tab-y", `${t.offsetTop}px`);
    list.style.setProperty("--pw-tab-w", `${t.offsetWidth}px`);
    list.style.setProperty("--pw-tab-h", `${t.offsetHeight}px`);
  }

  function show(value) {
    const all = tabs();
    for (const t of all) {
      const on = t.dataset.value === value;
      t.setAttribute("role", "tab");
      t.id ||= uid("pw-tab");
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.dataset.state = on ? "active" : "inactive";
      t.tabIndex = on ? 0 : -1;
    }
    for (const p of panels()) {
      const on = p.dataset.value === value;
      const t = all.find((x) => x.dataset.value === p.dataset.value);
      p.setAttribute("role", "tabpanel");
      p.id ||= uid("pw-tabpanel");
      if (t) { t.setAttribute("aria-controls", p.id); p.setAttribute("aria-labelledby", t.id); }
      p.dataset.state = on ? "active" : "inactive";
      p.hidden = !on;
      if (!p.hasAttribute("tabindex")) p.tabIndex = 0;
    }
    if (root.dataset.value !== value) root.dataset.value = value;
    indicator();
  }

  function select(value) {
    if (value == null || value === root.dataset.value) return;
    if (!fire(root, "valuechange", { value })) return;     // event.preventDefault() keeps the old tab
    if (root.dataset.controlled === undefined) show(value);
  }

  const first = tabs().find((t) => !t.disabled);
  show(root.dataset.value || root.dataset.default || (first && first.dataset.value));
  root.dataset.orientation = vertical ? "vertical" : "horizontal";

  const nav = roving(list, '[data-part="trigger"]', {
    orientation: vertical ? "vertical" : "horizontal",
    onMove: (el) => { if (!manual) select(el.dataset.value); },
    activate: (el) => select(el.dataset.value),
  });
  const onClick = (e) => {
    const t = e.target instanceof Element ? e.target.closest('[data-part="trigger"]') : null;
    if (t && list.contains(t) && !t.disabled) select(t.dataset.value);
  };
  list.addEventListener("click", onClick);
  const unwatch = watchAttr(root, "data-value", (v) => { if (v) show(v); });
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(indicator) : null;
  if (ro) ro.observe(list);
  root.pwSelect = select;
  return () => { nav.stop(); unwatch(); if (ro) ro.disconnect(); list.removeEventListener("click", onClick); };
}
