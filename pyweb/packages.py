"""npm packages for browser code, without Node.js.

``pyweb add chart.js`` downloads the package from the npm registry,
verifies its checksum, and copies into ``static/vendor/`` only the
JavaScript files its browser entry point actually imports (following
relative imports and the package's own dependencies). ``pyweb.lock``
records versions, checksums and the *import map* that lets browser
modules ``import ... from "chart.js"``.

In ``.pyweb`` code a package is bound at module level::

    Chart = npm("chart.js", "Chart")          # a named export
    confetti = npm("canvas-confetti")          # the default export
    L = npm("leaflet", "*")                    # the whole module

Packages must ship ES modules (most do). CommonJS-only packages and ones
that need Node built-ins are rejected with an explanation.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import posixpath
import re
import tarfile
import urllib.parse
import urllib.request

LOCK_NAME = "pyweb.lock"
VENDOR_DIR = os.path.join("static", "vendor")
VENDOR_URL = "/static/vendor"
NODE_BUILTINS = {"assert", "buffer", "child_process", "cluster", "crypto", "dgram", "dns", "events", "fs", "http",
                 "http2", "https", "module", "net", "os", "path", "perf_hooks", "process", "querystring",
                 "readline", "stream", "string_decoder", "timers", "tls", "tty", "url", "util", "v8", "vm",
                 "worker_threads", "zlib"}
EXTENSIONS = (".js", ".mjs")
MAX_FILES = 2000


class PackageError(Exception):
    """A package can't be installed or used; the message says why and what to do."""


# ------------------------------------------------------------ npm() binding

class NpmBinding:
    """What ``npm(...)`` evaluates to on the server: a placeholder.

    The compiler turns it into an ES ``import`` for browser code; using it
    in server code is an error with an explanation.
    """

    def __init__(self, package, export="default"):
        self.package, self.export = package, export

    def _fail(self, *_a, **_k):
        raise RuntimeError(f'npm("{self.package}") only exists in the browser: use it in event handlers or '
                           "on_mount, not in server code or markup")

    __call__ = _fail

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self._fail()

    def __repr__(self):
        return f"npm({self.package!r}, {self.export!r})"


def npm(package: str, export: str = "default") -> NpmBinding:
    """Bind an npm package export for browser code (see module docs)."""
    return NpmBinding(package, export)


# ------------------------------------------------------------------ semver

_VER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def parse_version(v):
    m = _VER.match(v.strip())
    if not m:
        return None
    pre = tuple(int(p) if p.isdigit() else p for p in m.group(4).split(".")) if m.group(4) else None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), pre


def _key(v):
    major, minor, patch, pre = v
    if pre is None:
        return (major, minor, patch, 1, ())
    return (major, minor, patch, 0, tuple((0, p, "") if isinstance(p, int) else (1, 0, p) for p in pre))


def _partial(s):
    """``1``, ``1.2``, ``1.2.x``, ``*`` -> list of ints (missing parts omitted)."""
    s = s.strip().lstrip("v=")
    if s in ("", "*", "x", "X"):
        return []
    out = []
    for part in s.split("-")[0].split("."):
        if part in ("x", "X", "*"):
            break
        out.append(int(part))
    return out


def _comparators(token):
    """One comparator token -> list of (op, (maj, min, pat)) bounds."""
    m = re.match(r"^(\^|~|>=|<=|>|<|=)?\s*(.*)$", token)
    op, ver = m.group(1) or "", m.group(2)
    parts = _partial(ver)
    full = tuple(parts + [0] * (3 - len(parts)))
    if not parts:
        return [] if op in ("", "=", ">=", "^", "~") else [("<", (0, 0, 0))]
    if op == "^":
        if parts[0] > 0 or len(parts) == 1:
            upper = (parts[0] + 1, 0, 0)
        elif len(parts) == 2 or parts[1] > 0:
            upper = (0, parts[1] + 1, 0)
        else:
            upper = (0, 0, parts[2] + 1)
        return [(">=", full), ("<", upper)]
    if op == "~":
        upper = (parts[0] + 1, 0, 0) if len(parts) == 1 else (parts[0], parts[1] + 1, 0)
        return [(">=", full), ("<", upper)]
    if op in ("", "="):
        if len(parts) == 3:
            return [("=", full)]
        upper = (parts[0] + 1, 0, 0) if len(parts) == 1 else (parts[0], parts[1] + 1, 0)
        return [(">=", full), ("<", upper)]
    if op == ">" and len(parts) < 3:
        return [(">=", (parts[0] + 1, 0, 0) if len(parts) == 1 else (parts[0], parts[1] + 1, 0))]
    if op == "<=" and len(parts) < 3:
        return [("<", (parts[0] + 1, 0, 0) if len(parts) == 1 else (parts[0], parts[1] + 1, 0))]
    return [(op, full)]


def _alternatives(rng):
    rng = (rng or "*").strip()
    out = []
    for alt in rng.split("||"):
        alt = alt.strip()
        m = re.match(r"^(\S+)\s+-\s+(\S+)$", alt)
        if m:  # hyphen range
            lo = _comparators(">=" + m.group(1))
            hi_parts = _partial(m.group(2))
            hi = _comparators("<=" + m.group(2)) if len(hi_parts) == 3 else _comparators("<=" + m.group(2))
            out.append(lo + hi)
            continue
        alt = re.sub(r"(>=|<=|>|<|=|\^|~)\s+", r"\1", alt)
        bounds = []
        for token in alt.split():
            bounds += _comparators(token)
        out.append(bounds)
    return out


def satisfies(version, rng):
    v = parse_version(version)
    if v is None:
        return False
    if v[3] is not None and "-" not in (rng or ""):
        return False  # prereleases only when asked for
    base = v[:3]
    for bounds in _alternatives(rng):
        ok = True
        for op, b in bounds:
            if op == "=":
                ok = base == b and (v[3] is None or "-" in rng)
            elif op == ">=":
                ok = base >= b
            elif op == ">":
                ok = base > b
            elif op == "<":
                ok = base < b or False
            elif op == "<=":
                ok = base <= b
            if not ok:
                break
        if ok:
            return True
    return False


def max_satisfying(versions, rng):
    good = [v for v in versions if satisfies(v, rng)]
    return max(good, key=lambda s: _key(parse_version(s))) if good else None


# ------------------------------------------------------------------ registry

class Registry:
    """Read-only npm registry client (``PYWEB_NPM_REGISTRY`` overrides the URL)."""

    def __init__(self, url=None, timeout=30):
        self.url = (url or os.environ.get("PYWEB_NPM_REGISTRY") or "https://registry.npmjs.org").rstrip("/")
        self.timeout = timeout
        self._docs = {}

    def _get(self, url):
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "pyweb"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - registry URL
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise PackageError(f"not found on the npm registry: {url}") from None
            raise PackageError(f"npm registry error {exc.code} for {url}") from None
        except OSError as exc:
            raise PackageError(f"can't reach the npm registry ({exc}); check your connection") from None

    def document(self, name):
        if name not in self._docs:
            quoted = urllib.parse.quote(name, safe="@")
            self._docs[name] = json.loads(self._get(f"{self.url}/{quoted.replace('/', '%2f')}"))
        return self._docs[name]

    def resolve(self, name, rng):
        doc = self.document(name)
        rng = (rng or "latest").strip()
        tags = doc.get("dist-tags", {})
        version = tags[rng] if rng in tags else max_satisfying(list(doc.get("versions", {})), rng)
        if not version:
            raise PackageError(f"no version of {name} matches {rng!r} (latest is {tags.get('latest')})")
        return version, doc["versions"][version]

    def tarball(self, meta):
        dist = meta.get("dist", {})
        data = self._get(dist["tarball"])
        integrity = dist.get("integrity", "")
        if integrity.startswith("sha512-"):
            got = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
            if got != integrity:
                raise PackageError(f"checksum mismatch for {meta['name']}@{meta['version']}; refusing to install")
        elif dist.get("shasum"):
            if hashlib.sha1(data).hexdigest() != dist["shasum"]:  # noqa: S324 - npm's legacy checksum
                raise PackageError(f"checksum mismatch for {meta['name']}@{meta['version']}; refusing to install")
        files = {}
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                path = member.name.split("/", 1)[1] if "/" in member.name else member.name
                path = posixpath.normpath(path)
                if path.startswith(("..", "/")):
                    continue
                files[path] = tar.extractfile(member).read()
        return files, integrity or ("sha1-" + dist.get("shasum", ""))


# ------------------------------------------------------------- resolution

def split_spec(spec):
    """``"@scope/name@^1"`` -> ("@scope/name", "^1"); ``"name"`` -> ("name", "latest")."""
    if spec.startswith("@"):
        name, _, rng = spec[1:].partition("@")
        return "@" + name, rng or "latest"
    name, _, rng = spec.partition("@")
    return name, rng or "latest"


def split_specifier(spec):
    """``"@a/b/c.js"`` -> ("@a/b", "c.js"); ``"lodash-es/debounce"`` -> ("lodash-es", "debounce")."""
    parts = spec.split("/")
    if spec.startswith("@"):
        return "/".join(parts[:2]), "/".join(parts[2:])
    return parts[0], "/".join(parts[1:])


_CONDITIONS = ("browser", "import", "module", "default")


def _condition(target):
    if isinstance(target, str):
        return target
    if isinstance(target, list):
        for t in target:
            got = _condition(t)
            if got:
                return got
        return None
    if isinstance(target, dict):
        for cond in _CONDITIONS:
            if cond in target:
                got = _condition(target[cond])
                if got:
                    return got
    return None


def entry_for(pkg, subpath, files):
    """The file (inside the package) that ``import "pkg/subpath"`` loads in a browser."""
    key = "./" + subpath if subpath else "."
    exports = pkg.get("exports")
    if exports is not None:
        table = exports if isinstance(exports, dict) and any(k.startswith(".") for k in exports) else {".": exports}
        target = table.get(key)
        if target is None:
            for pattern, value in table.items():
                if "*" in pattern:
                    pre, _, post = pattern.partition("*")
                    if key.startswith(pre) and key.endswith(post):
                        star = key[len(pre):len(key) - len(post) if post else None]
                        target = json.loads(json.dumps(value).replace("*", star))
                        break
        resolved = _condition(target) if target is not None else None
        if resolved:
            return _existing(posixpath.normpath(resolved), files)
        if exports and not subpath:
            raise PackageError(f"{pkg['name']} has no browser/ES module entry in its \"exports\"")
    if not subpath:
        for field in ("module", "browser", "main"):
            value = pkg.get(field)
            if isinstance(value, str):
                found = _existing(posixpath.normpath(value), files)
                if found:
                    return found
        return _existing("index", files)
    return _existing(posixpath.normpath(subpath), files)


def _existing(path, files):
    if path.startswith("./"):
        path = path[2:]
    for cand in (path, *(path + e for e in EXTENSIONS), *(path + "/index" + e for e in EXTENSIONS)):
        if cand in files:
            return cand
    return None


_IMPORT_RES = [
    re.compile(r"""\bimport\s*(?:[\w$*\s{},]*?\bfrom\s*)?(["'])([^"'\n]+)\1"""),
    re.compile(r"""\bexport\s*(?:\*\s*(?:as\s+[\w$]+\s*)?|\{[^}]*\}\s*)from\s*(["'])([^"'\n]+)\1"""),
    re.compile(r"""\bimport\s*\(\s*(["'])([^"'\n]+)\1\s*\)"""),
]
# After these, a "/" starts a regular expression rather than a division.
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw",
                   "yield", "await", "instanceof"}


def _code_only(source):
    """``source`` with comments, string/template contents and regex literals blanked (same length).

    String delimiters are kept, so ``import "x"`` still matches and the
    specifier is read from the original text at the same offsets. Code
    inside template ``${...}`` stays visible.
    """
    out = list(source)
    n, i = len(source), 0
    braces = []          # for each open "${": the brace depth to return to the template at
    depth = 0

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    def template(i):     # i is just after a backtick or a closing "}" of ${...}; returns (index, entered_expr)
        j = i
        while j < n:
            c = source[j]
            if c == "\\":
                j += 2
            elif c == "`":
                blank(i, j)
                return j + 1, False
            elif c == "$" and source.startswith("${", j):
                blank(i, j)
                return j + 2, True
            else:
                j += 1
        blank(i, n)
        return n, False

    while i < n:
        c = source[i]
        if c == "/" and source.startswith("//", i):
            j = source.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif c == "/" and source.startswith("/*", i):
            j = source.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and source[j] != c and source[j] != "\n":
                j += 2 if source[j] == "\\" else 1
            blank(i + 1, min(j, n))
            i = j + 1
        elif c == "`":
            i, entered = template(i + 1)
            if entered:
                braces.append(depth)
                depth += 1
        elif c == "{":
            depth += 1
            i += 1
        elif c == "}":
            depth -= 1
            if braces and braces[-1] == depth:
                braces.pop()
                i, entered = template(i + 1)
                if entered:
                    braces.append(depth)
                    depth += 1
            else:
                i += 1
        elif c == "/":
            k = i - 1
            while k >= 0 and source[k] in " \t\r\n":
                k -= 1
            prev = source[k] if k >= 0 else ""
            word = re.search(r"[A-Za-z_$][\w$]*$", source[max(0, k - 11):k + 1]) if prev else None
            if not prev or prev in _REGEX_AFTER or (word and word.group(0) in _REGEX_KEYWORDS):
                j, in_class = i + 1, False
                while j < n and source[j] != "\n":
                    ch = source[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    j += 1
                blank(i + 1, min(j, n))
                i = j + 1
            else:
                i += 1
        else:
            i += 1
    return "".join(out)


def _specifiers(source):
    """``(start, end, specifier)`` for each import/export-from in real code (not comments or strings)."""
    code = _code_only(source)
    out = []
    for rx in _IMPORT_RES:
        for m in rx.finditer(code):
            out.append((m.start(2), m.end(2), source[m.start(2):m.end(2)]))
    return sorted(set(out))


def _is_esm(source):
    return bool(re.search(r"(^|[\s;}])(export\s|export\{|export\*|import\s*[\w{*\"'])", _code_only(source)))


class _Install:
    def __init__(self, registry, pinned=None):
        self.registry = registry
        self.pinned = pinned or {}   # name -> version from the existing lock
        self.packages = {}   # name -> dict(version, integrity, files{path: text}, meta, deps, ranges)
        self.imports = {}    # bare specifier -> url
        self.order = []

    def package(self, name, rng, wanted_by=None):
        known = self.packages.get(name)
        if known:
            is_range = bool(re.fullmatch(r"[\s\d^~<>=.xX*|v-]+", rng or ""))  # else a dist-tag like "latest"
            if is_range and not satisfies(known["version"], rng):
                raise PackageError(f"{wanted_by or 'your app'} needs {name}@{rng}, but {name}@{known['version']} "
                                   "is already installed; PyWeb installs one version of each package")
            return known
        pinned = self.pinned.get(name)
        is_range = bool(re.fullmatch(r"[\s\d^~<>=.xX*|v-]+", rng or ""))
        if pinned and (not is_range or satisfies(pinned, rng)):
            version, meta = pinned, self.registry.document(name)["versions"][pinned]
        else:
            version, meta = self.registry.resolve(name, rng)
        files, integrity = self.registry.tarball(meta)
        pkg = json.loads(files.get("package.json", b"{}"))
        pkg.setdefault("name", name)
        deps = {}
        for field in ("optionalDependencies", "peerDependencies", "dependencies"):
            deps.update(pkg.get(field) or {})
        known = {"name": name, "version": version, "integrity": integrity, "raw": files, "pkg": pkg,
                 "deps": deps, "out": {}, "requested": rng}
        self.packages[name] = known
        self.order.append(name)
        return known

    def url(self, pkg, path):
        return f"{VENDOR_URL}/{pkg['name']}@{pkg['version']}/{path}"

    def specifier(self, spec, wanted_by):
        """Install whatever ``import "spec"`` needs and map it in the import map."""
        if spec in self.imports:
            return
        name, sub = split_specifier(spec)
        if name.startswith("node:") or name in NODE_BUILTINS:
            raise PackageError(f"{wanted_by} imports the Node.js module {name!r}, so it can't run in a browser")
        owner = self.packages.get(wanted_by) if wanted_by else None
        if owner and name != wanted_by:
            rng = owner["deps"].get(name)
            if rng is None:
                raise PackageError(f"{wanted_by} imports {name!r} but doesn't list it as a dependency")
        else:
            rng = self.packages[name]["requested"] if name in self.packages else "latest"
        pkg = self.package(name, rng, wanted_by)
        entry = entry_for(pkg["pkg"], sub, pkg["raw"])
        if not entry:
            raise PackageError(f"can't find the file for {spec!r} in {name}@{pkg['version']}")
        self.imports[spec] = self.url(pkg, entry)
        self.crawl(pkg, entry)

    def crawl(self, pkg, path):
        stack = [path]
        while stack:
            current = stack.pop()
            if current in pkg["out"]:
                continue
            if len(pkg["out"]) > MAX_FILES:
                raise PackageError(f"{pkg['name']} imports more than {MAX_FILES} files; it's too big to vendor")
            text = pkg["raw"][current].decode("utf-8", "replace")
            if not _is_esm(text):
                if re.search(r"\bmodule\.exports\b|\brequire\s*\(", text):
                    raise PackageError(f"{pkg['name']}@{pkg['version']} is CommonJS ({current}); PyWeb needs a package "
                                       "that ships ES modules. Look for an \"-es\" or \"esm\" variant of it")
            pieces, last = [], 0
            for start, end, spec in _specifiers(text):
                if spec.startswith((".", "/")):
                    target = _existing(posixpath.normpath(posixpath.join(posixpath.dirname(current), spec)), pkg["raw"])
                    if not target:
                        raise PackageError(f"{pkg['name']}: {current} imports missing file {spec!r}")
                    rel = posixpath.relpath(target, posixpath.dirname(current) or ".")
                    rel = rel if rel.startswith(".") else "./" + rel
                    pieces.append(text[last:start] + rel)
                    last = end
                    stack.append(target)
                elif not spec.startswith(("http:", "https:", "data:")):
                    self.specifier(spec, pkg["name"])
                    pkg.setdefault("needs", set()).add(spec)
            pieces.append(text[last:])
            text = "".join(pieces)
            # What bundlers do: browsers have no `process`.
            text = re.sub(r"\bprocess\.env\.NODE_ENV\b", '"production"', text)
            pkg["out"][current] = text


def types_summary(pkg, files, limit=200):
    """Exported names -> one-line declarations, from the package's .d.ts (best effort)."""
    start = pkg.get("types") or pkg.get("typings")
    if not start:
        exports = pkg.get("exports")
        dot = exports.get(".") if isinstance(exports, dict) else None
        if isinstance(dot, dict):
            t = dot.get("types")
            start = t if isinstance(t, str) else (t or {}).get("default") if isinstance(t, dict) else None
    if not start:
        return {}
    out, seen, stack = {}, set(), [posixpath.normpath(start)]
    while stack and len(seen) < 25:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        cand = path if path in files else next((c for c in (path + ".d.ts", path.replace(".js", ".d.ts"),
                                                              path + "/index.d.ts") if c in files), None)
        if not cand:
            continue
        text = files[cand].decode("utf-8", "replace")
        for m in re.finditer(r"^export\s+(?:declare\s+)?(?:default\s+)?(function|class|const|let|interface|type)\s+"
                             r"([A-Za-z_$][\w$]*)([^\n{;=]*)", text, re.M):
            rest = m.group(3).rstrip()
            if rest.count("<") > rest.count(">"):  # generics cut short by a default (`= T`): drop them
                rest = rest[:rest.index("<")]
            decl = f"{m.group(1)} {m.group(2)}{rest}"
            if m.group(1) == "class":  # show how to construct it
                body = text[m.end():m.end() + 4000].split("\n}", 1)[0]
                ctor = re.search(r"\bconstructor\s*\(([^)]*)\)", body)
                if ctor:
                    decl += f"({' '.join(ctor.group(1).split())})"
            out.setdefault(m.group(2), decl[:160])
        for m in re.finditer(r"""^export\s+\*\s+from\s+["']([^"']+)["']""", text, re.M):
            stack.append(posixpath.normpath(posixpath.join(posixpath.dirname(cand), m.group(1))))
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------- lockfile

def lock_path(app_dir):
    return os.path.join(app_dir, LOCK_NAME)


def read_lock(app_dir):
    path = lock_path(app_dir)
    if not os.path.isfile(path):
        return {"lockfileVersion": 1, "packages": {}, "imports": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def find_lock(start):
    """The nearest pyweb.lock at or above ``start`` (a directory), or None."""
    cur = os.path.abspath(start)
    for _ in range(6):
        if os.path.isfile(os.path.join(cur, LOCK_NAME)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def install(app_dir, specs=(), *, registry=None, remove=()):
    """Add ``specs`` (``"name@range"``), drop ``remove``, re-resolve everything; returns the new lock."""
    registry = registry or Registry()
    lock = read_lock(app_dir)
    direct, entries = {}, {}
    for n, p in lock.get("packages", {}).items():
        if p.get("direct"):
            direct[n] = p["requested"]
            entries[n] = list(p.get("entries", [""]))
    for name in remove:
        name = split_specifier(name)[0]
        if name not in direct:
            raise PackageError(f"{name} isn't installed (installed: {', '.join(sorted(direct)) or 'nothing'})")
        direct.pop(name)
        entries.pop(name)
    for spec in specs:
        target, rng = split_spec(spec)          # "chart.js/auto@^4" -> "chart.js/auto", "^4"
        name, sub = split_specifier(target)
        direct[name] = rng if rng != "latest" or name not in direct else direct[name]
        entries.setdefault(name, [])
        if sub not in entries[name]:
            entries[name].append(sub)
    new_specs = {split_specifier(split_spec(s)[0])[0] for s in specs}
    pinned = {n: p["version"] for n, p in lock.get("packages", {}).items() if n not in new_specs}
    job = _Install(registry, pinned)
    for name, rng in direct.items():
        job.package(name, rng)
        for sub in entries[name]:
            job.specifier(f"{name}/{sub}" if sub else name, None)
    # Resolve "latest" requests to a caret range so reinstalls are stable.
    for name in direct:
        if direct[name] in ("latest", "*", ""):
            direct[name] = "^" + job.packages[name]["version"]
    vendor = os.path.join(app_dir, VENDOR_DIR)
    keep = set()
    packages = {}
    for name in job.order:
        pkg = job.packages[name]
        folder = f"{name}@{pkg['version']}"
        keep.add(folder.split("/")[0] if name.startswith("@") else folder)
        for rel, text in pkg["out"].items():
            target = os.path.join(vendor, folder, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(text)
        packages[name] = {"version": pkg["version"], "requested": direct.get(name, pkg["requested"]),
                          "direct": name in direct, **({"entries": entries[name]} if name in direct else {}),
                          "integrity": pkg["integrity"], "needs": sorted(pkg.get("needs", ())),
                          "files": sorted(pkg["out"]), "types": types_summary(pkg["pkg"], pkg["raw"])}
    if os.path.isdir(vendor):  # drop versions nothing uses any more
        import shutil
        for entry in os.listdir(vendor):
            path = os.path.join(vendor, entry)
            if entry.startswith("@"):
                for sub in os.listdir(path):
                    if f"{entry}/{sub}" not in {f"{n}@{p['version']}" for n, p in packages.items()}:
                        shutil.rmtree(os.path.join(path, sub))
                if not os.listdir(path):
                    os.rmdir(path)
            elif entry not in keep:
                shutil.rmtree(path)
    new = {"lockfileVersion": 1, "packages": dict(sorted(packages.items())),
           "imports": dict(sorted(job.imports.items()))}
    with open(lock_path(app_dir), "w", encoding="utf-8") as fh:
        json.dump(new, fh, indent=2)
        fh.write("\n")
    return new


def page_imports(lock, specs):
    """The import-map entries ``specs`` need: themselves plus every bare import inside the packages they load."""
    imports, packages = lock.get("imports", {}), lock.get("packages", {})
    out, stack = {}, list(specs)
    while stack:
        spec = stack.pop()
        if spec in out or spec not in imports:
            continue
        out[spec] = imports[spec]
        stack.extend(packages.get(split_specifier(spec)[0], {}).get("needs", ()))
    return dict(sorted(out.items()))


def importmap_json(imports):
    """The ``<script type="importmap">`` body (safe inside HTML)."""
    raw = json.dumps({"imports": imports}, separators=(",", ":"), sort_keys=True)
    return raw.replace("<", "\\u003c")
