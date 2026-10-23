// NavigationMenu (WAI-ARIA disclosure navigation, like Radix NavigationMenu): top-level links and
// triggers; a trigger's panel opens on hover (after a short delay) or click, moving between
// triggers swaps panels at once; arrows move along the bar, ArrowDown enters a panel, Escape closes.
import { owner, partsAll, uid } from "./core.js";

export default function setup(root) {
  const items = partsAll(root, "item");
  const tops = () => items.map((it) => it.querySelector('[data-part="trigger"],[data-part="link"]')).filter(Boolean);
  let current = null, openTimer = 0, closeTimer = 0;
  const delay = Number(root.dataset.delay || 150);

  for (const it of items) {
    const t = it.querySelector(':scope > [data-part="trigger"]');
    const c = it.querySelector(':scope > [data-part="content"]');
    if (!t || !c) continue;
    c.id ||= uid("pw-nav");
    t.setAttribute("aria-expanded", "false");
    t.setAttribute("aria-controls", c.id);
    c.hidden = true;
  }

  function show(it) {
    clearTimeout(openTimer); clearTimeout(closeTimer);
    if (current === it) return;
    if (current) hide(current);
    const t = it.querySelector(':scope > [data-part="trigger"]'), c = it.querySelector(':scope > [data-part="content"]');
    if (!t || !c) return;
    const from = current;
    current = it;
    c.hidden = false;
    c.dataset.state = "open";
    c.dataset.motion = from ? (items.indexOf(it) > items.indexOf(from) ? "from-end" : "from-start") : "";
    t.setAttribute("aria-expanded", "true");
    t.dataset.state = "open";
    root.dataset.state = "open";
  }
  function hide(it) {
    const t = it.querySelector(':scope > [data-part="trigger"]'), c = it.querySelector(':scope > [data-part="content"]');
    c.hidden = true;
    c.dataset.state = "closed";
    t.setAttribute("aria-expanded", "false");
    t.dataset.state = "closed";
    if (current === it) { current = null; root.dataset.state = "closed"; }
  }
  const itemOf = (el) => (el instanceof Element ? items.find((it) => it.contains(el)) : null);

  const onOver = (e) => {
    const it = itemOf(e.target);
    if (!it || !it.querySelector(':scope > [data-part="trigger"]')) return;
    clearTimeout(closeTimer);
    if (current) show(it); else { clearTimeout(openTimer); openTimer = setTimeout(() => show(it), delay); }
  };
  const onLeave = () => {
    clearTimeout(openTimer);
    closeTimer = setTimeout(() => current && hide(current), 300);
  };
  const onClick = (e) => {
    const t = e.target instanceof Element ? e.target.closest('[data-part="trigger"]') : null;
    if (t && owner(t, "navigation-menu") === root) { const it = itemOf(t); current === it ? hide(it) : show(it); return; }
    if (e.target instanceof Element && e.target.closest("a[href]") && current) hide(current);
  };
  const onKey = (e) => {
    const list = tops(), i = list.indexOf(document.activeElement);
    if (e.key === "Escape" && current) {
      const t = current.querySelector(':scope > [data-part="trigger"]');
      hide(current); t.focus(); e.preventDefault(); return;
    }
    if (i < 0) return;
    const dir = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (dir) { e.preventDefault(); list[(i + dir + list.length) % list.length].focus(); }
    else if (e.key === "Home" || e.key === "End") { e.preventDefault(); list[e.key === "Home" ? 0 : list.length - 1].focus(); }
    else if (e.key === "ArrowDown" && document.activeElement.dataset.part === "trigger") {
      e.preventDefault();
      const it = itemOf(document.activeElement);
      show(it);
      const first = it.querySelector('[data-part="content"] a[href], [data-part="content"] button');
      if (first) first.focus();
    }
  };
  const onFocusOut = (e) => { if (current && !root.contains(e.relatedTarget)) hide(current); };
  root.addEventListener("pointerover", onOver);
  root.addEventListener("pointerleave", onLeave);
  root.addEventListener("click", onClick);
  root.addEventListener("keydown", onKey);
  root.addEventListener("focusout", onFocusOut);
  return () => {
    clearTimeout(openTimer); clearTimeout(closeTimer);
    root.removeEventListener("pointerover", onOver);
    root.removeEventListener("pointerleave", onLeave);
    root.removeEventListener("click", onClick);
    root.removeEventListener("keydown", onKey);
    root.removeEventListener("focusout", onFocusOut);
  };
}
