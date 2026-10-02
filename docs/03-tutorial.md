# Tutorial: a notes app

We'll build a small multi-user notes app in one file: a page with
browser-side state, a reusable component, a SQLite database behind
server functions, and sign-in with sessions. The finished app is about
90 lines.

## 1. A page with state

```pyweb
from pyweb import App

app = App(title="Notes")


@app.page("/")
def Home():
    notes = ["Try PyWeb"]
    draft = ""

    def save():
        if draft.strip():
            notes.append(draft.strip())
        draft = ""

    <main>
        <h1>Notes ({len(notes)})</h1>
        <form onsubmit={save}>
            <input bind={draft} placeholder="Write a note" />
            <button disabled={not draft.strip()}>Save</button>
        </form>
        <ul>
            for note in notes:
                <li>{note}</li>
        </ul>
    </main>
```

- `bind={draft}` keeps the input and the variable in sync both ways.
- `onsubmit={save}` runs `save` and prevents the browser's default form
  submission.
- `notes.append(...)` mutates a list that markup reads. PyWeb makes the
  update copy-on-write, so the `<ul>` adds exactly one `<li>` and the
  heading's count updates.
- `disabled={not draft.strip()}` re-evaluates as you type.

Inside handlers, assigning a page variable (`draft = ""`) updates the
page state. There is no `nonlocal` and no `setState`.

## 2. A component

Any capitalised function containing markup is a component. Parameters
are props; `children` receives nested markup.

```pyweb
from pyweb import App, component

app = App(title="Notes")


@component
def Note(text, on_delete):
    <li>
        <span>{text}</span>
        <button onclick={on_delete} aria-label="Delete">×</button>
    </li>


@app.page("/")
def Home():
    notes = ["Try PyWeb", "Write docs"]

    def delete(i):
        del notes[i]

    <ul>
        for i, text in enumerate(notes):
            <Note text={text} on_delete={lambda: delete(i)} />
    </ul>
```

`onclick={delete(i)}` would also work: an event attribute's expression
runs when the event fires, not while rendering.

## 3. Persist to a database with server functions

Functions marked `@server` run only on the server. Calling one from a
handler sends a typed RPC request and waits for the result; the
compiler generates both ends.

```pyweb
from pyweb import App, RPCError, server
from pyweb.db import connect

app = App(title="Notes")
db = connect("sqlite:///notes.db")
db.execute("create table if not exists notes (id integer primary key, body text not null)")


def all_notes():
    return db.execute("select id, body from notes order by id").dicts()


@server
def add_note(body: str) -> list:
    if not body.strip():
        raise RPCError("validation_error", "A note can't be empty.")
    db.execute("insert into notes (body) values (?)", (body.strip(),))
    return all_notes()


@server
def delete_note(note_id: int) -> list:
    db.execute("delete from notes where id = ?", (note_id,))
    return all_notes()


@app.page("/")
def Home():
    notes = all_notes()
    draft = ""
    error = ""

    def save():
        try:
            notes = add_note(draft)
            draft = ""
            error = ""
        except RPCError as e:
            error = str(e)

    def remove(note):
        notes = delete_note(note["id"])

    <main>
        <form onsubmit={save}>
            <input bind={draft} />
            <button>Save</button>
        </form>
        <p class="error">{error}</p>
        <ul>
            for note in notes:
                <li>{note["body"]} <button onclick={remove(note)}>×</button></li>
        </ul>
    </main>
```

What the compiler did (run `pyweb inspect app.pyweb` to see it):

- `notes = all_notes()` calls a module function, so it runs **on the
  server** for each request; its value is rendered into the HTML and
  sent to the browser as initial state.
- `save` and `remove` are compiled to `async` JavaScript functions that
  call `/__pyweb/rpc/add_note` and `/__pyweb/rpc/delete_note`.
- `db` and `connect` never reach the browser. Referencing them from a
  handler is a compile error that points at the line.
- `RPCError("validation_error", ...)` becomes a typed error in the
  browser; `except RPCError as e` catches it there.

## 4. Sign-in with sessions

`pyweb.session` stores a signed, HTTP-only cookie. Pages can redirect
before rendering.

```pyweb
from pyweb import App, redirect, server, session

app = App(title="Notes")


@server
def sign_in(name: str) -> bool:
    if not name.strip():
        return False
    session.login(name.strip())
    return True


@app.page("/login")
def Login():
    name = ""
    error = ""

    def go():
        if sign_in(name):
            window.location.href = "/"
        else:
            error = "Enter a name"

    <form onsubmit={go}>
        <input bind={name} placeholder="Your name" />
        <button>Sign in</button>
        <p>{error}</p>
    </form>


@app.page("/")
def Home():
    user = session.user()
    if not user:
        return redirect("/login")
    who = user["sub"]

    <h1>Hello, {who}</h1>
```

Only `who` is sent to the browser. The full session record in `user`
stays on the server because browser code never reads it.

In server functions, `session.require()` returns the session or raises
an `unauthenticated` RPC error. Set `PYWEB_AUTH_SECRET` in production
(see [Authentication](10-auth.md)).

## 5. Ship it

```bash
pyweb check app.pyweb
pyweb build app.pyweb --out dist --production
PYWEB_AUTH_SECRET=change-me pyweb serve dist
```

The complete, tested versions of these apps live in
[`examples/`](https://github.com/MaanavKrishna/PyWeb/tree/main/examples).
