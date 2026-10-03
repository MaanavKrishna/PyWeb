# npm packages

Browser code can use packages from npm: charts, maps, date pickers,
editors, confetti. You don't need Node.js. PyWeb downloads the package
from the npm registry, keeps only the files the browser loads, and
serves them from your app.

## Add a package

Run this in the folder that holds `app.pyweb`:

```text
pyweb add chart.js/auto
```

```text
  @kurkle/color@0.3.4  1 file(s)  (dependency)
  chart.js@4.5.1  3 file(s)
pyweb.lock: 2 package(s); files in static/vendor/
```

`pyweb add` does four things:

1. It picks the newest version that matches the range you give
   (`pyweb add chart.js@^4`, `pyweb add lit@3.2`), or the latest one.
2. It downloads the package and checks it against the registry's
   sha512 checksum.
3. It follows the `import` statements from the package's browser entry
   point and copies only the files they reach into
   `static/vendor/<name>@<version>/`. Dependencies are installed the
   same way.
4. It records versions, checksums and file lists in `pyweb.lock`.

Commit `pyweb.lock` and `static/vendor/`. Your app then builds and runs
without network access. Other commands:

```text
pyweb add                    reinstall exactly what pyweb.lock lists
pyweb add chart.js@^4        add, or change the requested range
pyweb remove chart.js        remove a package and the files only it used
```

Locked versions are kept until you ask for a different range, so
running `pyweb add` again never upgrades a package by surprise.

## Use it in browser code

`npm(package, export)` binds a name to one of the package's exports at
the top of the file:

```pyweb
from pyweb import App, npm

Chart = npm("chart.js/auto")             # the default export
confetti = npm("canvas-confetti")
dates = npm("date-fns", "*")             # the whole module

app = App()


@app.page("/")
def Sales():
    canvas = None
    chart = None
    total = 3
    until = ""

    def on_mount():
        chart = Chart(canvas, {"type": "bar",
                               "data": {"labels": ["Mon", "Tue", "Wed"],
                                        "datasets": [{"label": "Orders", "data": [1, 2, 3]}]}})

    def add():
        total += 1
        chart.data.labels.append("Thu")
        chart.data.datasets[0].data.append(total)
        chart.update()
        until = dates.format(dates.addDays(dates.startOfToday(), total), "EEE d MMM")
        confetti(particleCount=80, spread=60)

    <main>
        <canvas ref={canvas}></canvas>
        <button onclick={add}>Add a day</button>
        <p>{total} orders, planned until {until}</p>
    </main>
```

The rules:

- **Calling a class constructs it.** `Chart(canvas, config)` becomes
  `new Chart(canvas, config)` in JavaScript, so you write Python call
  syntax for both functions and classes.
- **Keyword arguments become an options object.**
  `confetti(particleCount=80, spread=60)` calls
  `confetti({particleCount: 80, spread: 60})`, the convention most
  JavaScript libraries use.
- **Objects are used as they are.** Attributes, methods and lists on a
  package's objects are the real JavaScript ones. When browser code
  changes one that a page variable holds (`chart.value = 5`), the page
  re-renders anything that shows it.
- **npm values only exist in the browser.** Use them in event handlers,
  `on_mount` and other browser functions. Using one directly in markup
  is a compile error, because the server renders markup first and has
  no copy of the package. Calling one from server code raises an error
  that says so.
- **Pages load only what they use.** Each page imports just the
  packages its own code calls, so adding a charting library doesn't
  slow down pages without charts. For a large module such as
  `date-fns`, a subpath (`npm("date-fns/format")`) loads only the one
  function instead of the whole library.

If a package isn't installed, the compiler points at the `npm(...)`
line and tells you which `pyweb add` command to run.

## Elements: `ref=`

Many libraries need a DOM element to draw into. `ref={name}` sets a
page variable to the element once it exists, which is in time for
`on_mount`:

```pyweb
from pyweb import App

app = App()


@app.page("/")
def Focus():
    box = None

    def on_mount():
        box.focus()

    <input ref={box} placeholder="Focused on load" />
```

## Web components

Packages that define custom elements, such as Shoelace or Lit
components, work too. Import the module for its side effect and use the
tags in markup:

```pyweb
from pyweb import App, npm

shoelace = npm("@shoelace-style/shoelace/dist/components/button/button.js", "*")

app = App()


@app.page("/")
def Buttons():
    clicks = 0

    def on_mount():
        print("loaded", shoelace)

    def click():
        clicks += 1

    <sl-button variant="primary" onclick={click}>Clicked {clicks} times</sl-button>
```

A page imports a package only if its code uses the bound name, which is
why `on_mount` mentions `shoelace` here.

## Which packages work

Packages that ship ES modules for browsers work: most modern UI,
charting, date, maths, animation and editor libraries. `pyweb add`
explains the problem when one doesn't:

- **CommonJS only** (`module.exports`, `require()`): look for an ES
  module build, often a package with `-es` or `esm` in its name
  (`lodash-es` instead of `lodash`).
- **Node.js modules** (`fs`, `path`, `crypto`, ...): the package is
  meant for servers. Do that work in an `@server` function in Python.
- **Version conflicts**: PyWeb installs one version of each package. If
  two packages need incompatible versions of a dependency, it tells you
  which ones.

Packages are served by your app, so the default Content Security
Policy (`script-src 'self'`) still applies. The import map that tells
the browser where each package lives is allowed by its hash.

## Editor support

`pyweb lsp` reads `pyweb.lock`. Hovering a name bound with `npm()`
shows the installed version and, when the package ships TypeScript
declarations, the signature (`class Chart(item: ChartItem, config: ...)`).
Inside `npm("` it completes installed packages, and after `name.` on a
whole-module binding it completes the module's exports.

`pyweb dts FILE.d.ts` turns TypeScript declarations into Python
dataclasses, for typing data that a package hands you.

## Building and deploying

`pyweb build` copies `pyweb.lock` and `static/vendor/` into `dist/`.
With `--production` your page code is minified and hashed as usual;
vendored files are served as they are, with long-lived caching because
their URLs include the version.
