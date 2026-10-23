// SelectMenu (WAI-ARIA APG "Select-Only Combobox"): a button showing the choice, a listbox to pick.
// Closed: arrows, Enter or Space open it; typing picks the next option starting with those letters.
// Open: arrows, Home/End, typing move; Enter/Space/click choose; Escape or Tab close. A hidden input
// carries the value for forms.
import { autoPlace, fire, part, pushLayer, setOpen, uid } from "./core.js";
import { activeList, label } from "./listbox.js";
import { floating } from "./popover.js";

export default function setup(root) {
  const trigger = part(root, "trigger"), content = part(root, "content"), input = part(root, "input");
  const valueEl = part(root, "value");
  if (!trigger || !content) return null;
  const layer = floating(root, content);
  content.id ||= uid("pw-listbox");
  content.setAttribute("role", "listbox");
  trigger.setAttribute("role", "combobox");
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-controls", content.id);
  const list = activeList(content, '[data-part="option"]', { onPick: (el) => { choose(el); close(); } });
  content.__owner = trigger;
  const placeholder = valueEl ? valueEl.textContent : "";
  let stop = [], typed = "", timer = 0;

  // Every option, including while the list is closed (hidden).
  const options = () => Array.from(content.querySelectorAll('[data-part="option"]'))
    .filter((o) => o.getAttribute("aria-disabled") !== "true");
  const selected = () => options().find((o) => o.dataset.value === root.dataset.value);
  function render() {
    const cur = selected();
    for (const o of content.querySelectorAll('[data-part="option"]')) {
      const on = o === cur;
      o.setAttribute("aria-selected", on ? "true" : "false");
      o.dataset.state = on ? "checked" : "unchecked";
    }
    if (valueEl) { valueEl.textContent = cur ? label(cur) : placeholder; valueEl.dataset.placeholder = cur ? "false" : "true"; }
    if (input) input.value = root.dataset.value || "";
  }
  function choose(el) {
    if (!el) return;
    const changed = el.dataset.value !== root.dataset.value;
    root.dataset.value = el.dataset.value;
    render();
    if (changed) {
      fire(root, "valuechange", { value: el.dataset.value });
      if (input) input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }
  function open(at = "selected") {
    if (root.dataset.state === "open") return;
    root.dataset.state = "open";
    layer.show();
    setOpen(content, true);
    trigger.setAttribute("aria-expanded", "true");
    stop = [autoPlace(trigger, content, { side: "bottom", align: "start", offset: 4 }),
            pushLayer({ el: content, keep: [trigger], dismiss: () => close() })];
    if (at === "last") list.last(trigger); else if (at === "first" || !selected()) list.first(trigger); else list.set(selected(), trigger);
  }
  function close() {
    if (root.dataset.state !== "open") return;
    root.dataset.state = "closed";
    for (const s of stop) s();
    stop = [];
    trigger.setAttribute("aria-expanded", "false");
    trigger.removeAttribute("aria-activedescendant");
    setOpen(content, false, () => layer.hide());
    trigger.focus({ preventScroll: true });
  }
  function typeahead(key) {
    clearTimeout(timer);
    typed += key.toLowerCase();
    timer = setTimeout(() => { typed = ""; }, 600);
    const items = options();
    const from = Math.max(0, items.indexOf(root.dataset.state === "open" ? list.active : selected()));
    const order = items.slice(from + (typed.length === 1 ? 1 : 0)).concat(items.slice(0, from + 1));
    return order.find((o) => label(o).toLowerCase().startsWith(typed));
  }
  const onKey = (e) => {
    const isOpen = root.dataset.state === "open";
    if (!isOpen) {
      if (["ArrowDown", "Enter", " "].includes(e.key)) { e.preventDefault(); open(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); open("selected"); }
      else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { const hit = typeahead(e.key); if (hit) choose(hit); }
      return;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); list.move(1, trigger); }
    else if (e.key === "ArrowUp") { e.preventDefault(); list.move(-1, trigger); }
    else if (e.key === "Home") { e.preventDefault(); list.first(trigger); }
    else if (e.key === "End") { e.preventDefault(); list.last(trigger); }
    else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(list.active); close(); }
    else if (e.key === "Tab") { choose(list.active); close(); }
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { const hit = typeahead(e.key); if (hit) list.set(hit, trigger); }
  };
  const onClick = (e) => { e.preventDefault(); root.dataset.state === "open" ? close() : open(); };
  trigger.addEventListener("keydown", onKey);
  trigger.addEventListener("click", onClick);
  render();
  return () => {
    for (const s of stop) s();
    list.stop();
    trigger.removeEventListener("keydown", onKey);
    trigger.removeEventListener("click", onClick);
  };
}
