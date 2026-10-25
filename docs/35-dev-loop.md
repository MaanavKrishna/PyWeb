# The dev loop

`pyweb dev app.pyweb` is where you spend your day, so it does a lot for
you: saves show up in the open page without a reload, any element opens
its own line in your editor, errors come with a "Fix with AI" button, you
can step back through what the page did, and a link lets someone else
try what you're building. Everything on this page exists only under
`pyweb dev`: production builds, `pyweb serve` and `TestClient` are
unchanged.

## Your first run

```bash
pyweb new                 # in a terminal: asks for a name, a starter and colours
cd my-app
pyweb dev app.pyweb       # opens the browser
```

`pyweb new` with no name asks three questions: the folder name, a
starter (each with a line saying what it is) and a colour preset, which
it writes into `App(theme=Theme(...))`. `pyweb new shop --template todo
--theme rose` does the same without asking.

`pyweb dev` opens your browser (not in CI, over SSH, or with
`--no-open`) and, the first time a project runs, shows a short Welcome
panel: what to try, and the snippet that connects your AI assistant.
**Got it** hides it for good (`PYWEB_WELCOME=0` turns it off).

## Saves update the page in place

Change some markup, a style or a function and save. The open page
updates in about a tenth of a second, without a reload and without a
flash:

- **page and component state stays.** Every page variable and every
  component's variables keep their values; two `<Counter />`s keep their
  own counts. A variable you renamed, or whose type changed (a number
  that is now a string), starts over.
- **form fields, scroll position, focus and the text selection stay**,
  for fields that have an `id` or a `name`.
- **a stylesheet-only save** swaps that stylesheet and nothing else.

When the page's structure changed too much to swap in place (a new
layout, different npm packages), it reloads, and the same state comes
back. After a compile error the browser shows the error; fix it, save,
and you're back where you were, state included. Add `?fresh` to the URL
to start over every time.

The first start is fast too: parse results and compiled code are kept in
`~/.cache/pyweb` (`PYWEB_CACHE_DIR` to move it, `PYWEB_CACHE=0` to turn
it off), keyed by each file's contents and the PyWeb and Python versions.

## Alt-click to open the source

Hold **Alt** (Option on a Mac) and point at anything: it is outlined,
with the components it sits in and its file and line. **Alt-click** it
to open that line in your editor. It also opens the **inspector**: the
chain of components around the element, each with its live variables.
Type a new value (JSON: `3`, `"hi"`, `[1, 2]`) and the page follows.

The editor is `$PYWEB_EDITOR` when you set it, a command with `{file}`
and `{line}` in it:

```bash
export PYWEB_EDITOR='code -g {file}:{line}'        # VS Code
export PYWEB_EDITOR='idea --line {line} {file}'    # JetBrains
export PYWEB_EDITOR='nvim +{line} {file}'          # in a terminal editor, use a new window
```

Otherwise the first of VS Code (`code`), Cursor, Windsurf, Zed and
Sublime Text found on your `PATH`; with none of them, the inspector shows
a `vscode://` link. Elements that come from the UI kit open the line in
your app that uses them.

## Fix with AI, and Edit with AI

When the app doesn't compile, or a page fails with an exception, the
error page has a **Fix with AI** button. **Alt+Shift-click** any element
to say what should change ("make this a card with a delete button").

Either way you get a **diff** of one file in your app, already compiled:
a change that doesn't compile is shown but can't be applied. **Apply**
writes it (only if the file hasn't changed since), the page updates in
place, and **Undo** puts the old version back (every replaced version is
also kept in `.pyweb/history/`).

Who answers, in this order:

1. **Your assistant, through `pyweb mcp`.** If the assistant's app
   supports MCP *sampling*, `pyweb mcp` hands the request to it: its own
   model does the work, and the app asks you before each request.
2. **An API key** in the environment `pyweb dev` runs in:
   `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY` / `OPENAI_BASE_URL` (any
   OpenAI-compatible server, Ollama included), with `AI_MODEL` to choose
   the model.
3. **Nobody yet.** The page gives you the prompt to copy into any
   assistant, and a connected assistant can pick the request up with the
   `pyweb_assist_requests` tool (the `fix_errors` prompt starts there).

What's sent is the file and the error (or your instruction). Credentials
in them, in the formats `pyweb check` knows and random-looking values in
secret-named variables, are replaced by placeholders before sending and
put back in the answer.

## Time travel

The toolbar's **Timeline** tab (the ⚡ badge, bottom left) records every
change to the page's variables with what caused it: a click on which
button, typing in which field, Enter, a server function call. Each event
is one step. Drag the slider, press **Step back** or click a step and
every variable goes back to its value then; the page is paused while you
look (clicks and typing are held back). **Resume** puts the latest values
back. Only the page's own state moves: data on the server isn't rolled
back. The last 500 changes are kept.

## On your phone, and with someone else

```bash
pyweb dev app.pyweb --host 0.0.0.0     # prints the network URL and a QR code
pyweb dev app.pyweb --share            # a temporary public link (and a QR code)
```

`--host 0.0.0.0` listens on your network: scan the QR code with a phone
on the same Wi-Fi.

`--share` starts a tunnel, with `cloudflared` when it's installed or
`ssh` to localhost.run otherwise, and prints a link like
`https://quiet-fox-12.trycloudflare.com/__pyweb/share?t=…`. Anyone with
the link can use the app and sees your saves live. Nobody gets in with
the address alone: the link's token sets a cookie, and every other
request through the tunnel is refused. The link works until you stop
`pyweb dev`.

People on the network or coming through a share link get the app and
hot reload, never your tools: no toolbar (it shows SQL and request
data), inspector, AI help, editor opening or theme saving. Those
answer only requests from your machine to `localhost`.

## Security limits

| Feature | Who can use it |
|---|---|
| Hot reload, the error page | Everyone who can open the page |
| Toolbar, timeline, inspector, Alt-click, Fix/Edit with AI, theme saving, Welcome | You: a request from this machine, to a loopback address, from the page itself |
| Files AI may change | `.pyweb` files inside the app folder; only after Apply; never if the file changed since |
| Files Alt-click may open | `.pyweb` files in the app folder or in PyWeb's UI kit |
| `--share` guests | The app, with the link's cookie; nothing under `/__pyweb/dev/` but change polling |
| `pyweb mcp`'s view of the dev server | With the token in `.pyweb/dev.json` (readable by you only), from this machine |

The [threat model](https://github.com/MaanavKrishna/PyWeb/blob/main/THREAT_MODEL.md) lists each of these with the tests
that prove it.
