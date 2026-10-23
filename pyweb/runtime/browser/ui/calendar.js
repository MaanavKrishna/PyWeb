// Calendar (WAI-ARIA APG "Date Picker Dialog" grid): a month grid of days. Arrows move a day or a
// week, Home/End to the week's ends, PageUp/PageDown a month (with Shift a year), Enter/Space pick.
// data-mode="range" picks a start and an end. data-min/data-max limit the choice. The value
// (YYYY-MM-DD, or "start/end") goes into the inner input, so it's sent with forms; without scripts
// that input is a native date field. Inside a DatePicker it also updates the trigger and closes.
import { fire } from "./core.js";

const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parse = (s) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
};
const add = (d, days) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + days);
const addMonths = (d, n) => {
  const last = new Date(d.getFullYear(), d.getMonth() + n + 1, 0).getDate();
  return new Date(d.getFullYear(), d.getMonth() + n, Math.min(d.getDate(), last));
};
const el = (tag, props = {}, kids = []) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "text") e.textContent = v; else if (v != null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...kids);
  return e;
};

export function format(value, locale, opts = { dateStyle: "medium" }) {
  const f = new Intl.DateTimeFormat(locale, opts);
  return String(value || "").split("/").map(parse).filter(Boolean).map((d) => f.format(d)).join(" – ");
}

export default function setup(root) {
  const input = root.querySelector('[data-part="input"]');
  const locale = root.dataset.locale || document.documentElement.lang || navigator.language;
  const range = root.dataset.mode === "range";
  const min = parse(root.dataset.min), max = parse(root.dataset.max);
  let weekStart = Number(root.dataset.weekStart);
  if (!root.dataset.weekStart) {
    try { weekStart = (new Intl.Locale(locale).weekInfo?.firstDay ?? 7) % 7; } catch (_) { weekStart = 0; }
  }
  let [start, end] = (root.dataset.value || (input && input.value) || "").split("/").map(parse);
  let focus = start || new Date();
  focus = new Date(focus.getFullYear(), focus.getMonth(), focus.getDate());
  let hover = null;
  const picker = root.closest('[data-pw-ui="popover"]');

  const caption = el("div", { "data-part": "caption", "aria-live": "polite", id: `${root.id || "pw-cal"}-caption` });
  const prev = el("button", { type: "button", "data-part": "previous", "aria-label": "Previous month", text: "‹" });
  const next = el("button", { type: "button", "data-part": "next", "aria-label": "Next month", text: "›" });
  const grid = el("table", { role: "grid", "data-part": "grid", "aria-labelledby": caption.id });
  root.append(el("div", { "data-part": "header" }, [prev, caption, next]), grid);

  const allowed = (d) => (!min || d >= min) && (!max || d <= max);
  const same = (a, b) => a && b && a.getTime() === b.getTime();

  function render() {
    caption.textContent = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(focus);
    const first = new Date(focus.getFullYear(), focus.getMonth(), 1);
    let day = add(first, -((first.getDay() - weekStart + 7) % 7));
    const long = new Intl.DateTimeFormat(locale, { weekday: "long" });
    const short = new Intl.DateTimeFormat(locale, { weekday: "short" });
    const head = el("tr");
    for (let i = 0; i < 7; i++) {
      const d = add(day, i);
      head.append(el("th", { scope: "col", abbr: long.format(d), text: short.format(d).slice(0, 2) }));
    }
    const body = el("tbody");
    const full = new Intl.DateTimeFormat(locale, { dateStyle: "full" });
    const today = new Date(); today.setHours(0, 0, 0, 0);
    let a = start, b = end || hover;
    if (a && b && b < a) [a, b] = [b, a];
    for (let w = 0; w < 6; w++) {
      const row = el("tr");
      for (let i = 0; i < 7; i++, day = add(day, 1)) {
        const picked = same(day, start) || same(day, end);
        const inRange = range && a && b && day > a && day < b;
        const btn = el("button", {
          type: "button", "data-part": "day", "data-date": iso(day), tabindex: same(day, focus) ? "0" : "-1",
          "aria-label": full.format(day), "aria-current": same(day, today) ? "date" : null,
          "data-outside": day.getMonth() !== focus.getMonth(), "data-in-range": inRange,
          "data-range-start": range && same(day, a), "data-range-end": range && same(day, b),
          disabled: !allowed(day), text: String(day.getDate()),
        });
        row.append(el("td", { role: "gridcell", "aria-selected": picked ? "true" : "false" }, [btn]));
      }
      body.append(row);
    }
    grid.replaceChildren(el("thead", {}, [head]), body);
    prev.disabled = !!min && new Date(focus.getFullYear(), focus.getMonth(), 0) < min;
    next.disabled = !!max && new Date(focus.getFullYear(), focus.getMonth() + 1, 1) > max;
  }

  function moveTo(d, takeFocus = true) {
    if (min && d < min) d = min;
    if (max && d > max) d = max;
    focus = d;
    render();
    if (takeFocus) grid.querySelector('[tabindex="0"]')?.focus();
  }

  function pick(d) {
    if (!allowed(d)) return;
    if (range && start && !end && d >= start) end = d;
    else if (range && start && !end) { end = start; start = d; }
    else { start = d; end = null; }
    hover = null;
    const value = range ? (end ? `${iso(start)}/${iso(end)}` : "") : iso(start);
    focus = d;
    render();
    grid.querySelector('[tabindex="0"]')?.focus();
    if (range && !end) return;
    root.dataset.value = value;
    if (input) input.value = value;
    fire(root, "valuechange", { value });
    if (input) input.dispatchEvent(new Event("change", { bubbles: true }));
    if (picker && root.dataset.picker !== undefined) {
      const label = picker.querySelector('[data-part="value"]');
      if (label) { label.textContent = format(value, locale); label.parentElement.removeAttribute("data-empty"); }
      picker.pwClose?.(true);
    }
  }

  const onClick = (e) => {
    const b = e.target instanceof Element ? e.target.closest("button") : null;
    if (!b || !root.contains(b)) return;
    if (b === prev) moveTo(addMonths(focus, -1), false);
    else if (b === next) moveTo(addMonths(focus, 1), false);
    else if (b.dataset.date) pick(parse(b.dataset.date));
  };
  const onKey = (e) => {
    if (!(e.target instanceof Element) || !e.target.dataset.date) return;
    const k = e.key, ws = (focus.getDay() - weekStart + 7) % 7;
    const to = { ArrowLeft: add(focus, -1), ArrowRight: add(focus, 1), ArrowUp: add(focus, -7), ArrowDown: add(focus, 7),
      Home: add(focus, -ws), End: add(focus, 6 - ws),
      PageUp: addMonths(focus, e.shiftKey ? -12 : -1), PageDown: addMonths(focus, e.shiftKey ? 12 : 1) }[k];
    if (to) { e.preventDefault(); moveTo(to); }
    else if (k === "Enter" || k === " ") { e.preventDefault(); pick(focus); }
  };
  const onOver = (e) => {
    if (!range || !start || end) return;
    const b = e.target instanceof Element ? e.target.closest("[data-date]") : null;
    const d = b && parse(b.dataset.date);
    if (d && !same(d, hover)) { hover = d; render(); }
  };
  const onOpen = (e) => { if (e.detail && e.detail.open) requestAnimationFrame(() => moveTo(start || focus)); };
  root.addEventListener("click", onClick);
  root.addEventListener("keydown", onKey);
  root.addEventListener("pointerover", onOver);
  if (picker) picker.addEventListener("openchange", onOpen);
  render();
  if (picker && root.dataset.value) {
    const label = picker.querySelector('[data-part="value"]');
    if (label) label.textContent = format(root.dataset.value, locale);
  }
  return () => {
    root.removeEventListener("click", onClick);
    root.removeEventListener("keydown", onKey);
    root.removeEventListener("pointerover", onOver);
    if (picker) picker.removeEventListener("openchange", onOpen);
  };
}
