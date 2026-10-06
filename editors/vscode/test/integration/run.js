// Runs the extension inside a real VS Code (downloaded by @vscode/test-electron) against
// the real language server: `node test/integration/run.js [version ...]`, default the
// oldest VS Code the extension supports and the current stable one. Needs Python with
// PyWeb (PYWEB_PYTHON, default python3) and, on Linux, a display (xvfb-run).
const fs = require("fs");
const os = require("os");
const path = require("path");
const { runTests } = require("@vscode/test-electron");

const root = path.resolve(__dirname, "../..");
const oldest = require(path.join(root, "package.json")).engines.vscode.replace(/^\^/, "");

async function main() {
  const versions = process.argv.slice(2).length ? process.argv.slice(2) : [oldest, "stable"];
  const python = process.env.PYWEB_PYTHON || "python3";
  for (const version of versions) {
    const work = fs.mkdtempSync(path.join(os.tmpdir(), "pyweb-vscode-"));
    fs.cpSync(path.join(__dirname, "workspace"), work, { recursive: true });
    fs.mkdirSync(path.join(work, ".vscode"), { recursive: true });
    fs.writeFileSync(path.join(work, ".vscode", "settings.json"),
      JSON.stringify({ "pyweb.server.command": [python, "-m", "pyweb.cli", "lsp"] }, null, 2));
    console.log(`VS Code ${version}`);
    await runTests({
      version,
      extensionDevelopmentPath: root,
      extensionTestsPath: path.join(__dirname, "suite.js"),
      launchArgs: [work, "--disable-extensions", "--disable-workspace-trust", "--skip-welcome",
        "--skip-release-notes", "--disable-gpu", `--user-data-dir=${path.join(work, ".user")}`],
    });
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
