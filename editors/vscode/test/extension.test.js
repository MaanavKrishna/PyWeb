// Loads extension.js with stand-ins for the `vscode` API and the language
// client, and checks how it picks and starts the language server and what
// its commands do.
const assert = require("assert");
const Module = require("module");

const tick = () => new Promise((r) => setImmediate(r));

// `programs` says what each command prints (missing: it isn't installed).
function harness({ setting = [], python = null, startFails = false, programs = { "pyweb --version": "pyweb 0.4.1" },
                   choice, picks = [], inputs = [], editor = null, files = [], workspace = "/work", config = {} } = {}) {
  const log = { clients: [], errors: [], infos: [], commands: {}, ran: [], opened: [], terminals: [], written: {},
                shown: [], snippets: [], listeners: {} };
  const settings = { "server.command": setting, "dev.port": 8000, autoCloseTags: true, ...config };
  const uri = (fsPath) => ({ fsPath, toString: () => fsPath });
  const vscode = {
    workspace: {
      getConfiguration: () => ({ get: (k, d) => (k in settings ? settings[k] : d) }),
      createFileSystemWatcher: (glob) => ({ glob }),
      onDidChangeConfiguration: () => ({ dispose() {} }),
      onDidChangeTextDocument: (fn) => { log.listeners.change = fn; return { dispose() {} }; },
      findFiles: async () => files.map(uri),
      asRelativePath: (u) => u.fsPath,
      workspaceFolders: workspace ? [{ uri: uri(workspace) }] : undefined,
      fs: {
        readFile: async (u) => { if (u.fsPath in log.written) return Buffer.from(log.written[u.fsPath]); throw new Error("ENOENT"); },
        writeFile: async (u, data) => { log.written[u.fsPath] = Buffer.from(data).toString("utf8"); },
      },
    },
    extensions: {
      getExtension: (id) => (id === "ms-python.python" && python ? {
        isActive: true,
        exports: { environments: {
          getActiveEnvironmentPath: () => ({ path: python }),
          resolveEnvironment: async () => (python === "python" ? undefined : { executable: { uri: { fsPath: python } } }),
          onDidChangeActiveEnvironmentPath: (fn) => { log.listeners.python = fn; return { dispose() {} }; },
        } },
      } : undefined),
    },
    window: {
      showErrorMessage: async (m) => { log.errors.push(m); return choice; },
      showInformationMessage: async (m) => { log.infos.push(m); return choice; },
      showQuickPick: async (items) => { const want = picks.shift(); return items.find((i) => i.label === want); },
      showInputBox: async (o) => { const v = inputs.shift(); return v === undefined ? o.value : v; },
      showOpenDialog: async () => [uri("/picked")],
      showTextDocument: async (u) => log.shown.push(u.fsPath),
      createTerminal: (o) => {
        const t = { ...o, lines: [], show() {}, sendText(line) { this.lines.push(line); } };
        log.terminals.push(t);
        return t;
      },
      activeTextEditor: editor,
    },
    languages: {
      createLanguageStatusItem: (id, selector) => (log.status = { id, selector, dispose() {} }),
    },
    LanguageStatusSeverity: { Information: 0, Warning: 1, Error: 2 },
    commands: {
      registerCommand: (name, fn) => { log.commands[name] = fn; return { dispose() {} }; },
      executeCommand: (...a) => log.opened.push(a),
    },
    env: { openExternal: (u) => log.opened.push(u) },
    Uri: { parse: (u) => u, file: uri, joinPath: (b, ...p) => uri([b.fsPath, ...p].join("/")) },
    Position: class { constructor(line, character) { Object.assign(this, { line, character }); } },
    SnippetString: class { constructor(value) { this.value = value; } },
  };
  if (editor) {
    editor.insertSnippet = (s, at) => log.snippets.push([s.value, at.line, at.character]);
    editor.document.lineAt = (n) => ({ text: editor.document.lines[n] });
  }
  class LanguageClient {
    constructor(id, name, server, options) {
      Object.assign(this, { id, name, server, options, running: false });
      log.clients.push(this);
    }
    async start() { if (startFails) throw new Error("spawn ENOENT"); this.running = true; }
    async stop() { this.running = false; }
  }
  const load = Module._load;
  Module._load = function (request, ...rest) {
    if (request === "vscode") return vscode;
    if (request === "vscode-languageclient/node") return { LanguageClient };
    if (request === "child_process") {
      return { execFile: (cmd, args, opts, cb) => {
        const key = [cmd, ...args].join(" ");
        log.ran.push({ key, cwd: opts.cwd });
        setImmediate(() => (key in programs ? cb(null, programs[key], "") : cb(Object.assign(new Error("spawn ENOENT"), { code: "ENOENT" }))));
      } };
    }
    return load.call(this, request, ...rest);
  };
  delete require.cache[require.resolve("../extension.js")];
  const ext = require("../extension.js");
  Module._load = load;
  return { ext, log, context: { subscriptions: [] } };
}

function pywebEditor(lines, dirty = false) {
  const document = { languageId: "pyweb", uri: { fsPath: "/work/shop/app.pyweb" }, isDirty: dirty, lines,
                     saved: false, async save() { this.saved = true; } };
  return { document };
}

function typed(editor, line, character, text = ">") {
  return { document: editor.document, contentChanges: [{ text, rangeLength: 0,
    range: { start: { line, character } } }] };
}

(async () => {
  {  // default: `pyweb lsp` from PATH, for .pyweb files only
    const { ext, log, context } = harness();
    await ext.activate(context);
    const c = log.clients[0];
    assert.deepStrictEqual(c.server.run, { command: "pyweb", args: ["lsp"] });
    assert.deepStrictEqual(c.options.documentSelector, [{ scheme: "file", language: "pyweb" }]);
    assert.strictEqual(c.running, true);
    assert.strictEqual(log.status.text, "PyWeb 0.4.1");
    assert.deepStrictEqual(log.status.selector, { language: "pyweb" });
    await log.commands["pyweb.restartServer"]();
    assert.strictEqual(log.clients.length, 2);
    assert.strictEqual(log.clients[0].running, false);
    assert.strictEqual(log.clients[1].running, true);
    await ext.deactivate();
  }
  {  // the Python extension's interpreter wins over PATH, and changing it restarts the server
    const { ext, log, context } = harness({ python: "/venv/bin/python",
      programs: { "pyweb --version": "pyweb 0.4.1", "/venv/bin/python -m pyweb.cli --version": "pyweb 0.4.2" } });
    await ext.activate(context);
    assert.deepStrictEqual(log.clients[0].server.run, { command: "/venv/bin/python", args: ["-m", "pyweb.cli", "lsp"] });
    assert.strictEqual(log.status.text, "PyWeb 0.4.2");
    await log.listeners.python();
    await tick();
    assert.strictEqual(log.clients.length, 2);
  }
  {  // an explicit setting wins over everything
    const { ext, log, context } = harness({ setting: ["/opt/pyweb", "lsp"], python: "/venv/bin/python" });
    await ext.activate(context);
    assert.deepStrictEqual(log.clients[0].server.run, { command: "/opt/pyweb", args: ["lsp"] });
  }
  {  // no interpreter selected (the Python extension says "python") and only python3 exists
    const { ext, log, context } = harness({ python: "python",
      programs: { "python3 -m pyweb.cli --version": "pyweb 0.4.1", "python3 --version": "Python 3.12.1" } });
    await ext.activate(context);
    assert.deepStrictEqual(log.clients[0].server.run, { command: "python3", args: ["-m", "pyweb.cli", "lsp"] });
    assert.strictEqual(log.errors.length, 0);
  }
  {  // too old a PyWeb on PATH is skipped
    const { ext, log, context } = harness({ programs: { "pyweb --version": "pyweb 0.2.0",
      "python --version": "Python 3.11", "python -m pyweb.cli --version": "pyweb 0.4.1" } });
    await ext.activate(context);
    assert.deepStrictEqual(log.clients[0].server.run, { command: "python", args: ["-m", "pyweb.cli", "lsp"] });
  }
  {  // Python found but PyWeb isn't installed in it: one click installs it with that Python
    const { ext, log, context } = harness({ programs: { "python3 --version": "Python 3.12.1" }, choice: "Install PyWeb" });
    await ext.activate(context);
    await tick();
    assert.strictEqual(log.clients.length, 0);
    assert.match(log.errors[0], /PyWeb isn't installed for python3\. Install it \("python3 -m pip install -U pyweb-stack"\)/);
    assert.strictEqual(log.status.text, "PyWeb: not running");
    assert.deepStrictEqual(log.terminals[0].lines, ["python3 -m pip install -U pyweb-stack"]);
  }
  {  // no Python at all
    const { ext, log, context } = harness({ programs: {}, choice: "Open setting" });
    await ext.activate(context);
    await tick();
    assert.match(log.errors[0], /Python with PyWeb wasn't found/);
    assert.deepStrictEqual(log.opened[0], ["workbench.action.openSettings", "pyweb.server.command"]);
  }
  {  // the server found but failing to start: a helpful message, no crash
    const { ext, log, context } = harness({ startFails: true });
    await ext.activate(context);
    assert.match(log.errors[0], /pip install -U pyweb-stack/);
    assert.strictEqual(log.status.text, "PyWeb: not running");
  }
  {  // Run: `pyweb dev` for the open file, saved first, in its folder
    const editor = pywebEditor([], true);
    const { ext, log, context } = harness({ editor });
    await ext.activate(context);
    await log.commands["pyweb.runDev"]();
    assert.strictEqual(editor.document.saved, true);
    assert.strictEqual(log.terminals[0].cwd, "/work/shop");
    assert.deepStrictEqual(log.terminals[0].lines, ["pyweb dev app.pyweb --port 8000"]);
    await log.commands["pyweb.check"]();
    assert.deepStrictEqual(log.terminals[1].lines, ["pyweb check app.pyweb"]);
  }
  {  // Run with no editor: the workspace's app.pyweb, with a custom setting's command
    const { ext, log, context } = harness({ setting: ["/my venv/bin/python", "-m", "pyweb.cli", "lsp"],
      files: ["/work/blog/app.pyweb"], config: { "dev.port": 9000 } });
    await ext.activate(context);
    await log.commands["pyweb.runDev"]();
    assert.strictEqual(log.terminals[0].cwd, "/work/blog");
    const quoted = process.platform === "win32" ? "& \"/my venv/bin/python\"" : "\"/my venv/bin/python\"";
    assert.deepStrictEqual(log.terminals[0].lines, [`${quoted} -m pyweb.cli dev app.pyweb --port 9000`]);
  }
  {  // New app: template, name, then `pyweb new` in the workspace folder
    const { ext, log, context } = harness({ picks: ["ai-chat"], inputs: ["helper"], choice: "Run it",
      programs: { "pyweb --version": "pyweb 0.4.1", "pyweb new helper --template ai-chat": "created helper/" } });
    await ext.activate(context);
    await log.commands["pyweb.newApp"]();
    assert.ok(log.ran.some((r) => r.key === "pyweb new helper --template ai-chat" && r.cwd === "/work"));
    assert.deepStrictEqual(log.shown, ["/work/helper/app.pyweb"]);
    assert.match(log.infos[0], /Created helper from the ai-chat template/);
    assert.deepStrictEqual(log.terminals[0].lines, ["pyweb dev app.pyweb --port 8000"]);
    assert.strictEqual(log.terminals[0].cwd, "/work/helper");
  }
  {  // Add an npm package next to the app
    const { ext, log, context } = harness({ editor: pywebEditor([]), inputs: ["chart.js/auto  canvas-confetti"] });
    await ext.activate(context);
    await log.commands["pyweb.addPackage"]();
    assert.deepStrictEqual(log.terminals[0].lines, ["pyweb add chart.js/auto canvas-confetti"]);
  }
  {  // MCP setup keeps other servers in .vscode/mcp.json
    const { ext, log, context } = harness();
    log.written["/work/.vscode/mcp.json"] = JSON.stringify({ servers: { other: { command: "x" } } });
    await ext.activate(context);
    await log.commands["pyweb.setUpMcp"]();
    const config = JSON.parse(log.written["/work/.vscode/mcp.json"]);
    assert.deepStrictEqual(config.servers.pyweb, { type: "stdio", command: "pyweb", args: ["mcp"] });
    assert.deepStrictEqual(config.servers.other, { command: "x" });
  }
  {  // closing tags as you type
    const { tagToClose } = harness().ext;
    assert.strictEqual(tagToClose("    <main>", ""), "main");
    assert.strictEqual(tagToClose("<button onclick={add} class=\"a > b\">", ""), "button");
    assert.strictEqual(tagToClose("<Card title={x[\"t\"]}>", ""), "Card");
    assert.strictEqual(tagToClose("<p>", "</p>"), null);           // already closed
    assert.strictEqual(tagToClose("<input bind={x} />", ""), null); // self-closing
    assert.strictEqual(tagToClose("<br>", ""), null);              // void element
    assert.strictEqual(tagToClose("if a <b and c >", ""), null);   // Python, not markup
    assert.strictEqual(tagToClose("<a href={f(x >", ""), null);     // `>` inside an expression
    assert.strictEqual(tagToClose("<a href={f(x > 1)}>", ""), "a");
    const editor = pywebEditor(["", "    <section>"]);
    const { ext, log, context } = harness({ editor });
    await ext.activate(context);
    log.listeners.change(typed(editor, 1, 12));
    assert.deepStrictEqual(log.snippets, [["$0</section>", 1, 13]]);
    log.listeners.change(typed(editor, 1, 12, ">>"));
    assert.strictEqual(log.snippets.length, 1);
  }
  {  // tag closing can be turned off
    const editor = pywebEditor(["<main>"]);
    const { ext, log, context } = harness({ editor, config: { autoCloseTags: false } });
    await ext.activate(context);
    log.listeners.change(typed(editor, 0, 5));
    assert.strictEqual(log.snippets.length, 0);
  }
  console.log("extension ok");
})().catch((e) => { console.error(e); process.exit(1); });
