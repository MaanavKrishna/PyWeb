"""npm packages without Node: version ranges, the crawler, the lock file, compiler integration."""

import base64
import hashlib
import io
import json
import re
import tarfile
import textwrap

import pytest

from pyweb import packages as P
from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError


# --------------------------------------------------------- a fake registry

def tarball(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, text in files.items():
            data = text.encode() if isinstance(text, str) else text
            info = tarfile.TarInfo("package/" + path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeRegistry(P.Registry):
    """Serves packages from memory through the real Registry code paths."""

    def __init__(self, packages):
        super().__init__(url="https://registry.test")
        self.blobs = {}
        self.docs = {}
        for name, versions in packages.items():
            doc = {"name": name, "versions": {}, "dist-tags": {}}
            for version, files in versions.items():
                pkg = json.loads(files.get("package.json", "{}")) | {"name": name, "version": version}
                files = {**files, "package.json": json.dumps(pkg)}
                data = tarball(files)
                url = f"https://registry.test/{name}/-/{version}.tgz"
                self.blobs[url] = data
                integrity = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
                doc["versions"][version] = {**pkg, "dist": {"tarball": url, "integrity": integrity}}
                doc["dist-tags"]["latest"] = version
            self.docs[name] = doc

    def document(self, name):
        if name not in self.docs:
            raise P.PackageError(f"not found on the npm registry: {name}")
        return self.docs[name]

    def _get(self, url):
        return self.blobs[url]


WIDGET_V1 = {
    "package.json": json.dumps({"type": "module", "exports": {".": {"import": "./src/index.js", "require": "./cjs/index.cjs"},
                                                              "./extra": "./src/extra.js"},
                                "dependencies": {"tiny-color": "^2.0.0"}, "types": "./types/index.d.ts"}),
    "src/index.js": 'import { mix } from "./util";\nimport { shade } from "tiny-color";\n'
                    "export class Gauge { constructor(el, opts) { this.el = el; this.opts = opts; this.value = 0; }\n"
                    "  set(v) { this.value = v; this.el.textContent = shade(mix(v)); } }\n"
                    "export default function burst(opts) { return 'burst:' + JSON.stringify(opts); }\n"
                    'export const MODE = process.env.NODE_ENV;\n',
    "src/util.js": "export const mix = (v) => v * 2;\n",
    "src/extra.js": "export const extra = 1;\n",
    "src/unused.js": "export const nope = 1;\n",
    "cjs/index.cjs": "module.exports = {};\n",
    "types/index.d.ts": "export declare class Gauge { constructor(el: HTMLElement, opts?: object); set(v: number): void; }\n"
                        "export default function burst(opts: object): string;\n",
}
WIDGET_V2 = {**WIDGET_V1, "src/util.js": "export const mix = (v) => v * 3;\n"}
TINY = {"package.json": json.dumps({"module": "index.mjs"}),
        "index.mjs": "export function shade(v) { return 'v=' + v; }\n"}


def registry():
    return FakeRegistry({"gauge-widget": {"1.0.0": WIDGET_V1, "1.1.0": WIDGET_V2},
                         "tiny-color": {"2.0.0": TINY, "2.4.1": TINY, "3.0.0": TINY},
                         "old-cjs": {"1.0.0": {"package.json": json.dumps({"main": "index.js"}),
                                               "index.js": "module.exports = function () {};\n"}},
                         "node-only": {"1.0.0": {"package.json": json.dumps({"module": "i.js"}),
                                                 "i.js": 'import fs from "fs";\nexport default fs;\n'}}})


# ------------------------------------------------------------ semver

@pytest.mark.parametrize("version, rng, ok", [
    ("1.2.3", "^1.2.0", True), ("2.0.0", "^1.2.0", False), ("0.2.5", "^0.2.1", True), ("0.3.0", "^0.2.1", False),
    ("1.2.9", "~1.2.3", True), ("1.3.0", "~1.2.3", False), ("1.4.0", ">=1.2 <2", True), ("2.0.0", ">=1.2 <2", False),
    ("1.9.9", "1.x", True), ("1.0.0", "*", True), ("3.1.0", "^1 || ^3", True), ("2.1.0", "^1 || ^3", False),
    ("1.5.0", "1.2.0 - 1.6.0", True), ("1.7.0", "1.2.0 - 1.6.0", False), ("2.0.0-beta.1", "^2.0.0", False),
    ("1.2.3", "1.2.3", True), ("1.2.4", "1.2.3", False), ("1.3.0", ">1.2", True), ("1.2.5", ">1.2", False),
])
def test_ranges(version, rng, ok):
    assert P.satisfies(version, rng) is ok


def test_max_satisfying_prefers_highest_release():
    assert P.max_satisfying(["1.0.0", "1.4.2", "1.10.0", "2.0.0", "1.11.0-rc.1"], "^1") == "1.10.0"


def test_specs():
    assert P.split_spec("@scope/pkg@^1") == ("@scope/pkg", "^1")
    assert P.split_spec("chart.js/auto") == ("chart.js/auto", "latest")
    assert P.split_specifier("@scope/pkg/sub/x.js") == ("@scope/pkg", "sub/x.js")


def test_imports_in_comments_strings_and_regexes_are_ignored():
    src = textwrap.dedent('''
        /* import "a" */ // import "b"
        /** @example
         * import { maxTime } from "./constants/date-fns/constants";
         */
        import x from "./real";
        const s = "import 'nope'"; const t = `import "${ await import("./dyn") }" and import "c"`;
        const r = /import "d"/g; const q = a / b / c;
        export * from "dep"; export { y } from './y.js';
    ''')
    assert [spec for _s, _e, spec in P._specifiers(src)] == ["./real", "./dyn", "dep", "./y.js"]
    assert not P._is_esm("// export default 1\nmodule.exports = 1")


# ------------------------------------------------------------ install

def test_install_vendors_only_what_the_entry_imports(tmp_path):
    lock = P.install(str(tmp_path), ["gauge-widget@^1.0"], registry=registry())
    widget = lock["packages"]["gauge-widget"]
    assert widget["version"] == "1.1.0" and widget["direct"] and widget["requested"] == "^1.0"
    assert widget["files"] == ["src/index.js", "src/util.js"]  # not unused.js, not the CommonJS build
    assert widget["needs"] == ["tiny-color"]
    assert lock["packages"]["tiny-color"] == {**lock["packages"]["tiny-color"], "version": "2.4.1", "direct": False}
    assert lock["imports"] == {"gauge-widget": "/static/vendor/gauge-widget@1.1.0/src/index.js",
                               "tiny-color": "/static/vendor/tiny-color@2.4.1/index.mjs"}
    index = (tmp_path / "static/vendor/gauge-widget@1.1.0/src/index.js").read_text()
    assert 'from "./util.js"' in index          # browsers don't add extensions; we do
    assert 'process.env' not in index and '"production"' in index
    assert widget["types"]["Gauge"] == "class Gauge(el: HTMLElement, opts?: object)"


def test_subpaths_removal_and_pinning(tmp_path):
    reg = registry()
    P.install(str(tmp_path), ["gauge-widget@1.0.0"], registry=reg)
    lock = P.install(str(tmp_path), ["gauge-widget/extra"], registry=reg)
    assert lock["packages"]["gauge-widget"]["version"] == "1.0.0"   # the locked version is kept
    assert set(lock["imports"]) == {"gauge-widget", "gauge-widget/extra", "tiny-color"}
    lock = P.install(str(tmp_path), registry=reg)                      # reinstall from the lock
    assert lock["packages"]["gauge-widget"]["version"] == "1.0.0"
    lock = P.install(str(tmp_path), remove=["gauge-widget"], registry=reg)
    assert lock["packages"] == {} and lock["imports"] == {}
    assert not (tmp_path / "static/vendor/gauge-widget@1.0.0").exists()


@pytest.mark.parametrize("spec, message", [
    ("old-cjs", "is CommonJS"),
    ("node-only", "imports the Node.js module 'fs'"),
    ("missing-pkg", "not found on the npm registry"),
    ("tiny-color@^9", "no version of tiny-color matches '^9'"),
])
def test_unusable_packages_are_explained(tmp_path, spec, message):
    with pytest.raises(P.PackageError, match=re.escape(message)):
        P.install(str(tmp_path), [spec], registry=registry())
    assert not (tmp_path / "pyweb.lock").exists()


def test_checksum_mismatch_is_refused(tmp_path):
    reg = registry()
    url = reg.docs["tiny-color"]["versions"]["3.0.0"]["dist"]["tarball"]
    reg.blobs[url] = tarball({"package.json": "{}", "index.mjs": "export const evil = 1;"})
    with pytest.raises(P.PackageError, match="checksum mismatch"):
        P.install(str(tmp_path), ["tiny-color@3"], registry=reg)


def test_npm_binding_on_the_server_explains_itself():
    gauge = P.npm("gauge-widget", "Gauge")
    with pytest.raises(RuntimeError, match="only exists in the browser"):
        gauge(1)


# ----------------------------------------------------------- compiler

APP = textwrap.dedent('''
    from pyweb import App, npm

    burst = npm("gauge-widget")
    Gauge = npm("gauge-widget", "Gauge")
    W = npm("gauge-widget", "*")

    app = App()


    @app.page("/")
    def Home():
        box = None
        gauge = None
        shown = ""

        def on_mount():
            gauge = Gauge(box, max=10)

        def go():
            shown = burst(count=3)
            gauge.set(4)
            W.Gauge(box)

        <main>
            <div id="box" ref={box}></div>
            <button onclick={go}>{shown}</button>
        </main>


    @app.page("/plain")
    def Plain():
        <p>no packages here</p>
''').lstrip()


def app_with_lock(tmp_path, source=APP):
    P.install(str(tmp_path), ["gauge-widget"], registry=registry())
    path = tmp_path / "app.pyweb"
    path.write_text(source)
    return str(path)


def test_pages_import_only_the_packages_they_use(tmp_path):
    out = compile_source(open(app_with_lock(tmp_path)).read(), filename=str(tmp_path / "app.pyweb"))
    home, plain = out["pages"]["Home"], out["pages"]["Plain"]
    js = home["js"]
    assert 'import $npm_gauge_widget_default_' in js and ' from "gauge-widget";' in js
    assert 'import { Gauge as $npm_gauge_widget_Gauge_' in js and 'import * as $npm_gauge_widget___' in js
    assert '$py.call($npm_gauge_widget_Gauge_' in js and '[box()], {"max": 10})' in js
    assert '$py.call($npm_gauge_widget_default_' in js and '{"count": 3})' in js
    assert '$py.callm($npm_gauge_widget___' in js and '"Gauge", [box()])' in js
    assert '"$ref": box' in js
    assert home["importmap"] == {"gauge-widget": "/static/vendor/gauge-widget@1.1.0/src/index.js",
                                "tiny-color": "/static/vendor/tiny-color@2.4.1/index.mjs"}  # with its dependency
    assert '<script type="importmap">' in home["html"] and 'ref=' not in home["html"]
    assert plain["js"] == "" and "importmap" not in plain["html"]


def test_missing_package_is_a_compile_error_with_the_fix(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    with pytest.raises(CompileError, match=r"run `pyweb add gauge-widget`") as exc:
        compile_source(APP, filename=str(path))
    assert exc.value.lineno == 3


def test_npm_values_cannot_be_used_in_markup(tmp_path):
    src = APP.replace("<button onclick={go}>{shown}</button>", "<button onclick={go}>{burst()}</button>")
    with pytest.raises(CompileError, match="only exists in the browser") as exc:
        compile_source(src, filename=app_with_lock(tmp_path, src))
    assert exc.value.lineno == src.splitlines().index('        <button onclick={go}>{burst()}</button>') + 1


def test_npm_bindings_can_be_shared_between_files(tmp_path):
    (tmp_path / "charts.pyweb").write_text('from pyweb import npm\nGauge = npm("gauge-widget", "Gauge")\n\n'
                                           'def Meter():\n    el = None\n    def on_mount():\n        Gauge(el)\n'
                                           '    <div ref={el}></div>\n')
    src = 'from pyweb import App\nfrom charts import Meter\napp = App()\n@app.page("/")\ndef H():\n    <Meter />\n'
    out = compile_source(src, filename=app_with_lock(tmp_path, src))
    js = out["pages"]["H"]["js"]
    assert js.index("import { Gauge as $npm_gauge_widget_Gauge_") < js.index("// charts.pyweb")
    assert set(out["pages"]["H"]["importmap"]) == {"gauge-widget", "tiny-color"}


def test_ref_requires_a_page_variable():
    src = 'from pyweb import App\napp = App()\n@app.page("/")\ndef H():\n    <div ref={nothing}></div>\n'
    with pytest.raises(CompileError, match="ref=\\{nothing\\} must name a local variable"):
        compile_source(src)


def test_csp_allows_exactly_the_import_map():
    from pyweb.serve import DEFAULT_CSP, csp_for
    html = b'<head><script type="importmap">{"imports":{}}</script></head>'
    digest = base64.b64encode(hashlib.sha256(b'{"imports":{}}').digest()).decode()
    assert f"script-src 'self' 'sha256-{digest}';" in csp_for(DEFAULT_CSP, html)
    assert csp_for(DEFAULT_CSP, b"<p>no map</p>") == DEFAULT_CSP
