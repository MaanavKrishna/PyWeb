# Layouts and navigation

Most sites have a header, navigation and footer around every page. A
layout writes that once. Links between pages then load without a full
page reload, and the layout stays on screen with its state.

## Layouts

```pyweb
from pyweb import App

app = App(title="Shop", stylesheets=["/static/app.css"])


@app.layout
def Shell(children):
    menu_open = False

    def toggle():
        menu_open = not menu_open

    <header>
        <a href="/">Shop</a>
        <button onclick={toggle}>{"Close" if menu_open else "Menu"}</button>
        if menu_open:
            <nav>
                <a href="/">Home</a>
                <a href="/products">Products</a>
                <a href="/about">About</a>
            </nav>
    </header>
    <main>{children}</main>
    <footer>Made with PyWeb</footer>


@app.page("/")
def Home():
    <h1>Welcome</h1>


@app.page("/products")
def Products():
    <h1>Products</h1>


@app.page("/about", layout=None)
def About():
    <h1>About (no layout)</h1>
```

- `{children}` marks where the page goes. It must appear exactly once.
- `@app.layout` wraps every page. `@app.layout("/admin")` wraps only
  pages whose route starts with `/admin`. Layouts nest: a page under
  `/admin` gets the `/` layout outside and the `/admin` one inside.
- `layout=None` on `@app.page` opts a page out; `layout="Admin"` picks
  a layout by name (with the layouts above it).
- A layout is like a page: its body runs on the server for each
  request (it can read `pyweb.request`, the session or the database),
  and it can have state, handlers and components. Its state is its own;
  pages can't read a layout's variables.

## Client-side navigation

When the current page has any interactivity, clicking a link to
another page of the same app:

1. fetches the next page's HTML (often already fetched: links are
   prefetched when the pointer rests on them or they get focus);
2. keeps the layouts both pages share and replaces what's inside them;
3. updates the title, description and social tags and loads the new
   page's module;
4. updates the address bar, so back, forward and reload work, and
   restores the scroll position when you go back.

So the `menu_open` state above survives moving between pages, and only
the page's own HTML is transferred and rendered.

A layout is kept only while its code and its server-rendered output are
the same for both pages. A layout that shows something that changes
from page to page (such as `request.path`) is re-rendered on each
navigation, so it never shows stale data, but its browser state resets.
Mark the current link with `aria-current` instead (below).

A page's own state starts fresh each time you navigate to it, as it
would with a full load. If a page starts timers or other work, stop it
in `on_unmount`, which runs when the page is navigated away from:

```pyweb
from pyweb import App

app = App()


@app.page("/clock")
def Clock():
    ticks = 0
    timer = None

    def on_mount():
        timer = setInterval(lambda: tick(), 1000)

    def tick():
        ticks += 1

    def on_unmount():
        clearInterval(timer)

    <p>{ticks} seconds on this page</p>
```

Subscriptions (`subscribe(...)`) and watches set up in `on_mount` stop
by themselves when the page is left.

The browser falls back to a normal page load whenever navigation can't
be done in place: links with `target`, `download`, `rel="external"` or
`data-pw-reload`; other sites; files under `/static/`; responses that
aren't PyWeb pages; and pages that need npm packages the current page
didn't load. Links to anchors on the same page scroll as usual.

To turn client-side navigation off for the whole app, use
`App(client_nav=False)`. To skip prefetching for one link, add
`data-pw-prefetch="false"`.

From browser code, `navigate("/path")` (from `pyweb.browser`) goes to
another page the same way, for example after a form is saved.

Navigation fires a `pyweb:navigate` event on `window` and announces the
new page title to screen readers.

## Marking the current link

Links to the current page get `aria-current="page"`, and links to a
section that contains it (`/products` while on `/products/7`) get
`aria-current="true"`. The server adds them, and the browser updates
them after each navigation. Style them with CSS:

```text
nav a[aria-current] { font-weight: 600; }
nav a[aria-current="page"] { text-decoration: underline; }
```

A link where you set `aria-current` yourself keeps your value.
