// Loads extension.js with stand-ins for the `vscode` API and the language
// client, and checks how it picks and starts the language server.
const assert = require("assert");
const Module = require("module");

function harness({ setting = [], python = null, startFails = false } = {}) {
  const log = { clients: [], errors: [], commands: {} };
  const vscode = {
    workspace: {
      getConfiguration: () => ({ get: () => setting }),
      createFileSystemWatcher: (glob) => ({ glob }),
      onDidChangeConfiguration: () => ({ dispose() {} }),
    },
    extensions: {
      getExtension: (id) => (id === "ms-python.python" && python ? {
        isActive: true,
        exports: { environments: {
          getActiveEnvironmentPath: () => ({ path: python }),
          resolveEnvironment: async () => ({ executable: { uri: { fsPath: python } } }),
        } },
      } : undefined),
    },
    window: { showErrorMessage: (m) => log.errors.push(m) },
    commands: { registerCommand: (name, fn) => { log.commands[name] = fn; return { dispose() {} }; } },
  };
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
    return load.call(this, request, ...rest);
  };
  delete require.cache[require.resolve("../extension.js")];
  const ext = require("../extension.js");
  Module._load = load;
  return { ext, log, context: { subscriptions: [] } };
}

(async () => {
  {  // default: `pyweb lsp` from PATH, for .pyweb files only
    const { ext, log, context } = harness();
    await ext.activate(context);
    const c = log.clients[0];
    assert.deepStrictEqual(c.server.run, { command: "pyweb", args: ["lsp"] });
    assert.deepStrictEqual(c.options.documentSelector, [{ scheme: "file", language: "pyweb" }]);
    assert.strictEqual(c.running, true);
    await log.commands["pyweb.restartServer"]();
    assert.strictEqual(log.clients.length, 2);
    assert.strictEqual(log.clients[0].running, false);
    assert.strictEqual(log.clients[1].running, true);
    await ext.deactivate();
  }
  {  // the Python extension's interpreter wins over PATH
    const { ext, log, context } = harness({ python: "/venv/bin/python" });
    await ext.activate(context);
    assert.deepStrictEqual(log.clients[0].server.run, { command: "/venv/bin/python", args: ["-m", "pyweb.cli", "lsp"] });
  }
  {  // an explicit setting wins over everything
    const { ext, log, context } = harness({ setting: ["/opt/pyweb", "lsp"], python: "/venv/bin/python" });
    await ext.activate(context);
    assert.deepStrictEqual(log.clients[0].server.run, { command: "/opt/pyweb", args: ["lsp"] });
  }
  {  // PyWeb not installed: a helpful message, no crash
    const { ext, log, context } = harness({ startFails: true });
    await ext.activate(context);
    assert.match(log.errors[0], /pip install -U pyweb-stack/);
  }
  console.log("extension ok");
})().catch((e) => { console.error(e); process.exit(1); });
