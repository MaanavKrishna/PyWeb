// PyWeb for VS Code: starts `pyweb lsp` (the language server that ships
// with PyWeb) for .pyweb files. Highlighting and snippets work without it.
const vscode = require("vscode");
const { LanguageClient } = require("vscode-languageclient/node");

let client = null;

async function pythonFromPythonExtension() {
  const ext = vscode.extensions.getExtension("ms-python.python");
  if (!ext) return null;
  try {
    if (!ext.isActive) await ext.activate();
    const envs = ext.exports && ext.exports.environments;
    if (!envs) return null;
    const active = envs.getActiveEnvironmentPath();
    const resolved = active ? await envs.resolveEnvironment(active) : null;
    return (resolved && resolved.executable && resolved.executable.uri && resolved.executable.uri.fsPath)
      || (active && active.path) || null;
  } catch {
    return null;
  }
}

async function serverCommand() {
  const custom = vscode.workspace.getConfiguration("pyweb").get("server.command");
  if (Array.isArray(custom) && custom.length) return { command: custom[0], args: custom.slice(1) };
  const python = await pythonFromPythonExtension();
  if (python) return { command: python, args: ["-m", "pyweb.cli", "lsp"] };
  return { command: "pyweb", args: ["lsp"] };
}

async function start() {
  const run = await serverCommand();
  client = new LanguageClient(
    "pyweb",
    "PyWeb",
    { run, debug: run },
    {
      documentSelector: [{ scheme: "file", language: "pyweb" }],
      synchronize: { fileEvents: vscode.workspace.createFileSystemWatcher("**/*.pyweb") },
    },
  );
  try {
    await client.start();
  } catch (err) {
    client = null;
    const cmd = [run.command, ...run.args].join(" ");
    vscode.window.showErrorMessage(
      `PyWeb: couldn't start the language server (${cmd}). Install PyWeb 0.3 or later ` +
      `("pip install -U pyweb-stack") in the Python environment VS Code uses, or set "pyweb.server.command".`,
    );
  }
}

async function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("pyweb.restartServer", async () => {
      if (client) await client.stop();
      await start();
    }),
    vscode.workspace.onDidChangeConfiguration(async (e) => {
      if (e.affectsConfiguration("pyweb.server.command")) {
        if (client) await client.stop();
        await start();
      }
    }),
  );
  await start();
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };
