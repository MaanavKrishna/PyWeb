// Menubar (WAI-ARIA APG "Menubar"): a row of menus; ArrowLeft/Right move along the bar (and switch
// the open menu), ArrowDown/Enter/Space open one.
import { roving } from "./core.js";
import { menuRoot } from "./menu.js";

export default function setup(bar) {
  bar.setAttribute("role", "menubar");
  const menus = Array.from(bar.querySelectorAll('[data-part="menu"]')).map((root) => {
    root.dataset.pwUi = "menubar-menu";       // its parts belong to it (core.part)
    const trigger = root.querySelector('[data-part="trigger"]');
    trigger.setAttribute("role", "menuitem");
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    return { root, trigger, menu: menuRoot(root, { anchorFor: () => trigger }) };
  });
  const nav = roving(bar, ':scope > [data-part="menu"] > [data-part="trigger"]', { orientation: "horizontal" });
  const openOf = () => menus.find((m) => m.menu.isOpen());
  const onKey = (e) => {
    // From the key's own target: the bar's arrow keys may already have moved focus along.
    const t = e.target instanceof Node ? e.target : null;
    const m = t && menus.find((x) => x.trigger === t || x.menu.content.contains(t));
    if (!m) return;
    const i = menus.indexOf(m);
    if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && (m.menu.isOpen() || e.target === m.trigger)) {
      const wasOpen = m.menu.isOpen();
      if (wasOpen && !(e.target instanceof Element && e.target.closest('[data-part="sub-trigger"]') && e.key === "ArrowRight")) {
        e.preventDefault();
        m.menu.close(false);
        const next = menus[(i + (e.key === "ArrowRight" ? 1 : menus.length - 1)) % menus.length];
        nav.focus(next.trigger);
        next.menu.open({ trigger: next.trigger, focus: "first" });
      }
    } else if (e.target === m.trigger && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      m.menu.open({ trigger: m.trigger, focus: "first" });
    }
  };
  const onClick = (e) => {
    const m = menus.find((x) => x.trigger === e.target || x.trigger.contains(e.target));
    if (!m) return;
    m.menu.isOpen() ? m.menu.close() : m.menu.open({ trigger: m.trigger, focus: e.detail === 0 ? "first" : "content" });
  };
  const onHover = (e) => {
    const open = openOf();
    const m = menus.find((x) => x.trigger === e.target || x.trigger.contains(e.target));
    if (open && m && m !== open) { open.menu.close(false); m.menu.open({ trigger: m.trigger, focus: "content" }); }
  };
  bar.addEventListener("keydown", onKey);
  bar.addEventListener("click", onClick);
  bar.addEventListener("pointerover", onHover);
  return () => {
    for (const m of menus) m.menu.close(false);
    nav.stop();
    bar.removeEventListener("keydown", onKey);
    bar.removeEventListener("click", onClick);
    bar.removeEventListener("pointerover", onHover);
  };
}
