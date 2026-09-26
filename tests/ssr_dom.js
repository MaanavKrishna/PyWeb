/* Tiny SSR-HTML -> fakedom loader (elements, double-quoted attrs,
 * comments, text; void elements input/img/br/hr/meta/link). */
"use strict";

function decodeEntities(s) {
  return s.replace(/&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z]+);/g, function (m, e) {
    if (e[0] === "#") {
      var cp = e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return String.fromCharCode(cp);
    }
    return { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" }[e] || m;
  });
}

function loadSSR(appEl, html) {
  var VOID = { input: 1, img: 1, br: 1, hr: 1, meta: 1, link: 1 };
  var doc = appEl.ownerDocument;
  var stack = [appEl];
  var re = /<!--([\s\S]*?)-->|<\/([a-zA-Z][a-zA-Z0-9]*)>|<([a-zA-Z][a-zA-Z0-9]*)((?:"[^"]*"|[^"<>])*)>/g;
  var last = 0, m;
  function top() { return stack[stack.length - 1]; }
  function attrParse(src, el) {
    var am = /([a-zA-Z_:][a-zA-Z0-9_.:-]*)(?:="([^"]*)")?/g, a;
    while ((a = am.exec(src))) {
      if (a[1] === "/") continue;
      el.setAttribute(a[1], a[2] === undefined ? "" : decodeEntities(a[2]));
      if (a[1] === "value") el.value = a[2] || "";
      if (a[1] === "placeholder") el.setAttribute("placeholder", a[2] || "");
    }
  }
  while ((m = re.exec(html))) {
    if (m.index > last) {
      var text = decodeEntities(html.slice(last, m.index));
      if (text) top().appendChild(doc.createTextNode(text));
    }
    last = re.lastIndex;
    if (m[1] !== undefined) {
      top().appendChild(doc.createComment(m[1]));
    } else if (m[2]) {
      if (stack.length > 1) stack.pop();
    } else if (m[3]) {
      var el = doc.createElement(m[3].toLowerCase());
      attrParse(m[4] || "", el);
      top().appendChild(el);
      var selfClose = /\/\s*$/.test(m[4] || "");
      if (!VOID[m[3].toLowerCase()] && !selfClose) stack.push(el);
    }
  }
  if (last < html.length) top().appendChild(doc.createTextNode(html.slice(last)));
  return appEl;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports.loadSSR = loadSSR;
}
