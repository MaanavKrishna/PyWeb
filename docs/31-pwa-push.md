# Installable apps and push notifications

One setting makes a PyWeb app installable: people can add it to their
home screen or dock, it opens in its own window, keeps working when the
connection drops, and can send notifications.

```python
from pyweb import App

app = App(title="Field Notes", pwa=True)
```

For more control, pass a `PWA`:

```python
from pyweb import App
from pyweb.pwa import PWA

app = App(title="Field Notes", pwa=PWA(
    name="Field Notes",
    short_name="Notes",          # under the icon on a phone (12 characters or fewer)
    icon="static/logo.png",      # a square PNG, 512×512 or larger; or an SVG
    theme_color="#0f766e",       # the window's title bar and the phone's status bar
    background_color="#ffffff",  # the splash screen while the app starts
    offline_page="/offline",     # one of your pages; default: a built-in one
    cache_pages=True,            # keep pages people opened (signed out) for offline reading
))
```

| Setting | Default | What it does |
|---|---|---|
| `name`, `short_name` | the app's `title` | the installed app's name |
| `description` | the app's `description` | shown by app stores and install dialogs |
| `icon` | a plain icon in `theme_color` | made into 192, 512 and 180 (iPhone) pixel PNGs |
| `display` | `"standalone"` | `"fullscreen"`, `"minimal-ui"` or `"browser"` |
| `start_url`, `scope` | `"/"` | where the app opens, and which pages belong to it |
| `orientation` | any | e.g. `"portrait"` |
| `offline_page` | built-in | shown when a page can't load without a connection |
| `cache_pages` | `False` | keep pages for offline reading (see below) |
| `precache` | none | more paths to store when the app is installed |
| `shortcuts`, `categories` | none | passed into the manifest as they are |
| `push` | `True` | allow push notifications |

## What you get

Every page links to `/manifest.webmanifest` and loads a small script that
registers the service worker `/sw.js`. Browsers then offer "Install" (in
the address bar on desktops, "Add to Home Screen" on phones).

The service worker:

- keeps static files: fingerprinted ones (from `pyweb build`) straight
  from its cache, others from the network with the kept copy as the
  fallback offline;
- shows the offline page when a page can't load;
- with `cache_pages=True`, keeps pages people opened **while signed out**,
  so they can read them again offline. Pages of signed-in visitors are
  never kept, so a shared computer never shows someone's private page;
- never touches server function calls, forms, live updates, uploads,
  anything but `GET`, and other sites' files.

When you deploy a new version, `/sw.js` changes with it and browsers pick
it up on the next visit, dropping what the old version kept.

The worker is registered through a Trusted Types policy (`pyweb-sw`) that
accepts `/sw.js` and nothing else, so PyWeb's strict
[Content-Security-Policy](13-security.md) stays on: injected script can't
register a worker of its own.

Is the page running as an installed app? The script sets a
`pyweb_display` cookie to `standalone` or `browser`.

## Push notifications

Install what push needs, and make a key:

```bash
pip install "pyweb-stack[push]"
pyweb push key            # prints PYWEB_VAPID_KEY=...: set it in production
```

Browsers only let a page ask for permission when someone clicks
something, so give them a button. No code needed:

```pyweb
from pyweb import App

app = App(title="Field Notes", pwa=True)


@app.page("/settings")
def Settings():
    <section>
        <button data-pw-push="subscribe">Turn on notifications</button>
        <button data-pw-push="unsubscribe">Turn them off</button>
    </section>
```

`data-pw-push="toggle"` does both. The page's `<html>` element gets
`data-pw-push="default|subscribed|unsubscribed|denied|unsupported"`, so
CSS can show the right button:

```css
html:not([data-pw-push="subscribed"]) [data-pw-push="unsubscribe"],
html[data-pw-push="subscribed"] [data-pw-push="subscribe"] { display: none; }
```

Each change also fires a `pyweb:push` event on `document`.

A subscription belongs to whoever is signed in when they turn
notifications on (and to their current organization). Send from a
server function, a job or a script:

```python
from pyweb import push, server

auth = app.use_auth()


@server(login=True)
def export(project_id: int):
    ...
    push.send(auth.user(), "Your export is ready", "Tap to download it", url="/exports")
```

- `push.send(to, title, body=None, *, url="/", tag=None, icon=None,
  ttl=86400, urgency="normal")`: `to` is a user (or user id: every browser
  they turned notifications on in), a `PushSubscription`, or a list. A tap
  opens `url`, which must be a page of your app. A newer notification with
  the same `tag` replaces the older one. `ttl` is how long the push service
  keeps it for a phone that's offline; `urgency="low"` saves batteries.
- `push.broadcast(title, body, org=None)`: every subscribed browser (of
  one organization).
- `push.subscriptions(user)`: the rows in `pyweb_push_subscriptions`.

Each notification is a [background job](24-jobs.md): encrypted for the
receiving browser (RFC 8291), signed with your app's key (VAPID, RFC
8292) and posted to the browser's push service. When the push service
answers that a subscription is gone (the person turned notifications off,
or uninstalled), its row is deleted; when it's busy, the job retries.
Messages can be up to about 4 KB.

### Keys and settings

| Variable | |
|---|---|
| `PYWEB_VAPID_KEY` | The key notifications are signed with (`pyweb push key`). Without it, one is derived from `PYWEB_AUTH_SECRET`; but browsers tie each subscription to the key, so rotating the secret would mean everyone turning notifications on again. Set it in production and keep it. |
| `PYWEB_PUSH_CONTACT` | A `mailto:` or `https:` address push services can contact about your app's traffic. Apple requires one. |
| `PYWEB_PUSH_HOSTS` | Push services beyond Google's, Mozilla's, Apple's and Microsoft's (comma-separated host names). |

Only the browsers' push services are accepted as endpoints, so a visitor
can't make your server send requests anywhere else. "Download my data"
includes a user's subscriptions and deleting the account deletes them.

## Testing

`pyweb.push.decrypt(body, browser_private_key, auth)` is the browser's
side of the encryption, so a test can stand up a fake push service and
read what your app sends; PyWeb's own tests (`tests/test_push.py`) show
how.

## Limits

- iPhones and iPads show notifications only for apps added to the home
  screen (iOS 16.4 and later).
- Push needs HTTPS (or `localhost` while developing).
- Pages that need a signed-in visitor aren't available offline; the
  offline page is shown instead.
