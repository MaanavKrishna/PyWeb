// Combobox (WAI-ARIA APG "Editable Combobox With List Autocomplete"): type to filter the options,
// arrows to move, Enter to choose, Escape to close (twice: clear). Options can also come from the
// server: listen to `oninput` and render new options; this module filters only data-filter="client".
import { autoPlace, fire, part, pushLayer, setOpen, uid } from "./core.js";
import { activeList, label, score } from "./listbox.js";
import { floating } from "./popover.js";

export default function setup(root) {
  const input = part(root, "input"), content = part(root, "content"), hidden = part(root, "value-input");
  const empty = part(root, "empty");
  if (!input || !content) return null;
  const layer = floating(root, content);
  content.id ||= uid("pw-listbox");
  content.setAttribute("role", "listbox");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", content.id);
  input.setAttribute("autocomplete", "off");
  const list = activeList(content, '[data-part="option"]', { onPick: (el) => choose(el) });
  content.__owner = input;
  const clientFilter = root.dataset.filter !== "server";
  let stop = [];

  function filter() {
    if (!clientFilter) { if (empty) empty.hidden = list.items().length > 0; return; }
    const q = input.value;
    let shown = 0;
    for (const o of content.querySelectorAll('[data-part="option"]')) {
      const ok = score(label(o), q) > 0;
      o.hidden = !ok;
      if (ok) shown++;
    }
    if (empty) empty.hidden = shown > 0;
  }
  function open() {
    if (root.dataset.state === "open") return;
    root.dataset.state = "open";
    layer.show();
    setOpen(content, true);
    input.setAttribute("aria-expanded", "true");
    stop = [autoPlace(input, content, { side: "bottom", align: "start", offset: 4 }),
            pushLayer({ el: content, keep: [input], dismiss: () => close() })];
  }
  function close() {
    if (root.dataset.state !== "open") return;
    root.dataset.state = "closed";
    for (const s of stop) s();
    stop = [];
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    list.set(null);
    setOpen(content, false, () => layer.hide());
  }
  function choose(el) {
    if (!el) return;
    input.value = label(el);
    root.dataset.value = el.dataset.value;
    if (hidden) hidden.value = el.dataset.value;
    for (const o of content.querySelectorAll('[data-part="option"]')) o.setAttribute("aria-selected", o === el ? "true" : "false");
    close();
    fire(root, "valuechange", { value: el.dataset.value });
    input.focus();
  }
  const onInput = () => { open(); filter(); list.first(input); };
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (root.dataset.state !== "open") { open(); filter(); } list.move(1, input); }
    else if (e.key === "ArrowUp") { e.preventDefault(); if (root.dataset.state !== "open") { open(); filter(); } list.move(-1, input); }
    else if (e.key === "Enter" && root.dataset.state === "open" && list.active) { e.preventDefault(); choose(list.active); }
    else if (e.key === "Escape") {
      if (root.dataset.state === "open") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (input.value) { e.preventDefault(); input.value = ""; filter(); }
    } else if (e.key === "Tab") close();
  };
  input.addEventListener("input", onInput);
  input.addEventListener("keydown", onKey);
  // Options rendered later (from the server, as the user types) are wired up too.
  const mo = new MutationObserver(() => {
    for (const el of content.querySelectorAll('[data-part="option"]:not([role])')) { el.id ||= uid("pw-opt"); el.setAttribute("role", "option"); }
    filter();
    if (root.dataset.state === "open" && !list.items().includes(list.active)) list.first(input);
  });
  mo.observe(content, { childList: true, subtree: true });
  return () => {
    mo.disconnect();
    for (const s of stop) s();
    list.stop();
    input.removeEventListener("input", onInput);
    input.removeEventListener("keydown", onKey);
  };
}
