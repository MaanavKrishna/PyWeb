// Toggle: a button that stays pressed (aria-pressed). Fires "pressedchange"; with data-controlled
// the app owns the state (pressed={...}) and the button only reports clicks.
import { fire } from "./core.js";

export default function setup(el) {
  const onClick = () => {
    if (el.disabled) return;
    const on = el.getAttribute("aria-pressed") !== "true";
    if (!fire(el, "pressedchange", { pressed: on }) || el.dataset.controlled !== undefined) return;
    el.setAttribute("aria-pressed", on ? "true" : "false");
    el.dataset.state = on ? "on" : "off";
  };
  el.addEventListener("click", onClick);
  return () => el.removeEventListener("click", onClick);
}
