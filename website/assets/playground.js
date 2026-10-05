// PyWeb playground. The real PyWeb (compiler, server rendering and your
// @server functions) runs in this tab on Pyodide (CPython compiled to
// WebAssembly). The preview iframe talks to it through a fetch bridge, so
// apps behave as they would under `pyweb dev`. Nothing leaves the browser.
(() => {
  const cfg = JSON.parse(document.getElementById("pg-config").textContent);
  const $ = (id) => document.getElementById(id);
  const editor = $("pg-source");
  const hl = $("pg-hl");
  const gutter = $("pg-gutter");
  const statusEl = $("pg-status");
  const errorEl = $("pg-error");
  const frame = $("pg-frame");
  const stage = $("pg-stage");
  const urlBox = $("pg-url");
  const pageSel = $("pg-page");
  const exampleSel = $("pg-example");
  const jsOut = $("pg-js");
  const jsInfo = $("pg-jsinfo");
  const placeOut = $("pg-place");
  const consoleOut = $("pg-console");
  const countEl = $("pg-count");
  const draftEl = $("pg-draft");
  const DRAFT_KEY = "pyweb-playground-draft";

  let host = null;          // Python functions: load, request
  let runtimeUrl = null;    // blob: URL of the PyWeb browser runtime
  let pages = [];
  let path = "/";
  let timer = null;
  let errorLine = null;
  let example = "counter";  // the example the code started from ("" for shared code)
  let unseen = 0;
  const blobs = [];

  const status = (text, busy = false) => {
    statusEl.textContent = text;
    statusEl.classList.toggle("busy", busy);
  };
  const store = {
    get() { try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "null"); } catch { return null; } },
    set(v) { try { localStorage.setItem(DRAFT_KEY, JSON.stringify(v)); } catch { /* private mode */ } },
    clear() { try { localStorage.removeItem(DRAFT_KEY); } catch { /* private mode */ } },
  };

  // ------------------------------------------------------ highlighting
  const PY_KW = new Set(("False None True and as assert async await break class continue def del elif else except " +
    "finally for from global if import in is lambda nonlocal not or pass raise return try while with yield").split(" "));
  const PY_BUILTIN = new Set(("len str int float bool list dict set tuple range print enumerate zip sorted min max " +
    "sum any all isinstance abs round").split(" "));
  const TOKEN = new RegExp([
    /(?<comment>#[^\n]*)/,
    /(?<string>[rbfRBF]?(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?))/,
    /(?<tag><\/?[A-Za-z][\w.-]*|\/?>)/,
    /(?<deco>@[\w.]+)/,
    /(?<number>\b\d[\d_]*(?:\.\d+)?\b)/,
    /(?<name>[A-Za-z_][\w]*)/,
    /(?<brace>[{}])/,
  ].map((r) => r.source).join("|"), "g");
  const escHtml = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  function highlight(code) {
    let out = "", last = 0;
    TOKEN.lastIndex = 0;
    for (let m; (m = TOKEN.exec(code));) {
      if (m[0] === "") { TOKEN.lastIndex++; continue; }
      out += escHtml(code.slice(last, m.index));
      const g = m.groups, text = escHtml(m[0]);
      const has = (k) => g[k] !== undefined;
      let cls = has("comment") ? "c" : has("string") ? "s" : has("tag") ? "t" : has("deco") ? "d"
        : has("number") ? "n" : has("brace") ? "p" : "";
      if (has("name")) cls = PY_KW.has(m[0]) ? "k" : PY_BUILTIN.has(m[0]) ? "b" : "";
      out += cls ? `<span class="${cls}">${text}</span>` : text;
      last = m.index + m[0].length;
    }
    out += escHtml(code.slice(last));
    if (errorLine) {
      const lines = out.split("\n");
      if (lines[errorLine - 1] !== undefined) lines[errorLine - 1] = `<mark class="err">${lines[errorLine - 1] || " "}</mark>`;
      out = lines.join("\n");
    }
    return out + "\n";   // keeps the last line's height when it's empty
  }

  // ----------------------------------------------------------- editor
  function paint() {
    const n = editor.value.split("\n").length;
    let nums = "";
    for (let i = 1; i <= n; i++) nums += (i === errorLine ? `<b>${i}</b>` : i) + "\n";
    gutter.innerHTML = nums;
    hl.innerHTML = highlight(editor.value);
    sync();
  }
  function sync() {
    gutter.scrollTop = editor.scrollTop;
    hl.scrollTop = editor.scrollTop;
    hl.scrollLeft = editor.scrollLeft;
  }
  editor.addEventListener("scroll", sync);
  editor.addEventListener("input", () => {
    paint();
    saveDraft();
    clearTimeout(timer);
    timer = setTimeout(run, 650);
  });
  editor.addEventListener("keydown", (e) => {
    const { selectionStart: s, selectionEnd: end, value } = editor;
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); run(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); download(); return; }
    if (e.key === "Tab") {
      e.preventDefault();
      const lineStart = value.lastIndexOf("\n", s - 1) + 1;
      if (e.shiftKey) {
        const line = value.slice(lineStart);
        const drop = line.match(/^ {1,4}/);
        if (drop) {
          editor.setRangeText("", lineStart, lineStart + drop[0].length, "preserve");
          editor.setSelectionRange(Math.max(lineStart, s - drop[0].length), Math.max(lineStart, end - drop[0].length));
        }
      } else if (s !== end && value.slice(s, end).includes("\n")) {
        const block = value.slice(lineStart, end).replace(/^/gm, "    ");
        editor.setRangeText(block, lineStart, end, "select");
      } else {
        editor.setRangeText("    ", s, end, "end");
      }
      editor.dispatchEvent(new Event("input"));
    } else if (e.key === "Enter" && !e.isComposing) {
      const lineStart = value.lastIndexOf("\n", s - 1) + 1;
      const line = value.slice(lineStart, s);
      let indent = line.match(/^\s*/)[0];
      if (/:\s*(#.*)?$/.test(line)) indent += "    ";
      e.preventDefault();
      editor.setRangeText("\n" + indent, s, end, "end");
      editor.dispatchEvent(new Event("input"));
    }
  });

  function edited() {
    const original = example && cfg.examples[example] ? cfg.examples[example].source : null;
    return editor.value !== original;
  }
  function saveDraft() {
    draftEl.hidden = !edited();
    if (edited()) store.set({ example, source: editor.value });
    else store.clear();
  }

  function showError(err) {
    errorLine = err && err.line ? err.line : null;
    if (!err) {
      errorEl.hidden = true;
    } else {
      errorEl.hidden = false;
      errorEl.innerHTML = "";
      const head = document.createElement(err.line ? "button" : "strong");
      head.textContent = err.line ? `Line ${err.line}` : "Error";
      if (err.line) { head.type = "button"; head.title = "Go to the line"; head.addEventListener("click", () => goToLine(err.line)); }
      const msg = document.createElement("span");
      msg.textContent = err.message;
      errorEl.append(head, msg);
      if (err.hint) {
        const hint = document.createElement("em");
        hint.textContent = err.hint;
        errorEl.append(hint);
      }
    }
    paint();
  }

  function goToLine(n) {
    const lines = editor.value.split("\n");
    const at = lines.slice(0, n - 1).reduce((sum, l) => sum + l.length + 1, 0);
    editor.focus();
    editor.setSelectionRange(at, at + (lines[n - 1] || "").length);
    const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 21;
    editor.scrollTop = Math.max(0, (n - 4) * lineHeight);
    sync();
  }

  // ---------------------------------------------------------- console
  function log(kind, text, detail = "") {
    const empty = consoleOut.querySelector(".pg-empty");
    if (empty) empty.remove();
    const row = document.createElement("div");
    row.className = "pg-row " + kind;
    const tag = document.createElement("span");
    tag.className = "pg-kind";
    tag.textContent = { print: "print", call: "server", error: "error", browser: "browser", warn: "warning" }[kind] || kind;
    const body = document.createElement("span");
    body.className = "pg-text";
    body.textContent = text;
    row.append(tag, body);
    if (detail) {
      const d = document.createElement("span");
      d.className = "pg-detail";
      d.textContent = detail;
      row.append(d);
    }
    consoleOut.append(row);
    while (consoleOut.children.length > 300) consoleOut.firstChild.remove();
    consoleOut.scrollTop = consoleOut.scrollHeight;
    if (document.querySelector('.pg-tabs [data-tab="console"]').getAttribute("aria-selected") !== "true") {
      unseen++;
      countEl.hidden = false;
      countEl.textContent = unseen > 99 ? "99+" : String(unseen);
      countEl.classList.toggle("bad", kind === "error" || countEl.classList.contains("bad"));
    }
  }
  function printed(output) {
    for (const line of (output || "").replace(/\n$/, "").split("\n")) if (line) log("print", line);
  }

  // ------------------------------------------------------------ python
  function call(fn, ...args) { return JSON.parse(host[fn](...args)); }

  function request(method, url, body = "", type = "") {
    const r = call("request", method, url, body || "", type || "");
    printed(r.output);
    return r;
  }

  async function boot() {
    try {
      status("Loading Python (about 8 MB, cached after the first visit)…", true);
      const base = new URL(cfg.pyodide, location.href).href;
      const { loadPyodide } = await import(base + "pyodide.mjs");
      const py = await loadPyodide({ indexURL: base });
      status("Loading PyWeb…", true);
      const [zip, runtime, hostPy, ...statics] = await Promise.all([
        fetch(cfg.base + "pyweb.zip").then((r) => r.arrayBuffer()),
        fetch(cfg.base + "runtime.js").then((r) => r.text()),
        fetch(cfg.base + "host.py").then((r) => r.text()),
        ...cfg.static.map((name) => fetch(cfg.base + "static/" + name).then((r) => r.text())),
      ]);
      py.unpackArchive(new Uint8Array(zip), "zip", { extractDir: "/home/pyodide/lib" });
      py.runPython(hostPy);
      host = { load: py.globals.get("load"), request: py.globals.get("request") };
      cfg.static.forEach((name, i) => py.globals.get("install_static")(name, statics[i]));
      runtimeUrl = URL.createObjectURL(new Blob([runtime], { type: "text/javascript" }));
      run();
    } catch (err) {
      status("Couldn't start Python in this browser: " + (err && err.message ? err.message : err));
    }
  }

  // ----------------------------------------------------------- running
  function run() {
    if (!host) return;
    clearTimeout(timer);
    const t0 = performance.now();
    let res;
    try {
      res = call("load", editor.value);
    } catch (err) {
      showError({ message: String(err.message || err) });
      return;
    }
    printed(res.output);
    if (!res.ok) {
      showError(res.error);
      status("Fix the error to update the preview");
      return;
    }
    showError(null);
    pages = res.pages;
    pageSel.innerHTML = "";
    for (const p of pages) {
      const opt = document.createElement("option");
      opt.value = p.route;
      opt.textContent = `${p.name}  ${p.route}`;
      pageSel.append(opt);
    }
    const routes = pages.map((p) => p.route);
    const keep = pages.some((p) => matches(p.route, path));
    navigate(keep ? path : sample(routes[0] || "/"));
    status(`Compiled and rendered in ${Math.round(performance.now() - t0)} ms`);
  }

  const routeRe = (route) => new RegExp("^" + route.replace(/\{\w+\}/g, "[^/]+") + "$");
  const matches = (route, p) => routeRe(route).test(p.split("?")[0]);
  const sample = (route) => route.replace(/\{\w+\}/g, "1");

  function pageFor(p) { return pages.find((pg) => matches(pg.route, p)); }

  function blobFor(text, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    blobs.push(url);
    return url;
  }

  function navigate(target) {
    let r = request("GET", target);
    for (let hops = 0; [301, 302, 303, 307, 308].includes(r.status) && hops < 5; hops++) {
      target = r.headers.location || "/";
      r = request("GET", target);
    }
    if (r.status >= 500) log("error", `GET ${target} answered ${r.status}`);
    path = target;
    urlBox.value = target;
    const page = pageFor(target);
    if (page) pageSel.value = page.route;
    showCompiled(page);
    while (blobs.length) URL.revokeObjectURL(blobs.pop());
    frame.srcdoc = preview(r.body || `<p>${r.status}</p>`);
  }

  // Point a module's imports of the runtime (and markdown.js) at their blob: URLs.
  let markdownUrl = null;
  function linkModules(js) {
    js = js.replace(/from\s+"\.\/runtime\.js"/g, `from "${runtimeUrl}"`);
    if (/from\s+"\.\/markdown\.js"/.test(js)) {
      if (!markdownUrl) {
        const md = request("GET", "/static/markdown.js").body.replace(/from\s+"\.\/runtime\.js"/g, `from "${runtimeUrl}"`);
        markdownUrl = URL.createObjectURL(new Blob([md], { type: "text/javascript" }));
      }
      js = js.replace(/from\s+"\.\/markdown\.js"/g, `from "${markdownUrl}"`);
    }
    return js;
  }

  // Page HTML with /static/ files turned into blob: URLs and a bridge
  // that sends the app's own requests (/__pyweb/...) to Python and its
  // console messages and errors to the playground's Console.
  function preview(html) {
    html = html.replace(/(src|href)="(\/static\/[^"]+)"/g, (m, attr, url) => {
      const res = request("GET", url.replace(/&amp;/g, "&"));
      if (res.status !== 200) return `${attr}="${blobFor("", "text/plain")}"`; // missing: don't ask the docs site
      let body = res.body;
      const type = (res.headers["content-type"] || "text/plain").split(";")[0];
      if (type === "text/javascript") body = linkModules(body);
      return `${attr}="${blobFor(body, type)}"`;
    });
    const bridge = `<script>(() => {
      const host = parent.__pywebPlayground;
      const realFetch = window.fetch.bind(window);
      window.EventSource = undefined; // live updates use the polling path here
      window.fetch = (input, init) => {
        const url = typeof input === "string" ? input : input.url;
        return url.startsWith("/__pyweb/") ? host.fetch(url, init || {}) : realFetch(input, init);
      };
      const show = (v) => { try { return typeof v === "string" ? v : JSON.stringify(v); } catch { return String(v); } };
      for (const [name, kind] of [["log", "browser"], ["info", "browser"], ["warn", "warn"], ["error", "error"]]) {
        const original = console[name].bind(console);
        console[name] = (...args) => { host.log(kind, args.map(show).join(" ")); original(...args); };
      }
      addEventListener("error", (e) => host.log("error", e.message || "error"));
      addEventListener("unhandledrejection", (e) => host.log("error", (e.reason && e.reason.message) || String(e.reason)));
      // Links and window.location changes go to the app, not the docs site.
      if (window.navigation) {
        navigation.addEventListener("navigate", (e) => {
          const to = new URL(e.destination.url);
          if (to.origin !== parent.location.origin || !e.cancelable || e.hashChange) return;
          e.preventDefault();
          host.navigate(to.pathname + to.search);
        });
      } else {
        document.addEventListener("click", (e) => {
          const a = e.target.closest && e.target.closest("a[href]");
          const href = a && a.getAttribute("href");
          if (href && href.startsWith("/") && !href.startsWith("//")) { e.preventDefault(); host.navigate(href); }
        });
      }
    })();<\/script>`;
    // A readable default font; the app's own stylesheet still wins.
    const base = "<style>body{font:16px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif;margin:16px}</style>";
    return html.includes("<head>") ? html.replace("<head>", "<head>" + bridge + base) : bridge + base + html;
  }

  window.__pywebPlayground = {
    fetch: async (url, init) => {
      const headers = new Headers(init.headers || {});
      const method = init.method || "GET";
      const r = request(method, url, typeof init.body === "string" ? init.body : "", headers.get("content-type") || "");
      const m = url.match(/^\/__pyweb\/rpc\/(\w+)/);
      if (m) {
        let args = "";
        try { args = JSON.stringify(JSON.parse(init.body || "{}").args || {}); } catch { /* not JSON */ }
        let outcome = `${r.status}`;
        if (r.status >= 400) {
          try { outcome += " " + JSON.parse(r.body).error.message; } catch { /* plain body */ }
        }
        log(r.status >= 400 ? "error" : "call", `${m[1]}(${args.length > 120 ? args.slice(0, 117) + "…" : args})`,
          `${outcome} · ${r.ms} ms`);
      }
      const empty = r.status === 204 || r.status === 304;
      return new Response(empty ? null : r.body, { status: r.status, headers: r.headers });
    },
    navigate: (href) => setTimeout(() => navigate(href)), // after the click/handler finishes
    log: (kind, text) => log(kind, text),
  };

  // ------------------------------------------------------------ panels
  async function gzipSize(text) {
    if (typeof CompressionStream === "undefined") return null;
    const stream = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
    return (await new Response(stream).arrayBuffer()).byteLength;
  }
  const kb = (n) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

  function showCompiled(page) {
    jsOut.innerHTML = page && page.js ? highlightJs(page.js)
      : '<span class="c">// This page is static: it ships no JavaScript, only server-rendered HTML.</span>';
    jsInfo.textContent = page && page.js ? `${page.name}.js · ${kb(page.js.length)}` : "No JavaScript for this page";
    if (page && page.js) {
      gzipSize(page.js).then((n) => { if (n) jsInfo.textContent = `${page.name}.js · ${kb(page.js.length)} · ${kb(n)} gzipped (plus the shared runtime, cached once)`; });
    }
    placeOut.innerHTML = "";
    if (!page) return;
    const table = document.createElement("table");
    table.innerHTML = "<thead><tr><th>Name</th><th>Runs</th><th>Why</th></tr></thead>";
    const body = document.createElement("tbody");
    for (const [name, [where, why]] of Object.entries(page.placement)) {
      const tr = document.createElement("tr");
      for (const [text, cls] of [[name, "name"], [where, "where " + where.replace(/\W/g, "")], [why, ""]]) {
        const td = document.createElement("td");
        td.textContent = text;
        if (cls) td.className = cls;
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(body);
    placeOut.append(table);
  }

  const JS_KW = new Set(("const let var function return if else for of in while async await import from export new try " +
    "catch finally throw true false null undefined break continue typeof").split(" "));
  function highlightJs(code) {
    return escHtml(code).replace(/(\/\/[^\n]*)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|\b(\d+(?:\.\d+)?)\b|(?<![\w$])([A-Za-z_$][\w$]*)/g,
      (m, comment, str, num, word) => comment ? `<span class="c">${m}</span>` : str ? `<span class="s">${m}</span>`
        : num ? `<span class="n">${m}</span>` : JS_KW.has(word) ? `<span class="k">${m}</span>`
          : word.startsWith("$") ? `<span class="t">${m}</span>` : m);
  }

  function selectTab(name) {
    for (const t of document.querySelectorAll(".pg-tabs [role=tab]")) t.setAttribute("aria-selected", String(t.dataset.tab === name));
    stage.hidden = name !== "preview";
    document.querySelector(".pg-urlbar").classList.toggle("dim", name !== "preview");
    consoleOut.hidden = name !== "console";
    $("pg-jswrap").hidden = name !== "js";
    placeOut.hidden = name !== "place";
    if (name === "console") {
      unseen = 0;
      countEl.hidden = true;
      countEl.classList.remove("bad");
    }
  }
  for (const tab of document.querySelectorAll(".pg-tabs [role=tab]")) {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
  }
  pageSel.addEventListener("change", () => navigate(sample(pageSel.value)));
  urlBox.addEventListener("keydown", (e) => { if (e.key === "Enter") navigate(urlBox.value.trim() || "/"); });
  $("pg-reload").addEventListener("click", () => host && navigate(path));
  $("pg-run").addEventListener("click", run);

  for (const b of document.querySelectorAll(".pg-sizes button")) {
    b.addEventListener("click", () => {
      for (const o of document.querySelectorAll(".pg-sizes button")) o.setAttribute("aria-pressed", String(o === b));
      frame.style.maxWidth = b.dataset.width ? b.dataset.width + "px" : "";
      stage.classList.toggle("framed", Boolean(b.dataset.width));
    });
  }

  // Phones: one pane at a time.
  for (const b of document.querySelectorAll(".pg-switch button")) {
    b.addEventListener("click", () => {
      for (const o of document.querySelectorAll(".pg-switch button")) o.setAttribute("aria-selected", String(o === b));
      $("pg-split").dataset.pane = b.dataset.pane;
    });
  }

  // Drag the divider to resize the panes (remembered).
  const split = $("pg-split");
  const resizer = $("pg-resize");
  const setSplit = (fraction) => {
    const f = Math.min(0.75, Math.max(0.25, fraction));
    split.style.gridTemplateColumns = `minmax(0, ${f}fr) 10px minmax(0, ${1 - f}fr)`;
    try { localStorage.setItem("pyweb-playground-split", String(f)); } catch { /* private mode */ }
  };
  try { const f = parseFloat(localStorage.getItem("pyweb-playground-split")); if (f) setSplit(f); } catch { /* private mode */ }
  resizer.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    resizer.setPointerCapture(e.pointerId);
    frame.style.pointerEvents = "none";   // the iframe would swallow the drag
    const box = split.getBoundingClientRect();
    const move = (ev) => setSplit((ev.clientX - box.left) / box.width);
    const up = () => {
      frame.style.pointerEvents = "";
      resizer.removeEventListener("pointermove", move);
      resizer.removeEventListener("pointerup", up);
    };
    resizer.addEventListener("pointermove", move);
    resizer.addEventListener("pointerup", up);
  });
  resizer.addEventListener("keydown", (e) => {
    const now = parseFloat(localStorage.getItem("pyweb-playground-split") || "0.5");
    if (e.key === "ArrowLeft") setSplit(now - 0.05);
    if (e.key === "ArrowRight") setSplit(now + 0.05);
  });

  // ---------------------------------------------- examples and sharing
  async function pack(text) {
    if (typeof CompressionStream === "undefined") return "t" + btoa(unescape(encodeURIComponent(text)));
    const stream = new Blob([text]).stream().pipeThrough(new CompressionStream("deflate-raw"));
    const bytes = new Uint8Array(await new Response(stream).arrayBuffer());
    let bin = "";
    for (const b of bytes) bin += String.fromCharCode(b);
    return "z" + btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  async function unpack(code) {
    if (code[0] === "t") return decodeURIComponent(escape(atob(code.slice(1))));
    const b64 = code.slice(1).replace(/-/g, "+").replace(/_/g, "/");
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return new Response(stream).text();
  }

  $("pg-share").addEventListener("click", async () => {
    const url = location.href.split("#")[0] + "#code=" + (await pack(editor.value));
    history.replaceState(null, "", url);
    try {
      await navigator.clipboard.writeText(url);
      status("Link copied");
    } catch {
      status("Link is in the address bar");
    }
  });

  function download() {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([editor.value], { type: "text/plain" }));
    a.download = "app.pyweb";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    status("Saved app.pyweb: run it with  pyweb dev app.pyweb");
  }
  $("pg-download").addEventListener("click", download);

  $("pg-reset").addEventListener("click", () => {
    const name = example || "counter";
    store.clear();
    exampleSel.value = name;
    history.replaceState(null, "", location.pathname + "#" + name);
    loadExample(name);
    status("Back to the original example");
  });

  function loadExample(name, source) {
    example = name;
    editor.value = source ?? cfg.examples[name].source;
    path = "/";
    editor.scrollTop = 0;
    saveDraft();
    paint();
    run();
  }
  for (const [name, ex] of Object.entries(cfg.examples)) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = ex.title;
    exampleSel.append(opt);
  }
  exampleSel.addEventListener("change", () => {
    if (edited() && !confirm("Switch examples? Your edits to this one will be lost (Download or Share them first to keep them).")) {
      exampleSel.value = example;
      return;
    }
    history.replaceState(null, "", location.pathname + "#" + exampleSel.value);
    store.clear();
    loadExample(exampleSel.value);
  });

  (async () => {
    const hash = location.hash.slice(1);
    const draft = store.get();
    let restored = false;
    if (hash.startsWith("code=")) {
      example = "";
      try { editor.value = await unpack(hash.slice(5)); } catch { example = "counter"; editor.value = cfg.examples.counter.source; }
      exampleSel.value = example;
    } else {
      const named = cfg.examples[hash] ? hash : "";
      if (draft && draft.source && (!named || draft.example === named)) {
        example = draft.example && cfg.examples[draft.example] ? draft.example : "";
        editor.value = draft.source;
        restored = true;
      } else {
        example = named || "counter";
        editor.value = cfg.examples[example].source;
      }
      exampleSel.value = example;
    }
    draftEl.hidden = !edited();   // a draft for another example stays saved until you edit this one
    paint();
    await boot();
    if (restored && host) status("Restored your last edits (Reset goes back to the example)");
  })();
})();
