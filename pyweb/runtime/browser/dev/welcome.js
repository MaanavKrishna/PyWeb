// pyweb dev only: the first time a project runs, a short welcome with what to try next.
(() => {
  if (!window.__PYWEB_WELCOME__ || window.__pywebWelcome) return;
  window.__pywebWelcome = true;
  const file = String(window.__PYWEB_WELCOME__.file || "app.pyweb");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const CLAUDE = "claude mcp add pyweb -- pyweb mcp";
  const JSON_CFG = '{"mcpServers": {"pyweb": {"command": "pyweb", "args": ["mcp"]}}}';
  const host = document.createElement("pyweb-welcome");
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
:host{all:initial}*{box-sizing:border-box;font:13px/1.5 system-ui,sans-serif}
.card{position:fixed;right:16px;top:16px;z-index:2147483645;width:min(400px,calc(100vw - 32px));max-height:calc(100vh - 32px);overflow:auto;
 background:#0b1020;color:#e5e7eb;border:1px solid #374151;border-radius:14px;box-shadow:0 16px 44px rgba(0,0,0,.45);padding:16px 18px}
h2{margin:0 0 6px;font-size:16px;font-weight:650;color:#fff}ol{margin:8px 0;padding-left:20px}li{margin:6px 0}
kbd,code{font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#1f2937;border-radius:5px;padding:1px 5px;color:#e5e7eb}
.muted{color:#9ca3af}.row{display:flex;gap:6px;align-items:center;margin:4px 0}.row code{flex:1;overflow:auto;white-space:nowrap}
button{background:#1f2937;color:#e5e7eb;border:1px solid #374151;border-radius:7px;padding:3px 9px;cursor:pointer}
.go{background:#4f46e5;border-color:#6366f1;color:#fff;padding:6px 14px;margin-top:8px}
</style><div class="card" role="dialog" aria-label="Welcome to PyWeb">
<h2>Welcome to PyWeb</h2><p class="muted">Your app is running. A few things to try:</p>
<ol>
<li>Change some text in <code>${esc(file)}</code> and save. The page updates in place and keeps its state.</li>
<li>Hold <kbd>Alt</kbd> and click anything to open its line in your editor.
 <kbd>Alt</kbd>+<kbd>Shift</kbd>-click to change it with AI.</li>
<li>The ⚡ badge (bottom left) shows each request's SQL, jobs and emails, and a timeline you can step back through.</li>
<li>Connect your AI assistant. Claude Code:
 <div class="row"><code>${esc(CLAUDE)}</code><button data-copy="${esc(CLAUDE)}">Copy</button></div>
 Cursor, VS Code, Windsurf, Claude Desktop:
 <div class="row"><code>${esc(JSON_CFG)}</code><button data-copy="${esc(JSON_CFG)}">Copy</button></div></li>
<li>Run the tests with <code>pyweb test</code>, and ship it with <code>pyweb deploy</code>.</li>
</ol><button class="go" data-done>Got it</button></div>`;
  root.addEventListener("click", async (ev) => {
    const t = ev.target.closest("button");
    if (!t) return;
    if (t.dataset.copy) {
      try { await navigator.clipboard.writeText(t.dataset.copy); t.textContent = "Copied"; } catch { t.textContent = "Select it"; }
      return;
    }
    if (t.hasAttribute("data-done")) {
      host.remove();
      try { await fetch("/__pyweb/dev/welcome", { method: "POST" }); } catch { /* shown again next time */ }
    }
  });
  (document.body || document.documentElement).appendChild(host);
})();
