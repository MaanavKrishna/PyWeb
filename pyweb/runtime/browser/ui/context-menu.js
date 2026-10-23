// ContextMenu: the menu from right-clicking (or long-pressing, or Shift+F10 on) an area.
import { menuRoot } from "./menu.js";
import { part } from "./core.js";

export default function setup(root) {
  const area = part(root, "trigger");
  if (!area || !part(root, "content")) return null;
  const menu = menuRoot(root, { anchorFor: () => area });
  let press = 0;
  const at = (x, y) => menu.open({ at: { x, y }, focus: "content" });
  const onContext = (e) => { e.preventDefault(); menu.close(false); at(e.clientX, e.clientY); };
  const onDown = (e) => {
    if (e.pointerType !== "touch") return;
    clearTimeout(press);
    press = setTimeout(() => at(e.clientX, e.clientY), 550);
  };
  const cancel = () => clearTimeout(press);
  const onKey = (e) => {
    if ((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu") {
      e.preventDefault();
      const r = area.getBoundingClientRect();
      menu.open({ at: { x: r.left + 8, y: r.top + 8 }, focus: "first" });
    }
  };
  area.addEventListener("contextmenu", onContext);
  area.addEventListener("pointerdown", onDown);
  area.addEventListener("pointerup", cancel);
  area.addEventListener("pointermove", cancel);
  area.addEventListener("keydown", onKey);
  return () => {
    menu.close(false);
    area.removeEventListener("contextmenu", onContext);
    area.removeEventListener("pointerdown", onDown);
    area.removeEventListener("keydown", onKey);
  };
}
