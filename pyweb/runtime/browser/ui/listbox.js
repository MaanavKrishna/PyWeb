// A list whose "current" option moves while focus stays in an input or button
// (aria-activedescendant), as in comboboxes, selects and command palettes.
import { uid } from "./core.js";

export function activeList(list, selector, { onPick } = {}) {
  let active = null;
  const items = () => Array.from(list.querySelectorAll(selector)).filter((el) =>
    !el.hidden && el.getAttribute("aria-disabled") !== "true" && !el.closest("[hidden]"));
  for (const el of list.querySelectorAll(selector)) { el.id ||= uid("pw-opt"); el.setAttribute("role", "option"); }
  function set(el, owner) {
    if (active) active.removeAttribute("data-highlighted");
    active = el || null;
    if (active) {
      active.setAttribute("data-highlighted", "");
      active.scrollIntoView({ block: "nearest" });
    }
    if (owner) {
      if (active) owner.setAttribute("aria-activedescendant", active.id);
      else owner.removeAttribute("aria-activedescendant");
    }
  }
  function move(delta, owner) {
    const list_ = items();
    if (!list_.length) { set(null, owner); return; }
    const i = list_.indexOf(active);
    const next = i < 0 ? (delta > 0 ? 0 : list_.length - 1) : Math.min(Math.max(i + delta, 0), list_.length - 1);
    set(list_[next], owner);
  }
  const onMove = (e) => {
    const el = e.target instanceof Element ? e.target.closest(selector) : null;
    if (el && items().includes(el) && el !== active) set(el, list.__owner);
  };
  const onClick = (e) => {
    const el = e.target instanceof Element ? e.target.closest(selector) : null;
    if (el && items().includes(el) && onPick) onPick(el, e);
  };
  list.addEventListener("pointermove", onMove);
  list.addEventListener("click", onClick);
  return {
    items, set, move,
    get active() { return active; },
    first: (owner) => set(items()[0], owner),
    last: (owner) => set(items().slice(-1)[0], owner),
    stop() { list.removeEventListener("pointermove", onMove); list.removeEventListener("click", onClick); },
  };
}

/** An option's label: data-label, else its text without the check mark and shortcut. */
export function label(el) {
  if (el.dataset.label) return el.dataset.label;
  let out = "";
  for (const n of el.childNodes) {
    if (n.nodeType === 8) continue;                    // the runtime's region markers
    if (n.nodeType === 1 && (n.classList.contains("pw-menu-indicator") || n.dataset.part === "shortcut")) continue;
    out += n.textContent;
  }
  return out.trim();
}

/** How well `text` matches `query` (0: not at all): every query letter in order, bonus for word starts. */
export function score(text, query) {
  text = text.toLowerCase();
  query = query.trim().toLowerCase();
  if (!query) return 1;
  if (text.includes(query)) return 2 + (text.startsWith(query) ? 1 : 0) + 1 / (1 + text.length);
  let ti = 0, s = 0;
  for (const ch of query) {
    const at = text.indexOf(ch, ti);
    if (at < 0) return 0;
    s += at === 0 || " -_/.".includes(text[at - 1]) ? 0.2 : 0.05;
    ti = at + 1;
  }
  return Math.min(s, 1);
}
