// Menus (WAI-ARIA APG "Menu Button" and "Menu"): DropdownMenu; also used by ContextMenu and Menubar.
// Keyboard: Enter/Space/ArrowDown open on the first item, ArrowUp on the last; arrows, Home/End and
// typing move; Enter/Space choose; ArrowRight/Left open and close submenus; Escape closes (focus back
// on the trigger); Tab closes. Checkbox and radio items keep aria-checked up to date.
import { autoPlace, fire, part, partsAll, place, pushLayer, roving, setOpen, uid } from "./core.js";
import { floating } from "./popover.js";

const ITEM = '[data-part="item"],[data-part="checkbox-item"],[data-part="radio-item"],[data-part="sub-trigger"]';

/** Behaviour for one menu panel (`content`). `opts.onClose(reason)` closes it. */
export function menuPanel(content, opts) {
  const own = (el) => el.closest('[data-part="content"],[data-part="sub-content"]') === content;
  content.setAttribute("role", "menu");
  content.id ||= uid("pw-menu");
  for (const el of content.querySelectorAll(ITEM)) {
    if (!own(el)) continue;
    const kind = el.dataset.part;
    el.setAttribute("role", kind === "checkbox-item" ? "menuitemcheckbox" : kind === "radio-item" ? "menuitemradio" : "menuitem");
    if (kind === "checkbox-item" || kind === "radio-item") el.setAttribute("aria-checked", el.dataset.checked === "true" ? "true" : "false");
    el.tabIndex = -1;
  }
  // Enter/Space click the item: its own onclick runs, and the click below does the rest (once).
  const nav = roving(content, ITEM, { activate: (el) => el.click() });
  let sub = null;

  function choose(el) {
    const kind = el.dataset.part;
    if (kind === "sub-trigger") { openSub(el, true); return; }
    if (kind === "checkbox-item") {
      const on = el.getAttribute("aria-checked") !== "true";
      el.setAttribute("aria-checked", on ? "true" : "false");
      el.dataset.state = on ? "checked" : "unchecked";
      fire(el, "checkedchange", { checked: on });
    } else if (kind === "radio-item") {
      const group = el.closest('[data-part="radio-group"]') || content;
      for (const r of group.querySelectorAll('[data-part="radio-item"]')) {
        r.setAttribute("aria-checked", r === el ? "true" : "false");
        r.dataset.state = r === el ? "checked" : "unchecked";
      }
      fire(group, "valuechange", { value: el.dataset.value });
    }
    if (el.dataset.keepOpen === undefined) opts.onClose("select");
  }

  function openSub(trigger, focusFirst) {
    const panel = trigger.parentElement.querySelector(':scope > [data-part="sub-content"]');
    if (!panel || sub?.panel === panel) { if (focusFirst && sub) sub.nav.first(); return; }
    closeSub();
    const layer = floating(trigger, panel);
    layer.show();
    setOpen(panel, true);
    trigger.setAttribute("aria-expanded", "true");
    place(trigger, panel, { side: "right", align: "start", offset: -2 });
    const inner = menuPanel(panel, { parent: true, onClose: (reason) => { closeSub(); if (reason === "select") opts.onClose("select"); else trigger.focus(); } });
    sub = { panel, trigger, layer, nav: inner };
    if (focusFirst) requestAnimationFrame(() => inner.first());
  }

  function closeSub() {
    if (!sub) return;
    const { panel, trigger, layer, nav: inner } = sub;
    sub = null;
    inner.stop();
    trigger.setAttribute("aria-expanded", "false");
    setOpen(panel, false, () => layer.hide());
  }

  const onKey = (e) => {
    const el = document.activeElement;
    if (!el || !own(el)) return;
    if (e.key === "ArrowRight" && el.dataset.part === "sub-trigger") { e.preventDefault(); openSub(el, true); }
    else if (e.key === "ArrowLeft" && opts.parent) { e.preventDefault(); opts.onClose("back"); }
    else if (e.key === "Tab") { opts.onClose("tab"); }
  };
  const onClick = (e) => {
    const el = e.target instanceof Element ? e.target.closest(ITEM) : null;
    if (!el || !own(el) || el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") return;
    choose(el);
  };
  const onMove = (e) => {
    const el = e.target instanceof Element ? e.target.closest(ITEM) : null;
    if (!el || !own(el)) return;
    if (el.dataset.part === "sub-trigger") openSub(el, false); else closeSub();
    if (document.activeElement !== el) nav.focus(el);
  };
  content.addEventListener("keydown", onKey);
  content.addEventListener("click", onClick);
  content.addEventListener("pointermove", onMove);
  return {
    first: nav.first, last: nav.last, focus: nav.focus,
    stop() {
      closeSub();
      nav.stop();
      content.removeEventListener("keydown", onKey);
      content.removeEventListener("click", onClick);
      content.removeEventListener("pointermove", onMove);
    },
  };
}

/** Open/close plumbing shared by DropdownMenu and ContextMenu. */
export function menuRoot(root, { anchorFor, point } = {}) {
  const content = part(root, "content");
  const layer = floating(root, content);
  let panel = null, stops = [], lastTrigger = null;

  function open({ focus = "first", trigger, at } = {}) {
    if (root.dataset.state === "open") { if (focus === "first" && panel) panel.first(); return; }
    root.dataset.state = "open";
    lastTrigger = trigger || null;
    layer.show();
    setOpen(content, true);
    if (trigger) trigger.setAttribute("aria-expanded", "true");
    panel = menuPanel(content, { onClose: (reason) => close(reason !== "tab") });
    const opts = { side: root.dataset.side || "bottom", align: root.dataset.align || "start", offset: 4, point: at };
    stops = [at ? (place(null, content, opts), () => {}) : autoPlace(anchorFor(trigger), content, opts),
             pushLayer({ el: content, keep: trigger ? [trigger] : [], dismiss: () => close(true) })];
    requestAnimationFrame(() => (focus === "last" ? panel.last() : focus === "first" ? panel.first() : content.focus()));
    fire(root, "openchange", { open: true });
  }

  function close(returnFocus = true) {
    if (root.dataset.state !== "open") return;
    root.dataset.state = "closed";
    for (const s of stops) s();
    stops = [];
    if (panel) panel.stop();
    panel = null;
    if (lastTrigger) lastTrigger.setAttribute("aria-expanded", "false");
    setOpen(content, false, () => layer.hide());
    if (returnFocus && lastTrigger) lastTrigger.focus({ preventScroll: true });
    fire(root, "openchange", { open: false });
  }
  return { content, open, close, isOpen: () => root.dataset.state === "open" };
}

export default function setup(root) {
  const triggers = partsAll(root, "trigger");
  const content = part(root, "content");
  if (!content || !triggers.length) return null;
  const menu = menuRoot(root, { anchorFor: (t) => t || triggers[0] });
  content.tabIndex = -1;
  for (const t of triggers) {
    t.setAttribute("aria-haspopup", "menu");
    t.setAttribute("aria-expanded", "false");
    t.setAttribute("aria-controls", content.id || (content.id = uid("pw-menu")));
  }
  const onClick = (e) => {
    const t = e.currentTarget;
    e.preventDefault();
    menu.isOpen() ? menu.close() : menu.open({ trigger: t, focus: e.detail === 0 ? "first" : "content" });
  };
  const onKey = (e) => {
    const t = e.currentTarget;
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") { e.preventDefault(); menu.open({ trigger: t, focus: "first" }); }
    else if (e.key === "ArrowUp") { e.preventDefault(); menu.open({ trigger: t, focus: "last" }); }
  };
  for (const t of triggers) { t.addEventListener("click", onClick); t.addEventListener("keydown", onKey); }
  root.pwOpen = () => menu.open({ trigger: triggers[0] });
  root.pwClose = () => menu.close();
  return () => {
    menu.close(false);
    for (const t of triggers) { t.removeEventListener("click", onClick); t.removeEventListener("keydown", onKey); }
  };
}
