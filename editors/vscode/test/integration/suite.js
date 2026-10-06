// Runs inside VS Code (see run.js): the extension starts `pyweb lsp`, and errors,
// hover, completion, outline and commands work end to end.
const assert = require("assert");
const path = require("path");
const vscode = require("vscode");

async function until(what, fn, ms = 60000) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) {
    try {
      last = await fn();
      if (last) return last;
    } catch (err) {
      last = err;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`timed out waiting for ${what} (last: ${last && (last.stack || JSON.stringify(last))})`);
}

function at(doc, needle, offset = 0) {
  const i = doc.getText().indexOf(needle);
  assert.ok(i >= 0, `${needle} not in the file`);
  return doc.positionAt(i + offset);
}

const steps = {
  async "the extension activates and its commands are registered"() {
    const ext = vscode.extensions.getExtension("maanavkrishna.pyweb");
    assert.ok(ext, "extension not found");
    await ext.activate();
    const commands = await vscode.commands.getCommands(true);
    for (const c of ["pyweb.restartServer", "pyweb.makeMigration", "pyweb.upgradeDatabase", "pyweb.runDev"]) {
      assert.ok(commands.includes(c), `${c} is not registered`);
    }
  },

  async "hover says where code runs"(doc) {
    const hover = await until("hover", async () => {
      const found = await vscode.commands.executeCommand("vscode.executeHoverProvider", doc.uri, at(doc, "count += 1", 1));
      return found && found.length && found;
    });
    const text = hover.flatMap((h) => h.contents.map((c) => c.value || String(c))).join("\n");
    assert.match(text, /browser/, text);
  },

  async "completion offers a Model's fields"(doc, editor) {
    const line = at(doc, "    return Post.create").line;
    await editor.edit((e) => e.insert(new vscode.Position(line, 0), "    Post.where(ti)\n"));
    const pos = new vscode.Position(line, "    Post.where(ti".length);
    const list = await until("completion", async () => {
      const found = await vscode.commands.executeCommand("vscode.executeCompletionItemProvider", doc.uri, pos);
      return found && found.items.some((i) => (i.label.label || i.label) === "title=") && found;
    });
    assert.ok(list.items.length > 0);
    await vscode.commands.executeCommand("undo");
  },

  async "the outline lists the page and the server function"(doc) {
    const symbols = await until("symbols", () =>
      vscode.commands.executeCommand("vscode.executeDocumentSymbolProvider", doc.uri));
    const names = symbols.map((s) => s.name);
    assert.ok(names.includes("Home") && names.includes("publish"), names.join(", "));
  },

  async "errors appear as you type and clear when fixed"(doc, editor) {
    const close = at(doc, "</button>");
    await editor.edit((e) => e.replace(new vscode.Range(close, close.translate(0, "</button>".length)), "</div>"));
    const diags = await until("a diagnostic", () => {
      const d = vscode.languages.getDiagnostics(doc.uri).filter((x) => /mismatched/.test(x.message));
      return d.length && d;
    });
    assert.strictEqual(diags[0].range.start.line, close.line);
    await vscode.commands.executeCommand("undo");
    await until("the diagnostic to clear", () =>
      !vscode.languages.getDiagnostics(doc.uri).some((x) => /mismatched/.test(x.message)));
  },

  async "a Model without a migration gets a code lens"(doc) {
    const lenses = await until("a code lens", async () => {
      const found = await vscode.commands.executeCommand("vscode.executeCodeLensProvider", doc.uri);
      return found && found.some((l) => l.command && /migration/.test(l.command.title)) && found;
    });
    assert.ok(lenses.some((l) => l.command.command === "pyweb.makeMigration"));
  },
};

exports.run = async function run() {
  const folder = vscode.workspace.workspaceFolders[0].uri.fsPath;
  const doc = await vscode.workspace.openTextDocument(path.join(folder, "app.pyweb"));
  const editor = await vscode.window.showTextDocument(doc);
  assert.strictEqual(doc.languageId, "pyweb");
  let failed = 0;
  for (const [name, step] of Object.entries(steps)) {
    try {
      await step(doc, editor);
      console.log(`  ok   ${name}`);
    } catch (err) {
      failed += 1;
      console.log(`  FAIL ${name}\n${err.stack || err}`);
    }
  }
  if (failed) throw new Error(`${failed} of ${Object.keys(steps).length} checks failed`);
};
