// <Form> for PyWeb pages. Loaded only by pages with a form.
//
// <Form> markup carries its rules (data-pw-rules, made from the Model on the
// server). Checks run as the user types, with the same messages as the server
// (pyweb/rules.py); the submit goes to the same endpoint a plain POST uses,
// asking for JSON, so the server checks everything again.

import { navigate } from "./runtime.js";

const G = typeof window !== "undefined" ? (window.__pyweb || (window.__pyweb = {})) : {};
const EMAIL_RE = /^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$/;
const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const URL_RE = /^https?:\/\/[^\s/$.?#][^\s]*$/i;
const FORMAT_RE = { email: EMAIL_RE, slug: SLUG_RE, url: URL_RE };
const FORMAT_MSG = {
  email: "must be a valid email address",
  slug: "may only use a-z, 0-9 and single dashes",
  url: "must be a web address starting with http:// or https://",
};
const TEXT_KINDS = ["str", "text"];
const NUMBER_KINDS = ["int", "bigint", "float", "decimal"];

function bound(word, n, unit) {
  return unit ? `must be ${word} ${n} ${n === 1 ? unit[0] : unit[1]}` : `must be ${word} ${n}`;
}

/** The first problem with `value` under rules `r` (as pyweb.rules.Rules.check), or null.
 * `value` is what the browser has: a string, a list of strings (multiple), or a boolean. */
export function checkRule(r, value) {
  const kind = r.kind || "str";
  const empty = value === null || value === undefined || value === "" ||
    (Array.isArray(value) && value.length === 0 && !r.multiple);
  if (empty) return r.required ? (r.message || "is required") : null;
  let v = value;
  if (NUMBER_KINDS.includes(kind) && typeof v === "string") {
    const t = v.trim();
    if (kind === "int" || kind === "bigint") {
      if (!/^[-+]?\d+$/.test(t)) return "must be a whole number";
    } else if (t === "" || !isFinite(Number(t))) return "must be a number";
    v = Number(t);
  }
  let size = null, unit = null;
  if (Array.isArray(v)) { size = v.length; unit = ["item", "items"]; }
  else if (typeof v === "string") { size = v.length; unit = ["character", "characters"]; }
  else if (typeof v === "number") size = v;
  if (size !== null) {
    if (r.min !== undefined && r.min !== null && size < r.min) return r.message || bound("at least", r.min, unit);
    if (r.max !== undefined && r.max !== null && size > r.max) return r.message || bound("at most", r.max, unit);
  }
  if (r.choices && !r.choices.map(String).includes(String(value))) {
    return r.message || "must be one of: " + r.choices.join(", ");
  }
  if (r.format && typeof v === "string" && !FORMAT_RE[r.format].test(v)) return r.message || FORMAT_MSG[r.format];
  if (r.pattern && typeof v === "string") {
    let re = null;
    try { re = new RegExp("^(?:" + r.pattern + ")$"); } catch (e) { re = null; }
    if (re && !re.test(v)) return r.message || "has the wrong format";
  }
  return null;
}

/** "Title is required" from a label and a phrase (messages starting with a capital stand alone). */
export function fieldMessage(label, msg) {
  if (!msg) return "";
  return /^[a-z]/.test(msg) && label ? `${label} ${msg}` : msg;
}

function formRules(form) {
  try { return JSON.parse(form.getAttribute("data-pw-rules") || "{}"); } catch (e) { return {}; }
}

function controlsOf(form, name) {
  return Array.from(form.elements).filter((el) => el.name === name);
}

function readField(form, name, r) {
  const els = controlsOf(form, name);
  if (!els.length) return undefined;
  const el = els[0];
  if (el.type === "file") {
    const files = Array.from(el.files || []);
    return files.length ? files : null;
  }
  if (el.type === "checkbox") return el.checked;
  if (el.tagName === "SELECT" && el.multiple) return Array.from(el.selectedOptions).map((o) => o.value);
  return el.value;
}

function checkField(form, name, r) {
  const value = readField(form, name, r);
  if (value === undefined) return null;
  if (r.file) {
    if (!value) return r.required ? "is required" : null;
    for (const f of value) {
      if (r.file.max_size && f.size > r.file.max_size) {
        const mb = r.file.max_size / 1048576;
        return `must be at most ${mb >= 1 ? +mb.toFixed(1) + " MB" : Math.round(r.file.max_size / 1024) + " KB"}`;
      }
      const types = r.file.types || [];
      if (types.length && f.type && !types.some((t) => t === f.type || (t.endsWith("/*") && f.type.startsWith(t.slice(0, -1))))) {
        return "isn't an allowed kind of file";
      }
    }
    return null;
  }
  if (r.kind === "bool") return null;
  return checkRule(r, value);
}

function showFieldError(form, name, r, msg) {
  const els = controlsOf(form, name).filter((el) => el.type !== "hidden");
  const el = els[0];
  if (!el) return;
  const p = el.id ? document.getElementById(el.id + "-error") : null;
  const text = fieldMessage(r && r.label, msg);
  if (msg) el.setAttribute("aria-invalid", "true"); else el.removeAttribute("aria-invalid");
  if (p) { p.textContent = text; p.hidden = !msg; }
}

function showFormError(form, msg) {
  const p = form.querySelector("[data-pw-form-error]");
  if (p) { p.textContent = msg || ""; p.hidden = !msg; }
}

/** Check every field; show the problems. Returns {name: message}. */
export function validateForm(form) {
  const rules = formRules(form);
  const errors = {};
  for (const [name, r] of Object.entries(rules)) {
    const msg = checkField(form, name, r);
    showFieldError(form, name, r, msg);
    if (msg) errors[name] = msg;
  }
  return errors;
}

function focusFirst(form, errors) {
  for (const name of Object.keys(errors)) {
    const el = controlsOf(form, name).find((e) => e.type !== "hidden");
    if (el && typeof el.focus === "function") { el.focus(); return; }
  }
}

function setBusy(form, busy) {
  if (busy) form.setAttribute("aria-busy", "true"); else form.removeAttribute("aria-busy");
  for (const b of form.querySelectorAll('[type="submit"], [data-pw-submit]')) b.disabled = busy;
}

async function submitForm(form) {
  if (form.getAttribute("aria-busy") === "true") return;
  const errors = validateForm(form);
  showFormError(form, "");
  if (Object.keys(errors).length) { focusFirst(form, errors); return; }
  setBusy(form, true);
  let res = null, data = null;
  try {
    res = await fetch(form.getAttribute("action"), {
      method: "POST", body: new FormData(form), credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    data = await res.json().catch(() => null);
  } catch (e) {
    data = null;
  } finally {
    setBusy(form, false);
  }
  if (!res || !data) {
    showFormError(form, "Couldn't reach the server. Check your connection and try again.");
    return;
  }
  if (data.ok) {
    const key = form.querySelector('input[name="__pw_key"]');
    if (key && data.next_key) key.value = data.next_key;   // the next submit is a new one
    form.dispatchEvent(new CustomEvent("pw:submitted", { detail: data.result, bubbles: true }));
    if (data.redirect) {
      if (G.router) navigate(data.redirect, { push: true }); else location.assign(data.redirect);
    } else {
      form.reset();
    }
    return;
  }
  const rules = formRules(form);
  const fieldErrors = data.errors || {};
  for (const [name, msg] of Object.entries(fieldErrors)) {
    if (name !== "__all__") showFieldError(form, name, rules[name] || { label: "" }, msg);
  }
  const general = fieldErrors.__all__ || (Object.keys(fieldErrors).length ? "" : data.error) || "";
  showFormError(form, general);
  focusFirst(form, fieldErrors);
}

function pwForm(el) {
  return el && el.closest ? el.closest("form[data-pw-form]") : null;
}

/** With JavaScript, PyWeb checks fields itself (same rules, nicer messages): turn off
 * the browser's own checks, which stay on for visitors without JavaScript. */
function takeOver(form) {
  if (form && !form.noValidate) form.noValidate = true;
}

function startForms() {
  if (G.formsStarted || typeof document === "undefined") return;
  G.formsStarted = true;
  const claim = (ev) => takeOver(pwForm(ev.target));
  document.addEventListener("click", claim, true);       // before a submit button's click submits
  document.addEventListener("keydown", (ev) => { if (ev.key === "Enter") claim(ev); }, true);
  document.addEventListener("focusin", claim, true);
  const claimAll = () => document.querySelectorAll("form[data-pw-form]").forEach(takeOver);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", claimAll);
  else claimAll();
  document.addEventListener("submit", (ev) => {
    const form = pwForm(ev.target);
    if (!form || ev.defaultPrevented) return;
    ev.preventDefault();
    submitForm(form);
  });
  // The browser's own checks (required, maxlength...) would show its bubbles; show ours instead.
  document.addEventListener("invalid", (ev) => {
    const form = pwForm(ev.target);
    if (!form) return;
    ev.preventDefault();
    const rules = formRules(form);
    const r = rules[ev.target.name];
    if (r) showFieldError(form, ev.target.name, r, checkField(form, ev.target.name, r) || "has the wrong format");
    if (!form.__pwFocused) {
      form.__pwFocused = true;
      ev.target.focus();
      setTimeout(() => { form.__pwFocused = false; }, 0);
    }
  }, true);
  const recheck = (ev, always) => {
    const form = pwForm(ev.target);
    const name = ev.target && ev.target.name;
    if (!form || !name) return;
    const r = formRules(form)[name];
    if (!r) return;
    if (!always && ev.target.getAttribute("aria-invalid") !== "true") return;
    showFieldError(form, name, r, checkField(form, name, r));
  };
  document.addEventListener("focusout", (ev) => {
    // Going to a submit button: let the submit check everything. Showing a message
    // now would move the button down, and the click would miss it.
    const next = ev.relatedTarget;
    if (next && next.closest && next.closest('[type="submit"], [data-pw-submit]')) return;
    if (ev.target && ev.target.value !== "") recheck(ev, true);
  });
  document.addEventListener("input", (ev) => recheck(ev, false));
  document.addEventListener("change", (ev) => recheck(ev, ev.target && ev.target.type === "checkbox"));
}

startForms();
