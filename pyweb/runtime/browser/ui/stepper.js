// Stepper: one step at a time. Next checks the fields of the current step first (the form's own
// rules when it's a <Form>, else the browser's constraint checks); Back never checks. The step list
// on top shows where you are (aria-current="step") and lets you go back to a finished step.
import { fire, part, partsAll } from "./core.js";

function stepValid(step) {
  const fields = Array.from(step.querySelectorAll("input, select, textarea")).filter((f) => !f.disabled && f.type !== "hidden");
  for (const f of fields) {
    if (f.getAttribute("aria-invalid") === "true" || !f.checkValidity()) {
      // forms.js listens for "invalid" and shows PyWeb's message; else the browser shows its own.
      if (!f.closest("form[data-pw-form]")) f.reportValidity();
      f.focus();
      return false;
    }
  }
  return true;
}

export default function setup(root) {
  const steps = () => partsAll(root, "step");
  const list = part(root, "list");
  const back = part(root, "back");
  const next = part(root, "next");
  const submit = part(root, "submit");
  let current = -1;

  function render() {
    const all = steps();
    list.replaceChildren(...all.map((s, i) => {
      const li = document.createElement("li");
      li.dataset.part = "indicator";
      li.dataset.state = i < current ? "complete" : i === current ? "current" : "upcoming";
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = s.dataset.title || `Step ${i + 1}`;
      b.disabled = i >= current;                // only finished steps can be revisited
      if (i === current) b.setAttribute("aria-current", "step");
      b.addEventListener("click", () => go(i));
      li.append(b);
      return li;
    }));
    all.forEach((s, i) => {
      s.hidden = i !== current;
      s.dataset.state = i === current ? "current" : i < current ? "complete" : "upcoming";
      const t = part(s, "title");
      if (t) t.hidden = true;                    // the list shows the titles
    });
    list.hidden = false;
    back.hidden = current === 0;
    next.hidden = current === all.length - 1;
    if (submit) submit.hidden = current !== all.length - 1;
    root.dataset.step = String(current);
  }

  function go(i, focus = true) {
    const all = steps();
    i = Math.max(0, Math.min(i, all.length - 1));
    if (i === current) return;
    if (current >= 0 && i > current && !stepValid(all[current])) return;
    if (current >= 0 && !fire(root, "stepchange", { step: i, from: current })) return;
    current = i;
    render();
    if (focus) {
      const first = all[i].querySelector("input:not([type=hidden]), select, textarea, button, [tabindex]");
      (first || all[i]).focus?.({ preventScroll: false });
    }
  }

  const onBack = () => go(current - 1);
  const onNext = () => go(current + 1);
  back.addEventListener("click", onBack);
  next.addEventListener("click", onNext);
  // Enter in a field moves on instead of submitting before the last step.
  const onKey = (e) => {
    if (e.key !== "Enter" || current === steps().length - 1) return;
    const t = e.target;
    if (t instanceof HTMLInputElement && t.type !== "submit" && t.type !== "button") { e.preventDefault(); onNext(); }
  };
  root.addEventListener("keydown", onKey);
  // A server error on a field of an earlier step: show that step.
  const form = root.closest("form");
  const onInvalid = (e) => {
    const s = e.target.closest && e.target.closest('[data-part="step"]');
    const i = steps().indexOf(s);
    if (i >= 0 && i !== current) { current = i; render(); }
  };
  if (form) form.addEventListener("invalid", onInvalid, true);
  const n = steps().length;
  const start = Number(root.dataset.default || 0);
  current = Number.isFinite(start) ? Math.max(0, Math.min(start, n - 1)) : 0;
  if (n) render();
  root.pwGo = go;
  return () => {
    back.removeEventListener("click", onBack);
    next.removeEventListener("click", onNext);
    root.removeEventListener("keydown", onKey);
    if (form) form.removeEventListener("invalid", onInvalid, true);
  };
}
