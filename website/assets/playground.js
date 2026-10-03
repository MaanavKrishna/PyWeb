// PyWeb playground. The real PyWeb (compiler, server rendering and your
// @server functions) runs in this tab on Pyodide (CPython compiled to
// WebAssembly). The preview iframe talks to it through a fetch bridge, so
// apps behave as they would under `pyweb dev`. Nothing leaves the browser.
(() => {
  const cfg = JSON.parse(document.getElementById("pg-config").textContent);
  const $ = (id) => document.getElementById(id);
  const editor = $("pg-source");
  const gutter = $("pg-gutter");
  const statusEl = $("pg-status");
  const errorEl = $("pg-error");
  const frame = $("pg-frame");
  const urlBox = $("pg-url");
  const pageSel = $("pg-page");
  const exampleSel = $("pg-example");
  const jsOut = $("pg-js");
  const placeOut = $("pg-place");

  let host = null;          // Python functions: load, request
  let runtimeUrl = null;    // blob: URL of the PyWeb browser runtime
  let pages = [];
  let path = "/";
  let timer = null;
  let errorLine = null;
  const blobs = [];

  const status = (text, busy = false) => {
    statusEl.textContent = text;
    statusEl.classList.toggle("busy", busy);
  };

  // ----------------------------------------------------------- editor
  function renderGutter() {
    const n = editor.value.split("\n").length;
    let out = "";
    for (let i = 1; i <= n; i++) out += (i === errorLine ? `<b>${i}</b>` : i) + "\n";
    gutter.innerHTML = out;
    gutter.scrollTop = editor.scrollTop;
  }
  editor.addEventListener("scroll", () => { gutter.scrollTop = editor.scrollTop; });
  editor.addEventListener("input", () => {
    renderGutter();
    clearTimeout(timer);
    timer = setTimeout(run, 700);
  });
  editor.addEventListener("keydown", (e) => {
    const { selectionStart: s, selectionEnd: end, value } = editor;
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); run(); return; }
    if (e.key === "Tab" && !e.shiftKey) {
      e.preventDefault();
      editor.setRangeText("    ", s, end, "end");
      editor.dispatchEvent(new Event("input"));
    } else if (e.key === "Enter") {
      const lineStart = value.lastIndexOf("\n", s - 1) + 1;
      const line = value.slice(lineStart, s);
      let indent = line.match(/^\s*/)[0];
      if (/:\s*$/.test(line)) indent += "    ";
      e.preventDefault();
      editor.setRangeText("\n" + indent, s, end, "end");
      editor.dispatchEvent(new Event("input"));
    }
  });

  function showError(err) {
    errorLine = err && err.line ? err.line : null;
    if (!err) {
      errorEl.hidden = true;
    } else {
      errorEl.hidden = false;
      errorEl.innerHTML = "";
      const head = document.createElement("strong");
      head.textContent = err.line ? `Line ${err.line}` : "Error";
      const msg = document.createElement("span");
      msg.textContent = err.message;
      errorEl.append(head, msg);
      if (err.hint) {
        const hint = document.createElement("em");
        hint.textContent = err.hint;
        errorEl.append(hint);
      }
    }
    renderGutter();
  }

  // ------------------------------------------------------------ python
  function call(fn, ...args) { return JSON.parse(host[fn](...args)); }

  function request(method, url, body = "", type = "") {
    return call("request", method, url, body || "", type || "");
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
  // that sends the app's own requests (/__pyweb/...) to Python.
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
      const r = request(init.method || "GET", url, typeof init.body === "string" ? init.body : "",
        headers.get("content-type") || "");
      const empty = r.status === 204 || r.status === 304;
      return new Response(empty ? null : r.body, { status: r.status, headers: r.headers });
    },
    navigate: (href) => setTimeout(() => navigate(href)), // after the click/handler finishes
  };

  // ------------------------------------------------------------ panels
  function showCompiled(page) {
    jsOut.textContent = page && page.js ? page.js
      : "// This page is static: it ships no JavaScript, only server-rendered HTML.";
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

  for (const tab of document.querySelectorAll(".pg-tabs [role=tab]")) {
    tab.addEventListener("click", () => {
      for (const t of document.querySelectorAll(".pg-tabs [role=tab]")) t.setAttribute("aria-selected", String(t === tab));
      frame.hidden = tab.dataset.tab !== "preview";
      jsOut.hidden = tab.dataset.tab !== "js";
      placeOut.hidden = tab.dataset.tab !== "place";
    });
  }
  pageSel.addEventListener("change", () => navigate(sample(pageSel.value)));
  urlBox.addEventListener("keydown", (e) => { if (e.key === "Enter") navigate(urlBox.value.trim() || "/"); });
  $("pg-run").addEventListener("click", run);

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

  function loadExample(name) {
    editor.value = cfg.examples[name].source;
    path = "/";
    renderGutter();
    run();
  }
  for (const [name, ex] of Object.entries(cfg.examples)) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = ex.title;
    exampleSel.append(opt);
  }
  exampleSel.addEventListener("change", () => {
    history.replaceState(null, "", location.pathname + "#" + exampleSel.value);
    loadExample(exampleSel.value);
  });

  (async () => {
    const hash = location.hash.slice(1);
    if (hash.startsWith("code=")) {
      try { editor.value = await unpack(hash.slice(5)); } catch { editor.value = cfg.examples.counter.source; }
      exampleSel.value = "";
    } else {
      const name = cfg.examples[hash] ? hash : "counter";
      exampleSel.value = name;
      editor.value = cfg.examples[name].source;
    }
    renderGutter();
    boot();
  })();
})();
