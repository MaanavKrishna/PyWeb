// <Markdown> for PyWeb pages: safe Markdown -> HTML with the same rules as
// pyweb/markdown.py (see there): no raw HTML, escaped text, and only
// http(s)/mailto/relative URLs. Loaded only by pages that use <Markdown>.

import { clsx, effect, element, prop } from "./runtime.js";

const MD = {
  fence: /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)/,
  heading: /^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$/,
  rule: /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/,
  quote: /^ {0,3}> ?/,
  item: /^( *)([-*+]|\d{1,9}[.)])[ \t]+(.*)$/,
  tableSep: /^ {0,3}\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/,
  url: "((?:[^()\\s]|\\([^()\\s]*\\))+)",
  safe: /^(https?:|mailto:|\/|#|\.\/|\.\.\/|[\w.-]+(\/|$))/i,
};

function mdEscape(t) {
  return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function mdUnescape(t) {
  return t.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&amp;/g, "&");
}
function mdSafeUrl(url) {
  url = url.trim();
  if (!url || (/^[a-z][a-z0-9+.-]*:/i.test(url) && !/^(https?|mailto):/i.test(url))) return null;
  return MD.safe.test(url) ? url : null;
}
function mdEmphasis(t) {
  return t.replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<![\w])__(?=\S)(.+?)(?<=\S)__(?![\w])/g, "<strong>$1</strong>")
    .replace(/\*(?=[^\s*])(.+?)(?<=[^\s*])\*/g, "<em>$1</em>")
    .replace(/(?<![\w])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w])/g, "<em>$1</em>")
    .replace(/~~(?=\S)(.+?)(?<=\S)~~/g, "<del>$1</del>");
}
function mdInline(text) {
  const saved = [];
  const keep = (html) => { saved.push(html); return "\x00" + (saved.length - 1) + "\x00"; };
  let out = "", i = 0;
  while (i < text.length) {
    if (text[i] === "`") {
      let run = 0;
      while (text[i + run] === "`") run++;
      const ticks = "`".repeat(run);
      let close = text.indexOf(ticks, i + run);
      while (close !== -1 && text[close + run] === "`") close = text.indexOf(ticks, close + run + 1);
      if (close !== -1) {
        let code = text.slice(i + run, close);
        if (code.startsWith(" ") && code.endsWith(" ") && code.trim()) code = code.slice(1, -1);
        out += keep("<code>" + mdEscape(code) + "</code>");
        i = close + run;
        continue;
      }
      out += ticks;
      i += run;
      continue;
    }
    out += text[i];
    i++;
  }
  let t = mdEscape(out);
  t = t.replace(new RegExp("!\\[([^\\]\\n]*)\\]\\(" + MD.url + "\\)", "g"), (_, alt, u) => {
    const url = mdSafeUrl(mdUnescape(u));
    return keep(url === null ? mdEscape(mdUnescape(alt)) : `<img src="${mdEscape(url)}" alt="${alt}">`);
  });
  t = t.replace(new RegExp("\\[([^\\]\\n]+)\\]\\(" + MD.url + "\\)", "g"), (_, label, u) => {
    const url = mdSafeUrl(mdUnescape(u));
    const inner = mdEmphasis(label);
    return keep(url === null ? inner : `<a href="${mdEscape(url)}" rel="noopener noreferrer">${inner}</a>`);
  });
  t = t.replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g, (_, u) => {
    const url = mdUnescape(u);
    return keep(`<a href="${mdEscape(url)}" rel="noopener noreferrer">${mdEscape(url)}</a>`);
  });
  t = mdEmphasis(t).replace(/(?: {2,}|\\)\n/g, "<br>");
  while (t.includes("\x00")) t = t.replace(/\x00(\d+)\x00/g, (_, n) => saved[+n]);
  return t;
}
function mdStartsBlock(l) {
  return MD.fence.test(l) || MD.heading.test(l) || MD.rule.test(l) || MD.quote.test(l) || MD.item.test(l);
}
function indentOf(l) { return l.length - l.trimStart().length; }
function mdBlocks(lines) {
  const out = [];
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m = MD.fence.exec(line);
    if (m) {
      const fence = m[1], lang = m[2];
      const end = new RegExp("^ {0,3}" + (fence[0] === "`" ? "`" : "~") + "{" + fence.length + ",}\\s*$");
      const body = [];
      i++;
      while (i < n && !end.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push(`<pre><code${lang ? ` class="language-${mdEscape(lang)}"` : ""}>${mdEscape(body.join("\n"))}</code></pre>`);
      continue;
    }
    m = MD.heading.exec(line);
    if (m) { out.push(`<h${m[1].length}>${mdInline(m[2] || "")}</h${m[1].length}>`); i++; continue; }
    if (MD.rule.test(line)) { out.push("<hr>"); i++; continue; }
    if (MD.quote.test(line)) {
      const body = [];
      while (i < n && lines[i].trim() && (MD.quote.test(lines[i]) || !mdStartsBlock(lines[i]))) body.push(lines[i++].replace(MD.quote, ""));
      out.push(`<blockquote>${mdBlocks(body)}</blockquote>`);
      continue;
    }
    if (MD.item.test(line)) { const [html, next] = mdList(lines, i); out.push(html); i = next; continue; }
    if (line.includes("|") && i + 1 < n && MD.tableSep.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const [html, next] = mdTable(lines, i); out.push(html); i = next; continue;
    }
    const para = [];
    while (i < n && lines[i].trim() && !(para.length && mdStartsBlock(lines[i]))) {
      para.push(lines[i].endsWith("  ") ? lines[i].trimStart() : lines[i].trim());
      i++;
    }
    out.push(`<p>${mdInline(para.join("\n"))}</p>`);
  }
  return out.join("");
}
function mdList(lines, i) {
  const first = MD.item.exec(lines[i]);
  const indent = first[1].length;
  const ordered = /\d/.test(first[2][0]);
  const start = ordered ? parseInt(first[2], 10) : 1;
  const items = [];
  const n = lines.length;
  while (i < n) {
    const m = MD.item.exec(lines[i]);
    if (!m || m[1].length !== indent || /\d/.test(m[2][0]) !== ordered) break;
    const body = [m[3]];
    let loose = false;
    i++;
    while (i < n) {
      const line = lines[i];
      if (!line.trim()) {
        const nxt = i + 1 < n ? lines[i + 1] : "";
        if (nxt.trim() && indentOf(nxt) > indent) { body.push(""); loose = true; i++; continue; }
        break;
      }
      const sub = MD.item.exec(line);
      if (sub && sub[1].length <= indent) break;
      if (!sub && indentOf(line) <= indent && mdStartsBlock(line)) break;
      body.push(line.startsWith(" ".repeat(indent + 2)) ? line.slice(indent + 2) : line.trimStart());
      i++;
    }
    let html = mdBlocks(body);
    if (!loose && html.startsWith("<p>") && html.split("<p>").length === 2) html = html.slice(3).replace("</p>", "");
    items.push(`<li>${html}</li>`);
  }
  const tag = ordered ? "ol" : "ul";
  const attr = ordered && start !== 1 ? ` start="${start}"` : "";
  return [`<${tag}${attr}>${items.join("")}</${tag}>`, i];
}
function mdCells(line) {
  line = line.trim();
  if (line.startsWith("|")) line = line.slice(1);
  if (line.endsWith("|") && !line.endsWith("\\|")) line = line.slice(0, -1);
  return line.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}
function mdTable(lines, i) {
  const head = mdCells(lines[i]);
  i += 2;
  const rows = [];
  while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(mdCells(lines[i++]));
  const w = head.length;
  let html = "<table><thead><tr>" + head.map((c) => `<th>${mdInline(c)}</th>`).join("") + "</tr></thead>";
  if (rows.length) {
    html += "<tbody>" + rows.map((r) => "<tr>" + [...r, ...Array(w).fill("")].slice(0, w).map((c) => `<td>${mdInline(c)}</td>`).join("") + "</tr>").join("") + "</tbody>";
  }
  return [html + "</table>", i];
}

/** Markdown `text` as safe HTML. */
export function renderMarkdown(text) {
  if (text == null) return "";
  const lines = String(text).replace(/\x00/g, "").replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");
  return mdBlocks(lines);
}

/** `<Markdown text={...}>`: a <div class="markdown"> whose HTML follows the text. */
export function markdown(get, props) {
  const el = element("div");
  const user = props && props.class;
  if (props) for (const k of Object.keys(props)) if (k !== "class") prop(el, k, props[k]);
  prop(el, "class", typeof user === "function" ? () => clsx(["markdown", user()]) : clsx(["markdown", user]));
  let last = null;
  effect(() => {
    const html = renderMarkdown(typeof get === "function" ? get() : get);
    if (html !== last) { last = html; el.innerHTML = html; }
  });
  return el;
}
