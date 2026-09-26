/* PyWeb tiny signals runtime (no dependencies).
 * Provides: signal/computed/effect, liveList (keyed, fine-grained),
 * liveIf (conditional blocks), hydrate helpers, and the error overlay hook
 * window.__pyweb_error(map).
 */
(function (global) {
  "use strict";

  var current = null;
  var collectors = [];

  function Signal(initial, name) {
    this._v = initial;
    this._subs = [];
    this.name = name || "";
  }
  Signal.prototype.get = function () {
    if (current) current._track(this);
    return this._v;
  };
  Signal.prototype.peek = function () { return this._v; };
  Signal.prototype.set = function (v) {
    if (v === this._v) return;
    this._v = v;
    var subs = this._subs.slice();
    for (var i = 0; i < subs.length; i++) subs[i]();
  };
  Signal.prototype.update = function (fn) { this.set(fn(this.get())); };
  Signal.prototype.subscribe = function (fn) {
    this._subs.push(fn);
    var self = this;
    return function () {
      var i = self._subs.indexOf(fn);
      if (i >= 0) self._subs.splice(i, 1);
    };
  };

  function signal(initial, name) { return new Signal(initial, name); }

  function Computed(fn, name) {
    this._fn = fn;
    this.name = name || "";
    this._cached = undefined;
    this._deps = [];
    this._unsubs = [];
    this._dirty = true;
    this._subs = [];
    this._recompute();
  }
  Computed.prototype._track = function (dep) {
    if (this._deps.indexOf(dep) < 0) {
      this._deps.push(dep);
      var self = this;
      this._unsubs.push(dep.subscribe(function () { self._invalidate(); }));
    }
  };
  Computed.prototype._invalidate = function () {
    this._dirty = true;
    var subs = this._subs.slice();
    for (var i = 0; i < subs.length; i++) subs[i]();
  };
  Computed.prototype._recompute = function () {
    for (var i = 0; i < this._unsubs.length; i++) this._unsubs[i]();
    this._deps = [];
    this._unsubs = [];
    var prev = current;
    current = this;
    try { this._cached = this._fn(); } finally { current = prev; }
    this._dirty = false;
  };
  Computed.prototype.get = function () {
    if (current) current._track(this);
    if (this._dirty) this._recompute();
    return this._cached;
  };
  Computed.prototype.subscribe = function (fn) {
    this._subs.push(fn);
    var self = this;
    return function () {
      var i = self._subs.indexOf(fn);
      if (i >= 0) self._subs.splice(i, 1);
    };
  };

  function computed(fn, name) { return new Computed(fn, name); }

  function effect(fn) {
    var deps = [];
    var unsubs = [];
    function track(dep) {
      if (deps.indexOf(dep) < 0) {
        deps.push(dep);
        unsubs.push(dep.subscribe(run));
      }
    }
    function run() {
      for (var i = 0; i < unsubs.length; i++) unsubs[i]();
      deps = [];
      unsubs = [];
      var prev = current;
      current = { _track: track };
      try { fn(); } finally { current = prev; }
    }
    run();
    function dispose() {
      for (var i = 0; i < unsubs.length; i++) unsubs[i]();
      unsubs = [];
    }
    if (collectors.length) collectors[collectors.length - 1].push(dispose);
    return dispose;
  }

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* Effect disposer collector: while a collector is on top of the stack,
   * every effect() registers its disposer there (used for row/branch teardown). */
  function capture(fn) {
    var bag = [];
    collectors.push(bag);
    var result;
    try { result = fn(); } finally { collectors.pop(); }
    return { result: result, disposers: bag };
  }
  function disposeAll(bag) {
    bag.forEach(function (d) { try { d(); } catch (e) { /* noop */ } });
  }

  function _findComment(root, text) {
    var stack = [root];
    while (stack.length) {
      var n = stack.pop();
      var kids = (n && n.childNodes) || [];
      for (var i = 0; i < kids.length; i++) {
        var k = kids[i];
        if (k.nodeType === 8 && k.nodeValue === text) return k;
        if (k.childNodes && k.childNodes.length) stack.push(k);
      }
    }
    return null;
  }

  /* SSR handoff: replace <!--pw:hid--> ... <!--/pw:hid--> with a live anchor.
   * When markers are absent (nested regions, already wiped by an outer
   * takeover), fall back to a fresh anchor appended to the parent. */
  function takeover(parent, hid) {
    var anchor = parent.ownerDocument
      ? parent.ownerDocument.createComment("pw-live")
      : { nodeType: 8, nodeValue: "pw-live" };
    var start = _findComment(parent, "pw:" + hid);
    if (!start) {
      parent.appendChild(anchor);
      return anchor;
    }
    var host = start.parentNode || parent;
    host.insertBefore(anchor, start);
    var cur = start;
    while (cur) {
      var next = cur.nextSibling;
      host.removeChild(cur);
      if (cur.nodeType === 8 && cur.nodeValue === "/pw:" + hid) break;
      cur = next;
    }
    return anchor;
  }

  function _rowEls(row) {
    if (!row) return [];
    if (row.els) return row.els;
    if (row.el) return [row.el];
    return [];
  }

  /* Keyed live list: appending one item creates exactly one row; existing
   * rows keep their DOM nodes. Rows whose item identity changed are rebuilt
   * in place (only that row, never the whole list). */
  function liveList(anchor, getItems, renderRow, keyFn) {
    keyFn = keyFn || function (_item, i) { return "i:" + i; };
    var rows = new Map(); // key -> {els, item, i, _disposers}
    var order = [];
    function makeRow(item, i, key) {
      var cap = capture(function () { return renderRow(item, i, key); });
      var row = cap.result || { els: [] };
      row.item = item;
      row.i = i;
      row._disposers = cap.disposers;
      return row;
    }
    function killRow(key) {
      var row = rows.get(key);
      if (!row) return;
      rows.delete(key);
      disposeAll(row._disposers || []);
      if (row.dispose) { try { row.dispose(); } catch (e) { /* noop */ } }
      _rowEls(row).forEach(function (e) {
        if (e.parentNode) e.parentNode.removeChild(e);
      });
    }
    function sync() {
      var items = getItems() || [];
      var parent = anchor.parentNode;
      var seen = {};
      var nextOrder = [];
      for (var i = 0; i < items.length; i++) {
        var key = String(keyFn(items[i], i));
        seen[key] = true;
        nextOrder.push(key);
        var row = rows.get(key);
        if (!row) {
          rows.set(key, makeRow(items[i], i, key));
        } else if (row.item !== items[i]) {
          killRow(key);
          rows.set(key, makeRow(items[i], i, key));
        }
      }
      var victims = [];
      rows.forEach(function (_row, key) {
        if (!seen[key]) victims.push(key);
      });
      victims.forEach(killRow);
      order = nextOrder;
      // place rows in order before the anchor, moving only what is stale
      var ref = anchor;
      for (var j = order.length - 1; j >= 0; j--) {
        var r = rows.get(order[j]);
        var els = _rowEls(r);
        for (var q = els.length - 1; q >= 0; q--) {
          if (els[q].nextSibling !== ref) parent.insertBefore(els[q], ref);
          ref = els[q];
        }
      }
    }
    var dispose = effect(sync);
    return { sync: sync, dispose: dispose };
  }

  /* Live conditional: swaps branch DOM in place; branch effects are captured
   * and disposed on switch. */
  function liveIf(anchor, pick, branches) {
    var state = { idx: -1, nodes: [], disposers: [] };
    function sync() {
      var idx = pick();
      if (idx === state.idx) return;
      disposeAll(state.disposers);
      state.disposers = [];
      state.nodes.forEach(function (n) {
        if (n.parentNode) n.parentNode.removeChild(n);
      });
      state.nodes = [];
      state.idx = idx;
      if (idx < 0 || idx >= branches.length) return;
      var cap = capture(function () { return branches[idx]() || []; });
      state.disposers = cap.disposers;
      var parent = anchor.parentNode;
      cap.result.forEach(function (n) {
        parent.insertBefore(n, anchor);
        state.nodes.push(n);
      });
    }
    var dispose = effect(sync);
    return { sync: sync, dispose: dispose };
  }

  function dynText(node, fn) {
    function sync() { node.textContent = fn() == null ? "" : String(fn()); }
    return effect(sync);
  }

  function dynAttr(el, name, fn) {
    return effect(function () {
      var v = fn();
      if (v == null || v === false) el.removeAttribute(name);
      else el.setAttribute(name, String(v));
    });
  }

  /* Error overlay hook: maps a JS stack line/col through the sourcemap. */
  function makeErrorHook(map) {
    function hook(err) {
      var overlay = document.getElementById("__pyweb_overlay");
      if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "__pyweb_overlay";
        overlay.setAttribute("style",
          "position:fixed;left:0;right:0;bottom:0;background:#300;color:#fff;"
          + "font:12px monospace;padding:8px;z-index:9999;white-space:pre-wrap;");
        document.body.appendChild(overlay);
      }
      var msg = err && (err.stack || err.message || String(err));
      overlay.textContent = "PyWeb error: " + msg;
      return overlay;
    }
    hook.map = map || null;
    return hook;
  }

  var api = {
    signal: signal,
    computed: computed,
    effect: effect,
    esc: esc,
    liveList: liveList,
    liveIf: liveIf,
    dynText: dynText,
    dynAttr: dynAttr,
    takeover: takeover,
    capture: capture,
    makeErrorHook: makeErrorHook,
    _Signal: Signal,
    _Computed: Computed
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.PyWeb = api;
  if (!global.__pyweb_error) global.__pyweb_error = makeErrorHook(null);
})(typeof window !== "undefined" ? window : globalThis);
