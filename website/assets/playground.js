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
  const filesEl = $("pg-files");
  const userSel = $("pg-user");
  const reqOut = $("pg-requests");
  const reqCount = $("pg-reqcount");
  const dbOut = $("pg-db");
  const DRAFT_KEY = "pyweb-playground-draft";

  let host = null;          // Python functions: load, request, ...
  let pyodide = null;
  let sqlite = null;        // SQLite for apps with a database: null, "loading", "ok" or "failed"
  let runtimeUrl = null;    // blob: URL of the PyWeb browser runtime
  let pages = [];
  let path = "/";
  let timer = null;
  let errorLine = null;
  let example = "counter";  // the example the code started from ("" for shared code)
  let unseen = 0;
  let files = { "app.pyweb": "" };   // every file in the editor; app.pyweb is the app
  let current = "app.pyweb";
  let errorFile = "app.pyweb";
  let hasDatabase = false;
  let lastRequest = "";              // newest request id the Requests tab has shown
  let unseenRequests = 0;
  let dbTable = "";
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
    if (errorLine && errorFile === current) {
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
    for (let i = 1; i <= n; i++) nums += (i === errorLine && errorFile === current ? `<b>${i}</b>` : i) + "\n";
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
    files[current] = editor.value;
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
    return Object.keys(files).length !== 1 || files["app.pyweb"] !== original;
  }
  function saveDraft() {
    draftEl.hidden = !edited();
    if (edited()) store.set({ example, files });
    else store.clear();
  }

  // ------------------------------------------------------------ files
  const FILE_NAME = /^(?:static\/)?[A-Za-z_][\w-]*\.(?:pyweb|py|css|js|json|txt|md)$/;
  function renderFiles() {
    filesEl.innerHTML = "";
    for (const name of Object.keys(files)) {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(name === current));
      b.textContent = name;
      b.addEventListener("click", (e) => {
        if (e.target.classList.contains("x")) return;
        openFile(name);
      });
      if (name !== "app.pyweb") {
        const x = document.createElement("span");
        x.className = "x";
        x.textContent = "×";
        x.title = `Remove ${name}`;
        x.addEventListener("click", () => {
          if (!confirm(`Remove ${name}?`)) return;
          delete files[name];
          if (current === name) current = "app.pyweb";
          openFile(current);
          saveDraft();
          run();
        });
        b.append(x);
      }
      filesEl.append(b);
    }
  }
  function openFile(name) {
    current = name;
    editor.value = files[name] ?? "";
    editor.scrollTop = 0;
    renderFiles();
    paint();
  }
  $("pg-addfile").addEventListener("click", () => {
    const name = (prompt("New file: a .pyweb module (import its components with  from widgets import Card), " +
      "a .py helper, or static/app.css", "widgets.pyweb") || "").trim();
    if (!name) return;
    if (!FILE_NAME.test(name) || files[name] !== undefined) {
      status(files[name] !== undefined ? `${name} already exists` : "Use a name like widgets.pyweb, helpers.py or static/app.css");
      return;
    }
    files[name] = name.endsWith(".pyweb")
      ? "from pyweb import component\n\n\n@component\ndef Card(title):\n    <div class=\"card\">{title}</div>\n"
      : name.endsWith(".css") ? "/* linked from App(stylesheets=[\"/static/app.css\"]) */\n" : "";
    openFile(name);
    saveDraft();
  });

  function showError(err) {
    errorLine = err && err.line ? err.line : null;
    errorFile = (err && err.file && files[err.file] !== undefined) ? err.file : "app.pyweb";
    if (!err) {
      errorEl.hidden = true;
    } else {
      errorEl.hidden = false;
      errorEl.innerHTML = "";
      const head = document.createElement(err.line ? "button" : "strong");
      const where = errorFile !== "app.pyweb" ? `${errorFile}, line` : "Line";
      head.textContent = err.line ? `${where} ${err.line}` : "Error";
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
    if (errorFile !== current) openFile(errorFile);
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
    tag.textContent = { print: "print", call: "server", error: "error", browser: "browser", warn: "warning", job: "job" }[kind] || kind;
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
    for (const j of r.jobs || []) {
      log(j.state === "done" ? "job" : "error", `${j.name} ${j.state === "done" ? "ran" : j.state}`, j.error || "");
    }
    if (!url.startsWith("/static/")) refreshPanels(method !== "GET");
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
      pyodide = py;
      py.runPython(hostPy);
      host = {};
      for (const fn of ["load", "request", "recent", "tables", "rows", "sign_in"]) host[fn] = py.globals.get(fn);
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
      const others = Object.fromEntries(Object.entries(files).filter(([n]) => n !== "app.pyweb"));
      res = call("load", files["app.pyweb"], JSON.stringify(others));
    } catch (err) {
      showError({ message: String(err.message || err) });
      return;
    }
    printed(res.output);
    if (!res.ok && res.needs === "sqlite3") {
      if (sqlite === "loading") return;                 // run() again once it's here
      if (sqlite === null) {
        sqlite = "loading";
        status("Loading SQLite for the app's database…", true);
        pyodide.loadPackage("sqlite3", { messageCallback: () => {}, errorCallback: () => {} })
          .then(() => { sqlite = "ok"; }, () => { sqlite = "failed"; })
          .then(run);
        return;
      }
      showError({ message: "This browser couldn't load SQLite, so apps with a database can't run here. " +
        "Download the files and run them with  pyweb dev app.pyweb" });
      status("Apps with a database need SQLite");
      return;
    }
    if (!res.ok) {
      showError(res.error);
      status("Fix the error to update the preview");
      return;
    }
    showError(null);
    pages = res.pages;
    hasDatabase = res.database;
    lastRequest = "";
    dbTable = "";
    reqOut.querySelectorAll(".pg-req").forEach((d) => d.remove());
    if (userSel.value) {          // still signed in (the cookie survives edits): say so in the picker
      try { call("sign_in", userSel.value); } catch { userSel.value = ""; }
    }
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

  // Point a module's imports of the runtime and its add-ons (markdown.js, forms.js,
  // live.js) at their blob: URLs.
  const addonUrls = {};
  function linkModules(js) {
    js = js.replace(/from\s+"\.\/runtime\.js"/g, `from "${runtimeUrl}"`);
    return js.replace(/(from\s+|import\s+)"\.\/(markdown|forms|live)\.js"/g, (m, kw, name) => {
      if (!addonUrls[name]) {
        const src = request("GET", `/static/${name}.js`).body.replace(/from\s+"\.\/runtime\.js"/g, `from "${runtimeUrl}"`);
        addonUrls[name] = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
      }
      return `${kw}"${addonUrls[name]}"`;
    });
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
      window.WebSocket = undefined;
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

  // ----------------------------------------------- requests and database
  const tabOpen = (name) => document.querySelector(`.pg-tabs [data-tab="${name}"]`).getAttribute("aria-selected") === "true";
  const ms = (v) => (v < 10 ? v.toFixed(1) : Math.round(v)) + " ms";
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };

  function refreshPanels(wrote) {
    if (!host) return;
    let fresh = [];
    try { fresh = call("recent", lastRequest).requests; } catch { return; }
    if (fresh.length) {
      lastRequest = fresh[fresh.length - 1].id;
      const empty = reqOut.querySelector(".pg-empty");
      if (empty) empty.hidden = true;
      for (const r of fresh) reqOut.prepend(requestRow(r));
      while (reqOut.querySelectorAll(".pg-req").length > 80) reqOut.querySelector(".pg-req:last-of-type").remove();
      if (!tabOpen("requests")) {
        unseenRequests += fresh.length;
        reqCount.hidden = false;
        reqCount.textContent = unseenRequests > 99 ? "99+" : String(unseenRequests);
        reqCount.classList.toggle("bad", fresh.some((r) => r.error || (r.warnings || []).length) || reqCount.classList.contains("bad"));
      }
    }
    if (wrote && tabOpen("db")) showDatabase();
  }

  function requestRow(r) {
    const d = el("details", "pg-req");
    const s = el("summary");
    s.append(el("span", "m", r.method), el("span", "p", r.path), el("span", "st" + (r.status >= 400 || r.error ? " bad" : ""), String(r.status)),
      el("span", "q", `${r.queries} SQL`), el("span", "ms", ms(r.ms)));
    if ((r.warnings || []).length) s.querySelector(".q").append(" ", el("span", "warn", "N+1?"));
    d.append(s);
    const body = el("div", "body");
    if (r.error) body.append(el("h4", "", "Error"), el("pre", "", r.error));
    for (const w of r.warnings || []) body.append(el("div", "flag", "⚠ " + w));
    if ((r.sql || []).length) {
      const groups = new Map();
      for (const q of r.sql) { const g = groups.get(q.sql); if (g) { g.n++; g.ms += q.ms; } else groups.set(q.sql, { ...q, n: 1 }); }
      const t = el("table");
      for (const q of groups.values()) {
        const tr = el("tr");
        tr.append(el("td", "n", ms(q.ms)), el("td", "", q.sql + (q.n > 1 ? `   ×${q.n}` : q.params && q.params.length ? "   " + JSON.stringify(q.params) : "")));
        t.append(tr);
      }
      body.append(el("h4", "", "SQL"), t);
    }
    if ((r.spans || []).length) {
      const t = el("table");
      for (const sp of r.spans) { const tr = el("tr"); tr.append(el("td", "n", ms(sp.ms)), el("td", "", sp.name)); t.append(tr); }
      body.append(el("h4", "", "Spans"), t);
    }
    const ev = r.events || [];
    if (ev.length) {
      const t = el("table");
      for (const e of ev) {
        const tr = el("tr");
        const text = e.kind === "job" ? `queued ${e.name}` : e.kind === "mail" ? `email to ${(e.to || []).join(", ")}: ${e.subject}` : JSON.stringify(e);
        tr.append(el("td", "n", e.kind), el("td", "", text));
        t.append(tr);
      }
      body.append(el("h4", "", "Side effects"), t);
    }
    if (!body.childNodes.length) body.append(el("div", "pg-detail", `${r.route || ""} · no queries`));
    d.append(body);
    return d;
  }

  function showDatabase() {
    if (!host) return;
    dbOut.querySelectorAll("nav, .grid").forEach((n) => n.remove());
    const empty = dbOut.querySelector(".pg-empty");
    if (!hasDatabase) { empty.hidden = false; return; }
    let info;
    try { info = call("tables"); } catch (err) { empty.hidden = false; empty.textContent = String(err.message || err); return; }
    empty.hidden = true;
    const nav = el("nav");
    const tables = info.tables.filter((t) => !t.name.startsWith("pyweb_"));
    if (!dbTable || !tables.some((t) => t.name === dbTable)) {
      dbTable = (tables.find((t) => t.rows) || tables[0] || {}).name || "";
    }
    for (const t of tables) {
      const b = el("button");
      b.type = "button";
      b.setAttribute("aria-pressed", String(t.name === dbTable));
      b.append(el("b", "", t.name), el("span", "", String(t.rows)));
      b.addEventListener("click", () => { dbTable = t.name; showDatabase(); });
      nav.append(b);
    }
    const grid = el("div", "grid");
    if (dbTable) {
      const data = call("rows", dbTable, 200);
      const table = el("table");
      const head = el("tr");
      for (const c of data.columns || []) head.append(el("th", "", c));
      table.append(head);
      for (const row of data.rows || []) {
        const tr = el("tr");
        for (const v of row) tr.append(el("td", v === null ? "null" : "", v === null ? "null" : typeof v === "object" ? JSON.stringify(v) : String(v)));
        table.append(tr);
      }
      grid.append(table);
      if (!(data.rows || []).length) grid.append(el("p", "pg-empty", "No rows yet."));
    }
    dbOut.append(nav, grid);
  }

  userSel.addEventListener("change", () => {
    if (!host) return;
    try {
      const r = call("sign_in", userSel.value);
      printed(r.output);
      log("call", r.user ? `signed in as ${r.user}${(r.roles || []).length ? " (" + r.roles.join(", ") + ")" : ""}` : "signed out");
    } catch (err) {
      log("error", "couldn't sign in: " + (err.message || err));
      userSel.value = "";
    }
    navigate(path);
  });

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
    reqOut.hidden = name !== "requests";
    dbOut.hidden = name !== "db";
    if (name === "requests") {
      unseenRequests = 0;
      reqCount.hidden = true;
      reqCount.classList.remove("bad");
    }
    if (name === "db") showDatabase();
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
    const one = Object.keys(files).length === 1;
    const url = location.href.split("#")[0] + (one ? "#code=" + (await pack(files["app.pyweb"]))
      : "#files=" + (await pack(JSON.stringify(files))));
    history.replaceState(null, "", url);
    try {
      await navigator.clipboard.writeText(url);
      status("Link copied");
    } catch {
      status("Link is in the address bar");
    }
  });

  // A small zip writer (stored, not compressed) for downloading several files.
  const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
  const crc32 = (b) => { let c = 0xffffffff; for (const x of b) c = CRC[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  function zip(entries) {
    const enc = new TextEncoder(), parts = [], dir = [];
    let offset = 0;
    for (const [name, text] of entries) {
      const n = enc.encode(name), data = enc.encode(text), crc = crc32(data);
      const local = new DataView(new ArrayBuffer(30));
      [[0, 0x04034b50, 4], [4, 20, 2], [8, 0, 2], [14, crc, 4], [18, data.length, 4], [22, data.length, 4], [26, n.length, 2]]
        .forEach(([at, v, size]) => size === 4 ? local.setUint32(at, v, true) : local.setUint16(at, v, true));
      const central = new DataView(new ArrayBuffer(46));
      [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [16, crc, 4], [20, data.length, 4], [24, data.length, 4], [28, n.length, 2], [42, offset, 4]]
        .forEach(([at, v, size]) => size === 4 ? central.setUint32(at, v, true) : central.setUint16(at, v, true));
      parts.push(local, n, data);
      dir.push(central, n);
      offset += 30 + n.length + data.length;
    }
    const size = dir.reduce((s, p) => s + p.byteLength, 0);
    const end = new DataView(new ArrayBuffer(22));
    [[0, 0x06054b50, 4], [8, entries.length, 2], [10, entries.length, 2], [12, size, 4], [16, offset, 4]]
      .forEach(([at, v, s]) => s === 4 ? end.setUint32(at, v, true) : end.setUint16(at, v, true));
    return new Blob([...parts, ...dir, end], { type: "application/zip" });
  }

  function download() {
    const many = Object.keys(files).length > 1;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(many ? zip(Object.entries(files)) : new Blob([files["app.pyweb"]], { type: "text/plain" }));
    a.download = many ? "pyweb-app.zip" : "app.pyweb";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    status(many ? "Saved pyweb-app.zip: unzip it and run  pyweb dev app.pyweb" : "Saved app.pyweb: run it with  pyweb dev app.pyweb");
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
    files = { "app.pyweb": source ?? cfg.examples[name].source };
    path = "/";
    openFile("app.pyweb");
    saveDraft();
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
    if (hash.startsWith("code=") || hash.startsWith("files=")) {
      example = "";
      try {
        const text = await unpack(hash.slice(hash.indexOf("=") + 1));
        files = hash.startsWith("files=") ? JSON.parse(text) : { "app.pyweb": text };
        if (typeof files["app.pyweb"] !== "string") throw new Error("no app.pyweb");
      } catch { example = "counter"; files = { "app.pyweb": cfg.examples.counter.source }; }
      exampleSel.value = example;
    } else {
      const named = cfg.examples[hash] ? hash : "";
      const saved = draft && (draft.files || (draft.source ? { "app.pyweb": draft.source } : null));
      if (saved && (!named || draft.example === named)) {
        example = draft.example && cfg.examples[draft.example] ? draft.example : "";
        files = saved;
        restored = true;
      } else {
        example = named || "counter";
        files = { "app.pyweb": cfg.examples[example].source };
      }
      exampleSel.value = example;
    }
    openFile("app.pyweb");
    draftEl.hidden = !edited();   // a draft for another example stays saved until you edit this one
    await boot();
    if (restored && host) status("Restored your last edits (Reset goes back to the example)");
  })();
})();
