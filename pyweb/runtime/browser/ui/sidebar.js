// Sidebar layout: SidebarTrigger (or Ctrl/⌘+B) collapses the sidebar to icons or hides it, and the
// choice is remembered in this browser. On small screens the sidebar opens over the page as a
// sheet instead (Escape, a click outside or a link closes it).
import { fire, lockScroll, owner, part, partsAll, pushLayer, trapFocus } from "./core.js";

const KEY = "pyweb:sidebar";

export default function setup(root) {
  const bar = part(root, "sidebar");
  if (!bar) return null;
  const mq = matchMedia(`(max-width: ${root.dataset.breakpoint || "768px"})`);
  const triggers = () => [...partsAll(root, "trigger"), ...partsAll(root, "rail")];
  let release = [];
  try {
    const saved = root.dataset.save !== "false" && localStorage.getItem(KEY);
    if (saved === "collapsed" || saved === "expanded") root.dataset.state = saved;
  } catch (_) { /* storage unavailable */ }
  root.dataset.state ||= "expanded";
  bar.id ||= "pw-sidebar";

  function sync() {
    const mobile = mq.matches;
    root.dataset.mobile = mobile ? "true" : "false";
    const open = mobile ? root.dataset.mobileOpen === "true" : root.dataset.state === "expanded";
    for (const t of triggers()) { t.setAttribute("aria-expanded", open ? "true" : "false"); t.setAttribute("aria-controls", bar.id); }
    bar.inert = mobile && !open;
  }

  function closeSheet() {
    if (root.dataset.mobileOpen !== "true") return;
    root.dataset.mobileOpen = "false";
    for (const r of release.splice(0)) r();
    sync();
  }

  function toggle() {
    if (mq.matches) {
      if (root.dataset.mobileOpen === "true") { closeSheet(); return; }
      root.dataset.mobileOpen = "true";
      release = [lockScroll(), pushLayer({ el: bar, keep: triggers(), dismiss: closeSheet }), trapFocus(bar)];
    } else {
      root.dataset.state = root.dataset.state === "expanded" ? "collapsed" : "expanded";
      try { if (root.dataset.save !== "false") localStorage.setItem(KEY, root.dataset.state); } catch (_) { /* blocked */ }
      fire(root, "openchange", { open: root.dataset.state === "expanded" });
    }
    sync();
  }

  const onClick = (e) => {
    const el = e.target instanceof Element ? e.target : null;
    if (!el) return;
    const t = el.closest('[data-part="trigger"],[data-part="rail"]');
    if (t && owner(t, "sidebar") === root) { e.preventDefault(); toggle(); return; }
    if (mq.matches && el.closest("a[href]") && bar.contains(el)) closeSheet();
  };
  const onKey = (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === (root.dataset.shortcut || "b")) { e.preventDefault(); toggle(); }
  };
  root.addEventListener("click", onClick);
  document.addEventListener("keydown", onKey);
  const onMedia = () => { closeSheet(); sync(); };
  mq.addEventListener("change", onMedia);
  sync();
  root.pwToggle = toggle;
  return () => {
    closeSheet();
    mq.removeEventListener("change", onMedia);
    root.removeEventListener("click", onClick);
    document.removeEventListener("keydown", onKey);
  };
}
