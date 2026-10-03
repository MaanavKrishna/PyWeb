// Tokenizes .pyweb samples with the same TextMate engine VS Code uses and
// checks the scopes. Run: npm test
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const vsctm = require("vscode-textmate");
const oniguruma = require("vscode-oniguruma");

const wasm = fs.readFileSync(require.resolve("vscode-oniguruma/release/onig.wasm")).buffer;
const onigLib = oniguruma.loadWASM(wasm).then(() => ({
  createOnigScanner: (patterns) => new oniguruma.OnigScanner(patterns),
  createOnigString: (s) => new oniguruma.OnigString(s),
}));

// A stand-in for VS Code's Python grammar: enough to see where Python is used.
const python = {
  scopeName: "source.python",
  patterns: [
    { match: "\\b(def|for|in|if|else|return|import|from|not|and|or)\\b", name: "keyword.control.python" },
    { match: "\"[^\"]*\"|'[^']*'", name: "string.quoted.python" },
    { match: "\\b\\d+\\b", name: "constant.numeric.python" },
  ],
};

const registry = new vsctm.Registry({
  onigLib,
  loadGrammar: async (scope) => {
    if (scope === "source.pyweb") {
      const file = path.join(__dirname, "..", "syntaxes", "pyweb.tmLanguage.json");
      return vsctm.parseRawGrammar(fs.readFileSync(file, "utf8"), file);
    }
    if (scope === "source.python") return python;
    return null;
  },
});

const SAMPLE = [
  'from pyweb import App',                                       // 0
  '@app.page("/")',                                              // 1
  'def Home():',                                                 // 2
  '    todos = [{"id": 1}]',                                     // 3
  '    <main class={{"dark": True}}>',                           // 4
  '        <TodoItem',                                           // 5
  '            todo={todos[0]}',                                 // 6
  '            onclick={add} />',                                // 7
  '        for t in todos:',                                     // 8
  '            <li data-id="x&amp;y">{t["id"]} left</li>',       // 9
  '        <!-- note -->',                                       // 10
  '        <input bind={draft} disabled />',                     // 11
  '    </main>',                                                 // 12
];

function scopesOf(tokensByLine, line, text) {
  const src = SAMPLE[line];
  const at = src.indexOf(text);
  assert(at >= 0, `"${text}" not on line ${line}`);
  const tok = tokensByLine[line].find((t) => t.startIndex <= at && at < t.endIndex);
  return tok.scopes.join(" ");
}

(async () => {
  const grammar = await registry.loadGrammar("source.pyweb");
  let state = vsctm.INITIAL;
  const lines = [];
  for (const src of SAMPLE) {
    const r = grammar.tokenizeLine(src, state);
    lines.push(r.tokens);
    state = r.ruleStack;
  }
  const has = (line, text, scope) => {
    const s = scopesOf(lines, line, text);
    assert(s.includes(scope), `line ${line} "${text}": expected ${scope}, got ${s}`);
  };
  const lacks = (line, text, scope) => {
    const s = scopesOf(lines, line, text);
    assert(!s.includes(scope), `line ${line} "${text}": did not expect ${scope}, got ${s}`);
  };

  has(0, "from", "keyword.control.python");          // Python lines are Python
  has(2, "def", "keyword.control.python");
  has(4, "main", "entity.name.tag.pyweb");           // HTML tag
  has(4, "class", "entity.other.attribute-name.pyweb");
  has(4, '"dark"', "meta.embedded.expression.pyweb"); // nested braces stay inside the expression
  has(4, "True", "meta.embedded.expression.pyweb");
  has(4, ">", "punctuation.definition.tag.end.pyweb");
  has(5, "TodoItem", "entity.name.type.class.component.pyweb"); // component tag
  has(6, "todo", "entity.other.attribute-name.pyweb");          // multi-line tag attributes
  has(6, "todos", "meta.embedded.expression.pyweb");
  has(7, "onclick", "entity.other.attribute-name.event.pyweb");
  has(7, "/>", "punctuation.definition.tag.end.pyweb");
  has(8, "for", "keyword.control.python");            // control flow is Python
  lacks(8, "for", "meta.tag.pyweb");
  has(9, '"x', "string.quoted.double.pyweb");
  has(9, "&amp;", "constant.character.entity.pyweb");
  has(9, '"id"', "string.quoted.python");             // Python inside {…}
  lacks(9, "left", "source.python string");            // text content is not Python
  has(9, "</", "punctuation.definition.tag.begin.pyweb");
  has(10, "note", "comment.block.html.pyweb");
  has(11, "bind", "entity.other.attribute-name.event.pyweb");
  has(11, "disabled", "entity.other.attribute-name.pyweb");
  has(12, "main", "entity.name.tag.pyweb");

  // The snippets and manifest are valid JSON with what VS Code expects.
  const pkg = require("../package.json");
  const snippets = require("../snippets/pyweb.json");
  assert.strictEqual(pkg.contributes.languages[0].extensions[0], ".pyweb");
  assert(Object.values(snippets).every((s) => s.prefix && Array.isArray(s.body)));
  console.log("grammar ok:", SAMPLE.length, "lines,", Object.keys(snippets).length, "snippets");
})().catch((e) => { console.error(e); process.exit(1); });
