// Command palette (like cmdk): a search box over a list of commands, ranked as you type, run with
// Enter or a click. Inside a CommandDialog it opens with its shortcut (default ⌘K / Ctrl+K).
import { fire, part } from "./core.js";
import { activeList, label, score } from "./listbox.js";

export default function setup(root) {
  const input = part(root, "input"), list = part(root, "list"), empty = part(root, "empty");
  if (!input || !list) return null;
  list.setAttribute("role", "listbox");
  list.id ||= `pw-cmd-${Math.random().toString(36).slice(2, 8)}`;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "true");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("autocomplete", "off");
  const nav = activeList(list, '[data-part="item"]', { onPick: (el) => run(el) });
  list.__owner = input;

  // A click runs a command (its own onclick, then "select"); Enter clicks the current one.
  function run(el) {
    if (!el) return;
    fire(el, "select", { value: el.dataset.value || label(el) });
    const dlg = root.closest('[data-pw-ui="dialog"]');
    if (dlg && dlg.pwClose && el.dataset.keepOpen === undefined) dlg.pwClose();
  }
  function filter() {
    const q = input.value;
    const items = Array.from(list.querySelectorAll('[data-part="item"]'));
    const ranked = items.map((el) => [el, score(`${label(el)} ${el.dataset.keywords || ""}`, q)]);
    for (const [el, s] of ranked) el.hidden = s <= 0;
    // Best matches first within each group; empty groups disappear.
    for (const group of list.querySelectorAll('[data-part="group"]')) {
      const mine = ranked.filter(([el]) => el.parentElement === group || el.closest('[data-part="group"]') === group);
      if (q.trim()) mine.sort((a, b) => b[1] - a[1]).forEach(([el]) => el.parentElement.appendChild(el));
      group.hidden = !mine.some(([el]) => !el.hidden);
    }
    if (empty) empty.hidden = ranked.some(([el]) => !el.hidden);
    nav.first(input);
  }
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); nav.move(1, input); }
    else if (e.key === "ArrowUp") { e.preventDefault(); nav.move(-1, input); }
    else if (e.key === "Home" && e.ctrlKey) { e.preventDefault(); nav.first(input); }
    else if (e.key === "End" && e.ctrlKey) { e.preventDefault(); nav.last(input); }
    else if (e.key === "Enter") { e.preventDefault(); if (nav.active) nav.active.click(); }
  };
  input.addEventListener("input", filter);
  input.addEventListener("keydown", onKey);
  filter();

  // The palette's dialog opens with its keyboard shortcut.
  const dlg = root.closest('[data-pw-ui="dialog"]');
  const shortcut = (dlg && dlg.dataset.shortcut) || "";
  const onGlobal = (e) => {
    if (!shortcut) return;
    const want = shortcut.toLowerCase();
    if (e.key.toLowerCase() === want && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (!dlg.pwOpen) return;
      if (dlg.dataset.state === "open") dlg.pwClose(); else { input.value = ""; filter(); dlg.pwOpen(); }
    }
  };
  document.addEventListener("keydown", onGlobal);
  const onOpen = (e) => { if (e.detail && e.detail.open) requestAnimationFrame(() => { input.focus(); filter(); }); };
  if (dlg) dlg.addEventListener("openchange", onOpen);
  return () => {
    nav.stop();
    input.removeEventListener("input", filter);
    input.removeEventListener("keydown", onKey);
    document.removeEventListener("keydown", onGlobal);
    if (dlg) dlg.removeEventListener("openchange", onOpen);
  };
}
