/* Minimal fake DOM for testing runtime.js + generated app code in node. */
"use strict";

function FakeNode(nodeType) {
  this.nodeType = nodeType; // 1 el, 3 text, 8 comment, 11 fragment
  this.parentNode = null;
  this._kids = [];
  this._listeners = {};
}
Object.defineProperty(FakeNode.prototype, "childNodes", {
  get: function () { return this._kids; }
});
Object.defineProperty(FakeNode.prototype, "nextSibling", {
  get: function () {
    if (!this.parentNode) return null;
    var sibs = this.parentNode._kids;
    var i = sibs.indexOf(this);
    return i >= 0 && i + 1 < sibs.length ? sibs[i + 1] : null;
  }
});
FakeNode.prototype.appendChild = function (child) {
  if (child.nodeType === 11) {
    var kids = child._kids.slice();
    for (var i = 0; i < kids.length; i++) this.appendChild(kids[i]);
    return child;
  }
  if (child.parentNode) child.parentNode.removeChild(child);
  child.parentNode = this;
  this._kids.push(child);
  return child;
};
FakeNode.prototype.insertBefore = function (node, ref) {
  if (node.nodeType === 11) {
    var kids = node._kids.slice();
    for (var i = 0; i < kids.length; i++) this.insertBefore(kids[i], ref);
    return node;
  }
  if (node.parentNode) node.parentNode.removeChild(node);
  node.parentNode = this;
  if (!ref) { this._kids.push(node); return node; }
  var i = this._kids.indexOf(ref);
  if (i < 0) this._kids.push(node);
  else this._kids.splice(i, 0, node);
  return node;
};
FakeNode.prototype.removeChild = function (child) {
  var i = this._kids.indexOf(child);
  if (i < 0) throw new Error("removeChild: not a child");
  this._kids.splice(i, 1);
  child.parentNode = null;
  return child;
};
FakeNode.prototype.addEventListener = function (type, fn) {
  (this._listeners[type] = this._listeners[type] || []).push(fn);
};
FakeNode.prototype.fire = function (type, ev) {
  var list = this._listeners[type] || [];
  for (var i = 0; i < list.length; i++) list[i].call(this, ev || {});
};
FakeNode.prototype.querySelector = function (sel) {
  var m = sel.match(/^\[data-pw-hid="(\d+)"\]$/);
  var all = this.querySelectorAll(sel);
  return all.length ? all[0] : null;
};
FakeNode.prototype.querySelectorAll = function (sel) {
  var m = sel.match(/^\[data-pw-hid="(\d+)"\]$/);
  var tag = sel.match(/^([a-zA-Z][a-zA-Z0-9]*)$/);
  var out = [];
  (function walk(n) {
    if (m && n.getAttribute && n.getAttribute("data-pw-hid") === m[1]) out.push(n);
    else if (tag && n.tagName === tag[1].toUpperCase()) out.push(n);
    var kids = n._kids || [];
    for (var i = 0; i < kids.length; i++) walk(kids[i]);
  })(this);
  return out;
};

function FakeEl(tag) {
  FakeNode.call(this, 1);
  this.tagName = String(tag).toUpperCase();
  this.attrs = {};
  this.style = { cssText: "" };
  this.classList = { _s: [], add: function (c) { this._s.push(c); } };
  this.type = "";
  this.value = "";
  this.checked = false;
  this.ownerDocument = null;
}
FakeEl.prototype = Object.create(FakeNode.prototype);
FakeEl.prototype.constructor = FakeEl;
FakeEl.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
FakeEl.prototype.getAttribute = function (k) {
  return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
};
FakeEl.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
FakeEl.prototype.insertAdjacentHTML = function (_pos, html) {
  this.appendChild(new FakeText(html));
};
Object.defineProperty(FakeEl.prototype, "textContent", {
  get: function () {
    return this._kids.map(function (k) { return k.textContent; }).join("");
  },
  set: function (v) {
    this._kids = [];
    if (v !== "" && v != null) this.appendChild(new FakeText(String(v)));
  }
});

function FakeText(text) {
  FakeNode.call(this, 3);
  this.textContent = String(text);
  this.nodeValue = this.textContent;
  this.ownerDocument = null;
}
FakeText.prototype = Object.create(FakeNode.prototype);
FakeText.prototype.constructor = FakeText;

function FakeComment(text) {
  FakeNode.call(this, 8);
  this.nodeValue = String(text);
  this.ownerDocument = null;
}
FakeComment.prototype = Object.create(FakeNode.prototype);
FakeComment.prototype.constructor = FakeComment;

function FakeFrag() {
  FakeNode.call(this, 11);
  this.ownerDocument = null;
}
FakeFrag.prototype = Object.create(FakeNode.prototype);
FakeFrag.prototype.constructor = FakeFrag;

function makeDocument() {
  var app = new FakeEl("div");
  app.setAttribute("id", "app");
  var doc = {
    _app: app,
    createElement: function (t) { var e = new FakeEl(t); e.ownerDocument = doc; return e; },
    createTextNode: function (t) { var e = new FakeText(t); e.ownerDocument = doc; return e; },
    createComment: function (t) { var e = new FakeComment(t); e.ownerDocument = doc; return e; },
    createDocumentFragment: function () { var e = new FakeFrag(); e.ownerDocument = doc; return e; },
    getElementById: function (id) { return id === "app" ? app : null; },
    body: new FakeEl("body")
  };
  app.ownerDocument = doc;
  doc.body.ownerDocument = doc;
  return doc;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { makeDocument: makeDocument, FakeEl: FakeEl };
} else {
  globalThis.__fakedom = { makeDocument: makeDocument };
}
