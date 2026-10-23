// RadioGroup: native radio buttons (arrow keys, forms and validation come from the browser). This
// gives items without a name the group's name, and fires "valuechange" on the group.
import { fire, uid } from "./core.js";

export default function setup(root) {
  const name = root.dataset.name || uid("pw-radio");
  const value = root.dataset.value;
  for (const input of root.querySelectorAll('input[type="radio"]')) {
    if (!input.name) input.name = name;
    if (value != null && input.value === value) input.checked = true;
  }
  const onChange = (e) => {
    if (e.target instanceof HTMLInputElement && e.target.type === "radio" && e.target.checked) {
      root.dataset.value = e.target.value;
      fire(root, "valuechange", { value: e.target.value });
    }
  };
  root.addEventListener("change", onChange);
  return () => root.removeEventListener("change", onChange);
}
