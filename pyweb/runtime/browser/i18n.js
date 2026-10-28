// _() and ngettext() in browser code: the translations this page uses, sent with it as
// <script type="application/json" id="pw-i18n"> (only strings the page's code translates).
let table = null;
function strings() {
  if (table === null) {
    const el = document.getElementById("pw-i18n");
    try { table = el ? JSON.parse(el.textContent) : {}; } catch { table = {}; }
  }
  return table;
}

export function gettext(message) {
  const t = strings()[message];
  return typeof t === "string" && t ? t : message;
}

export function ngettext(singular, plural, n) {
  return pick(singular, plural, n).split("{n}").join(String(n));
}

function pick(singular, plural, n) {
  const forms = strings()[singular + "\u0000" + plural];
  if (Array.isArray(forms)) {
    // The language's plural rule, as the browser knows it (Intl), mapped to gettext's order.
    const lang = strings()["$lang"] || document.documentElement.lang || "en";
    const category = new Intl.PluralRules(lang).select(n);
    const order = ORDER[forms.length] || ORDER[2];
    const i = order.indexOf(category);
    const form = forms[i >= 0 ? i : forms.length - 1];
    if (form) return form;
  }
  return n === 1 ? singular : plural;
}

// gettext form index -> CLDR category, for the usual Plural-Forms of each count of forms.
const ORDER = {
  1: ["other"],
  2: ["one", "other"],
  3: ["one", "few", "many"],
  4: ["one", "two", "few", "other"],
  6: ["zero", "one", "two", "few", "many", "other"],
};
