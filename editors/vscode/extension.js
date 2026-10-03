// PyWeb for VS Code: starts `pyweb lsp` (the language server that ships
// with PyWeb) for .pyweb files, and adds commands to create, run and set up
// PyWeb apps. Highlighting, snippets and tag closing work without PyWeb.
const path = require("path");
const vscode = require("vscode");
const { execFile } = require("child_process");
const { LanguageClient } = require("vscode-languageclient/node");

const TEMPLATES = [
  ["counter", "A page with state and a button"],
  ["todo", "A to-do list saved in SQLite"],
  ["blog", "Pages, layouts and Markdown posts"],
  ["auth", "Sign up, log in and a private page"],
  ["chat", "Live chat between browsers"],
  ["ai-chat", "A streaming AI chat"],
  ["blank", "Just the basics"],
];
// Tags that never have a closing tag.
const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);

let client = null;
let status = null;
let found = null;       // the last serverCommand() result
let pythonEvents = null;

async function pythonExtensionApi() {
  const ext = vscode.extensions.getExtension("ms-python.python");
  if (!ext) return null;
  try {
    if (!ext.isActive) await ext.activate();
    return (ext.exports && ext.exports.environments) || null;
  } catch {
    return null;
  }
}

async function pythonFromPythonExtension() {
  const envs = await pythonExtensionApi();
  if (!envs) return null;
  try {
    const active = envs.getActiveEnvironmentPath();
    const resolved = active ? await envs.resolveEnvironment(active) : null;
    // Only a resolved executable counts: with no interpreter selected the Python
    // extension reports the bare name "python", which may not exist at all.
    return (resolved && resolved.executable && resolved.executable.uri && resolved.executable.uri.fsPath) || null;
  } catch {
    return null;
  }
}

/** Runs `command args...`; resolves to its output, or null if it can't run or fails. */
function probe(command, args) {
  return new Promise((resolve) => {
    try {
      execFile(command, args, { timeout: 20000, windowsHide: true }, (err, stdout, stderr) => {
        resolve(err ? null : `${stdout}${stderr}`);
      });
    } catch {
      resolve(null);
    }
  });
}

function parseVersion(output) {
  const m = /(\d+)\.(\d+)(?:\.(\d+))?/.exec(output || "");
  return m ? m[0] : null;
}

function versionOk(output) {
  const m = /(\d+)\.(\d+)/.exec(output || "");
  return !!m && (Number(m[1]) > 0 || Number(m[2]) >= 3);
}

/** The ways to run PyWeb, most specific first. */
async function candidates() {
  const out = [];
  const python = await pythonFromPythonExtension();
  if (python) out.push({ command: python, args: ["-m", "pyweb.cli"], python: true });
  out.push({ command: "pyweb", args: [] });
  if (process.platform === "win32") out.push({ command: "py", args: ["-3", "-m", "pyweb.cli"], python: true });
  out.push({ command: "python3", args: ["-m", "pyweb.cli"], python: true });
  out.push({ command: "python", args: ["-m", "pyweb.cli"], python: true });
  return out;
}

/**
 * `{ run, cli, version }` with a working command (`cli` runs other PyWeb commands),
 * or `{ problem, python }` explaining why there is none (`python`: one without PyWeb).
 */
async function serverCommand() {
  const custom = vscode.workspace.getConfiguration("pyweb").get("server.command");
  if (Array.isArray(custom) && custom.length) {
    const run = { command: custom[0], args: custom.slice(1) };
    // `[..., "lsp"]`: the rest of the command runs the other PyWeb commands too.
    const cli = run.args[run.args.length - 1] === "lsp" ? { command: run.command, args: run.args.slice(0, -1) } : null;
    return { run, cli, version: null };
  }
  let pythonWithoutPyweb = null;
  for (const c of await candidates()) {
    const output = await probe(c.command, [...c.args, "--version"]);
    if (versionOk(output)) {
      return { run: { command: c.command, args: [...c.args, "lsp"] }, cli: { command: c.command, args: c.args },
               version: parseVersion(output) };
    }
    if (c.python && !pythonWithoutPyweb && (await probe(c.command, ["--version"])) !== null) {
      pythonWithoutPyweb = [c.command, ...c.args.slice(0, c.args.indexOf("-m"))];
    }
  }
  if (pythonWithoutPyweb) {
    const py = pythonWithoutPyweb.join(" ");
    return { python: pythonWithoutPyweb, problem: `PyWeb isn't installed for ${py}. Install it ` +
      `("${py} -m pip install -U pyweb-stack") or pick the interpreter that has it with "Python: Select Interpreter".` };
  }
  return { problem: "Python with PyWeb wasn't found. Install Python 3.10+ and run \"pip install -U pyweb-stack\", " +
    "or set \"pyweb.server.command\" to the command that runs it." };
}

function quote(word) {
  return /^[\w@%+=:,./-]+$/.test(word) ? word : JSON.stringify(word);
}

function shellLine(command, args) {
  // PowerShell needs `&` to run a quoted program path.
  const prefix = process.platform === "win32" && /\s/.test(command) ? "& " : "";
  return prefix + [command, ...args].map(quote).join(" ");
}

function setStatus(state, detail) {
  if (!status) return;
  status.busy = state === "starting";
  status.severity = state === "error" ? vscode.LanguageStatusSeverity.Error : vscode.LanguageStatusSeverity.Information;
  status.text = state === "error" ? "PyWeb: not running" : state === "starting" ? "PyWeb: starting" : `PyWeb ${detail || ""}`.trim();
  status.detail = state === "error" ? detail : state === "running" ? "Language server running" : "";
  status.command = state === "error"
    ? { title: "Fix", command: "pyweb.setUp" }
    : { title: "Restart", command: "pyweb.restartServer" };
}

async function showProblem(problem) {
  const actions = found && found.python ? ["Install PyWeb", "Open setting"] : ["Open setting", "Install guide"];
  const choice = await vscode.window.showErrorMessage(`PyWeb: ${problem}`, ...actions);
  if (choice === "Install PyWeb") return installPyweb();
  if (choice === "Open setting") vscode.commands.executeCommand("workbench.action.openSettings", "pyweb.server.command");
  if (choice === "Install guide") {
    vscode.env.openExternal(vscode.Uri.parse("https://maanavkrishna.github.io/PyWeb/quickstart.html"));
  }
}

async function start() {
  setStatus("starting");
  found = await serverCommand();
  if (!found.run) {
    setStatus("error", found.problem);
    showProblem(found.problem);  // not awaited: activation shouldn't wait for a click
    return;
  }
  const run = found.run;
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
    setStatus("running", found.version);
  } catch (err) {
    client = null;
    const cmd = [run.command, ...run.args].join(" ");
    const problem = `couldn't start the language server (${cmd}). Install PyWeb 0.3 or later ` +
      `("pip install -U pyweb-stack") in the Python environment VS Code uses, or set "pyweb.server.command".`;
    setStatus("error", problem);
    vscode.window.showErrorMessage(`PyWeb: ${problem}`);
  }
}

async function restart() {
  if (client) {
    const old = client;
    client = null;
    await old.stop();
  }
  await start();
}

/** The PyWeb command line (`{command, args}`), or null after telling the user why there is none. */
async function cli() {
  if (!found || !found.cli) found = await serverCommand();
  if (found.cli) return found.cli;
  await showProblem(found.problem || "set \"pyweb.server.command\" to a command ending in \"lsp\" to run PyWeb commands.");
  return null;
}

function runInTerminal(name, cwd, command, args) {
  const terminal = vscode.window.createTerminal({ name, cwd });
  terminal.show();
  terminal.sendText(shellLine(command, args));
  return terminal;
}

/** The app file to act on: the open .pyweb file, else app.pyweb in the workspace. */
async function appFile(uri) {
  if (uri && uri.fsPath) return uri.fsPath;
  const editor = vscode.window.activeTextEditor;
  if (editor && editor.document.languageId === "pyweb") {
    if (editor.document.isDirty) await editor.document.save();
    return editor.document.uri.fsPath;
  }
  const files = await vscode.workspace.findFiles("**/app.pyweb", "**/{node_modules,dist,.venv,venv}/**", 20);
  if (files.length === 1) return files[0].fsPath;
  if (files.length > 1) {
    const pick = await vscode.window.showQuickPick(
      files.map((f) => ({ label: vscode.workspace.asRelativePath(f), uri: f })), { placeHolder: "Which app?" });
    return pick ? pick.uri.fsPath : null;
  }
  vscode.window.showErrorMessage("PyWeb: open a .pyweb file first.");
  return null;
}

async function runDev(uri) {
  const file = await appFile(uri);
  const pyweb = file && await cli();
  if (!pyweb) return;
  const port = String(vscode.workspace.getConfiguration("pyweb").get("dev.port") || 8000);
  runInTerminal("PyWeb dev", path.dirname(file), pyweb.command, [...pyweb.args, "dev", path.basename(file), "--port", port]);
  if (vscode.workspace.getConfiguration("pyweb").get("dev.openBrowser")) {
    setTimeout(() => vscode.env.openExternal(vscode.Uri.parse(`http://127.0.0.1:${port}/`)), 2500);
  }
}

async function check(uri) {
  const file = await appFile(uri);
  const pyweb = file && await cli();
  if (pyweb) runInTerminal("PyWeb check", path.dirname(file), pyweb.command, [...pyweb.args, "check", path.basename(file)]);
}

async function newApp() {
  const pyweb = await cli();
  if (!pyweb) return;
  const template = await vscode.window.showQuickPick(
    TEMPLATES.map(([label, description]) => ({ label, description })), { placeHolder: "Pick a starter app" });
  if (!template) return;
  const name = await vscode.window.showInputBox({
    prompt: "Folder name for the new app", value: template.label === "blank" ? "my-app" : `my-${template.label}`,
    validateInput: (v) => (/^[\w.-]+$/.test(v) ? null : "Use letters, digits, '-', '_' or '.'"),
  });
  if (!name) return;
  const folders = vscode.workspace.workspaceFolders;
  let parent = folders && folders.length ? folders[0].uri : null;
  if (!parent) {
    const picked = await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false, openLabel: "Create here" });
    if (!picked || !picked.length) return;
    parent = picked[0];
  }
  const output = await new Promise((resolve) => {
    execFile(pyweb.command, [...pyweb.args, "new", name, "--template", template.label],
      { cwd: parent.fsPath, timeout: 60000, windowsHide: true },
      (err, stdout, stderr) => resolve(err ? { error: `${stderr || err.message}`.trim() } : { ok: stdout }));
  });
  if (output.error) {
    vscode.window.showErrorMessage(`PyWeb: couldn't create the app: ${output.error}`);
    return;
  }
  const app = vscode.Uri.file(path.join(parent.fsPath, name, "app.pyweb"));
  await vscode.window.showTextDocument(app);
  const choice = await vscode.window.showInformationMessage(`Created ${name} from the ${template.label} template.`, "Run it", "Open folder");
  if (choice === "Run it") await runDev(app);
  if (choice === "Open folder") vscode.commands.executeCommand("vscode.openFolder", vscode.Uri.file(path.join(parent.fsPath, name)));
}

async function addPackage() {
  const file = await appFile();
  const pyweb = file && await cli();
  if (!pyweb) return;
  const spec = await vscode.window.showInputBox({
    prompt: "npm package for browser code (no Node.js needed)", placeHolder: "chart.js/auto, canvas-confetti, date-fns@^4",
  });
  if (!spec) return;
  runInTerminal("PyWeb add", path.dirname(file), pyweb.command, [...pyweb.args, "add", ...spec.split(/\s+/).filter(Boolean)]);
}

async function installPyweb() {
  if (!found || found.cli || !found.python) found = await serverCommand();
  if (found.run) {
    vscode.window.showInformationMessage(`PyWeb ${found.version || ""} is already installed.`.replace("  ", " "));
    return;
  }
  const python = found.python || [process.platform === "win32" ? "py" : "python3"];
  const terminal = runInTerminal("PyWeb install", undefined, python[0], [...python.slice(1), "-m", "pip", "install", "-U", "pyweb-stack"]);
  const choice = await vscode.window.showInformationMessage(
    "Installing PyWeb in the terminal. Restart the language server when it finishes.", "Restart now");
  if (choice === "Restart now") await restart();
  return terminal;
}

async function setUpMcp() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || !folders.length) {
    vscode.window.showErrorMessage("PyWeb: open your app's folder first.");
    return;
  }
  const pyweb = await cli();
  if (!pyweb) return;
  const file = vscode.Uri.joinPath(folders[0].uri, ".vscode", "mcp.json");
  let config = {};
  try {
    config = JSON.parse(Buffer.from(await vscode.workspace.fs.readFile(file)).toString("utf8"));
  } catch {
    config = {};
  }
  config.servers = Object.assign({}, config.servers, {
    pyweb: { type: "stdio", command: pyweb.command, args: [...pyweb.args, "mcp"] },
  });
  await vscode.workspace.fs.writeFile(file, Buffer.from(JSON.stringify(config, null, 2) + "\n", "utf8"));
  await vscode.window.showTextDocument(file);
  vscode.window.showInformationMessage("Added the PyWeb MCP server to .vscode/mcp.json. Start it from that file, " +
    "then ask Copilot's agent mode to build or change your app.");
}

async function setUp() {
  if (found && found.problem) return showProblem(found.problem);
  return restart();
}

/** Typing `>` after `<tag ...` at the start of a markup line adds `</tag>`. */
function closeTag(event) {
  if (!vscode.workspace.getConfiguration("pyweb").get("autoCloseTags", true)) return;
  const doc = event.document;
  if (doc.languageId !== "pyweb" || event.contentChanges.length !== 1) return;
  const change = event.contentChanges[0];
  if (change.text !== ">" || change.rangeLength !== 0) return;
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document !== doc) return;
  const line = doc.lineAt(change.range.start.line).text;
  const end = change.range.start.character + 1;
  const tag = tagToClose(line.slice(0, end), line.slice(end));
  if (!tag) return;
  const at = new vscode.Position(change.range.start.line, end);
  editor.insertSnippet(new vscode.SnippetString(`$0</${tag}>`), at, { undoStopBefore: false, undoStopAfter: false });
}

/** The tag name to close, given the line up to and including the `>` just typed and the text after it. */
function tagToClose(before, after) {
  // Only markup: the line starts with a tag, so `if a <b and c > d` is left alone.
  if (!/^\s*</.test(before)) return null;
  const m = /<([A-Za-z][\w.-]*)(?:\s(?:[^<>{}"']|"[^"]*"|'[^']*'|\{[^{}]*\})*)?>$/.exec(before);
  if (!m || /\/>$/.test(before)) return null;
  const tag = m[1];
  if (VOID.has(tag.toLowerCase())) return null;
  if (after.trimStart().startsWith(`</${tag}>`)) return null;
  return tag;
}

async function watchPythonInterpreter() {
  const envs = await pythonExtensionApi();
  if (envs && envs.onDidChangeActiveEnvironmentPath) {
    pythonEvents = envs.onDidChangeActiveEnvironmentPath(() => {
      const custom = vscode.workspace.getConfiguration("pyweb").get("server.command");
      return Array.isArray(custom) && custom.length ? undefined : restart();
    });
  }
}

async function activate(context) {
  if (vscode.languages.createLanguageStatusItem) {
    status = vscode.languages.createLanguageStatusItem("pyweb.server", { language: "pyweb" });
    status.name = "PyWeb";
    context.subscriptions.push(status);
  }
  context.subscriptions.push(
    vscode.commands.registerCommand("pyweb.restartServer", restart),
    vscode.commands.registerCommand("pyweb.setUp", setUp),
    vscode.commands.registerCommand("pyweb.runDev", runDev),
    vscode.commands.registerCommand("pyweb.check", check),
    vscode.commands.registerCommand("pyweb.newApp", newApp),
    vscode.commands.registerCommand("pyweb.addPackage", addPackage),
    vscode.commands.registerCommand("pyweb.installPyweb", installPyweb),
    vscode.commands.registerCommand("pyweb.setUpMcp", setUpMcp),
    vscode.workspace.onDidChangeConfiguration(async (e) => {
      if (e.affectsConfiguration("pyweb.server.command")) await restart();
    }),
    vscode.workspace.onDidChangeTextDocument(closeTag),
    { dispose: () => pythonEvents && pythonEvents.dispose() },
  );
  await watchPythonInterpreter();
  await start();
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate, tagToClose, shellLine };
