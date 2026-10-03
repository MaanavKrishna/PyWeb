# Building AI apps

AI features need three things from a web framework: a server side that
can hold API keys and call a model, a way to show the answer while it's
still being generated, and safe rendering of what the model wrote.
PyWeb has all three, without a separate frontend.

## Start from the template

```text
pyweb new mychat --template ai-chat
cd mychat
pyweb dev app.pyweb
```

The app runs straight away with a built-in demo model. To use a real
one, set environment variables before starting it:

```text
ANTHROPIC_API_KEY=...                         Anthropic (AI_MODEL defaults to claude-sonnet-5-5)
OPENAI_API_KEY=...  AI_MODEL=...              OpenAI
OPENAI_BASE_URL=http://localhost:11434/v1 \
  AI_MODEL=llama3                             Ollama, vLLM, LM Studio or any OpenAI-compatible server
```

The template calls the provider's HTTP API with the standard library,
so there's nothing else to install. Swap in an official SDK if you
prefer: the server function only has to `yield` text.

## Streaming the answer

A `@server` function that `yield`s streams each value to the browser as
it's produced (see [Streaming results](06-server-functions.md)). This is
the core of the template, slightly shortened:

```pyweb
import os

from pyweb import App, Markdown, RPCError, server

app = App(title="AI chat")


def ask_model(messages):
    """Yield pieces of text from your model provider (see the template for real ones)."""
    for word in ("Streaming ", "**works**."):
        yield word


@server
def reply(messages: list):
    if not messages or messages[-1].get("role") != "user":
        raise RPCError("validation_error", "the last message must be from the user")
    yield from ask_model(messages[-20:])


@app.page("/")
def Chat():
    messages = []
    draft = ""
    busy = False
    stream = None

    async def send():
        if not draft.strip() or busy:
            return
        messages.append({"role": "user", "content": draft})
        messages.append({"role": "assistant", "content": ""})
        draft = ""
        busy = True
        stream = reply(messages[:-1])
        async for piece in stream:
            messages[-1]["content"] += piece
        busy = False

    def stop():
        if stream:
            stream.cancel()

    def on_unmount():
        stop()

    <main>
        for m in messages:
            <div class={"bubble " + m["role"]}>
                <Markdown text={m["content"]} />
            </div>
        <form onsubmit={send}>
            <input bind={draft} placeholder="Message" />
            if busy:
                <button type="button" onclick={stop}>Stop</button>
            else:
                <button>Send</button>
        </form>
    </main>
```

- **Each piece is shown as it arrives.** `messages[-1]["content"] += piece`
  changes page state, so the last bubble re-renders.
- **Stop really stops.** `stream.cancel()` aborts the request. On the
  server the generator is closed, which closes the connection to the
  model provider, so you don't pay for tokens nobody reads. Leaving the
  page does the same through `on_unmount`.
- **Errors reach the page.** Raise `RPCError("unavailable", "...")` in
  the generator (the template does this for provider errors) and the
  `async for` raises it; catch it with `except RPCError as e`.

## Showing model output: `<Markdown>`

Models answer in Markdown. `<Markdown text={...} />` (from `pyweb`)
renders it on the server for the first page load and in the browser as
the text changes, using the same rules in both places:

- headings, emphasis, inline and fenced code, lists, quotes, tables,
  links and images;
- an unclosed code fence runs to the end, so half-streamed code blocks
  look right;
- **it is safe on untrusted text**: raw HTML is shown as text, and links
  and images only accept `http(s)`, `mailto` and relative URLs, so a
  prompt-injected reply can't run script or plant a `javascript:` link.

It renders into `<div class="markdown">`; add classes with `class=`.

## Keys, cost and abuse

- **Keys stay on the server.** Read them with `os.environ` inside server
  functions. The compiler stops you from sending a variable named like a
  secret (`api_key`, `token`, ...) to the browser.
- **Treat the conversation as user input.** The browser sends the whole
  history with each call, so the server function should check roles,
  trim the length (the template keeps the last 20 messages of up to
  4,000 characters) and set the model's `max_tokens`.
- **Limit who can call it.** `pyweb serve` rate-limits RPC calls per
  client (120 a minute by default). For a public app, also require a
  login (`session.require()`) and keep per-user budgets in your
  database.

## Other AI patterns

- **Long jobs with progress**: yield progress dicts from the server
  function and show a bar.
- **Background work**: start it with `pyweb.jobs` and `publish()`
  progress to the page over [live updates](06-server-functions.md).
- **npm packages for AI UIs** (syntax highlighting, charts of token
  usage) work in browser code: see [npm packages](18-npm-packages.md).
