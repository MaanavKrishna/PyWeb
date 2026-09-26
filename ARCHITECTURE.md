# PyWeb Architecture — Track A: compiler + browser runtime

`.pyweb` files mix Python logic with JSX-like markup. The compiler lowers them
to static HTML (SSR) plus a small signals-based JS bundle that hydrates the
SSR DOM in the browser.

## Pipeline (`pyweb/compiler/pipeline.py`)

`build_text(source, filename, route)` → `Artifacts(js, html, css, css_file,
sourcemap, manifest, live)`:

1. `parser.parse_text` — markup lines (starting with `<`, or inside an open
   element incl. fragments `<>`) are replaced with line-preserving `pass`
   placeholders so CPython `ast` parses the logic; a tag tokenizer then builds
   the UI AST (`ast.py`: `Element/Text/DynText/For/Cond/CompUse/Slot/...`).
   Every UI node gets a unique `hid` used as the SSR↔JS join key.
2. `analyzer` — signals, handlers, routes, component prop schemas; raises
   `CompileError` (file/line/col/snippet/hint) on missing/extra props and
   unknown names.
3. `placement` / `reactivity` / `rpc` — live-region marking, expression
   validation, RPC manifest for handlers.
4. `codegen.css.extract` — `<style>` blocks + `css={...}`/`css="..."` props
   become a hashed `.css` bundle; SSR `<link>`s it.
5. `codegen.js.JSGen.generate` — hydration JS + inline VLQ `SourceMap`
   (no deps) exposed as `artifacts.sourcemap`; `js_with_map()` appends the
   `window.__pyweb_error(map)` overlay hook + data-URL `sourceMappingURL`.
6. `codegen.html.SSR.render` — static HTML with `<!--pw:hid-->` /
   `data-pw-hid` markers and component/slot inlining.

## JS emitter modes (`codegen/js.py`)

- **hydrate** (page top level): locate SSR nodes via `takeover`/`takeoverText`
  /`querySelector('[data-pw-hid]')`. Each marker hydrates once (`_claim`); a
  repeated component instance keeps its SSR output static.
- **create** (inside `liveList` row / `liveIf` branch callbacks): build fresh
  DOM with `createElement`; prop refs rename to `__p<hid>_<prop>` consts.
- Components instantiate their template inline with prop consts + slot
  substitution; missing optional props fall back to declared defaults.
- `bind={sig}` (or `value=`/`checked=`) lowers to `PyWeb.bindEl` two-way
  binding; `onclick={fn}` to `addEventListener`. Runtime helpers are
  null-safe so partial SSR never crashes hydration.

## Browser runtime (`pyweb/runtime/browser/runtime.js`)

No dependencies: `signal/computed/effect`, `dynText/dynAttr/bindEl`,
`liveList` (keyed; same key + same object identity reuses the row node,
reorders via `insertBefore`; a new object for a known key re-renders so rows
never go stale), `liveIf` (branch swap with disposal), `takeover*`
hydration, `makeErrorHook(map)` overlay mapping JS lines back through the
VLQ map (`hook.mapPosition`).

## Pure-Python API (`pyweb/components/__init__.py`)

`Page()/Heading()/Button()/Input()/...` build `Element` ASTs in `.py` files;
`bind(name)` → `BindRef`, `onclick(name)` → `HandlerRef`, `expr(code)` →
`Dyn`, `css={...}` dict → `CssStatic`. Same SSR/JS codegen as `.pyweb`.

## Tests

`tests/fakedom.js` (fake DOM) + `tests/ssr_dom.js` (SSR HTML loader) drive
`runtime.js` and real generated bundles under node via `_node_harness.py`:
live append/remove/reorder, conditional toggle, prop errors, VLQ roundtrip,
CSS extraction, pyAPI parity, example builds.
