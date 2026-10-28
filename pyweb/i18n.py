"""Translations: ``_("Welcome")`` and ``ngettext("{n} item", "{n} items", n)`` in Python and in markup.

::

    from pyweb import App, _, ngettext

    app = App(locales=["en", "de", "fr"])

    @app.page("/")
    def Home():
        count = 3
        <h1>{_("Welcome")}</h1>
        <p>{ngettext("{n} new message", "{n} new messages", count)}</p>

Catalogs are gettext ``.po`` files in ``locales/<lang>/messages.po`` next to the app (``pyweb i18n
extract`` finds the strings, ``pyweb i18n init de`` starts a language, ``pyweb i18n check`` lists
what's missing). No extra package is needed.

The language for a request is the ``lang`` cookie (set it with :func:`set_locale`), else the best
match in ``Accept-Language``, else the first of ``App(locales=...)``. The page's ``<html lang>``
(and ``dir="rtl"`` for Arabic, Hebrew, Persian and Urdu) follow it. In the browser, ``_`` and
``ngettext`` look up the same translations: each page gets only the strings its browser code uses.

:func:`number`, :func:`currency` and :func:`date` format values for the current language (common
languages built in) on the server.
"""

from __future__ import annotations

import ast
import contextvars
import datetime as _dt
import os
import re
import threading

RTL = {"ar", "he", "fa", "ur", "yi", "ps", "sd", "ug"}
_current: contextvars.ContextVar = contextvars.ContextVar("pyweb_locale", default=None)
_catalogs: dict = {}
_lock = threading.Lock()
_folder = {"path": None}           # the app's locales/ folder (set when an app with locales loads)


# ------------------------------------------------------------------ .po files

class Catalog:
    def __init__(self, messages=None, plural=None, nplurals=2):
        self.messages = messages or {}      # msgid -> str, or (msgid, msgid_plural) -> [forms]
        self.plural = plural or (lambda n: int(n != 1))
        self.nplurals = nplurals

    def gettext(self, msgid):
        value = self.messages.get(msgid)
        return value if isinstance(value, str) and value else msgid

    def ngettext(self, singular, plural, n):
        forms = self.messages.get((singular, plural))
        if forms:
            try:
                index = int(self.plural(int(n)))
            except (TypeError, ValueError):
                index = 0
            if 0 <= index < len(forms) and forms[index]:
                return forms[index]
        return singular if n == 1 else plural


def _unquote(chunk):
    try:
        return ast.literal_eval(chunk)
    except (ValueError, SyntaxError):
        raise ValueError(f"bad string in .po file: {chunk[:60]}") from None


def parse_po(text):
    """``(messages, header)`` from the text of a ``.po`` file. Fuzzy entries are skipped."""
    entries, cur, key, fuzzy = [], {}, None, False

    def flush():
        nonlocal cur, fuzzy
        if cur and not fuzzy:
            entries.append(cur)
        cur, fuzzy = {}, False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            key = None
            continue
        if line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
            continue
        if line.startswith("#"):
            continue
        m = re.match(r'(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(".*")$', line)
        if m:
            if m.group(1) == "msgid" and "msgid" in cur:
                flush()
            key = m.group(1)
            cur[key] = _unquote(m.group(2))
        elif line.startswith('"') and key:
            cur[key] += _unquote(line)
    flush()
    messages, header = {}, {}
    for e in entries:
        msgid = e.get("msgid", "")
        if msgid == "" and "msgstr" in e:
            for hl in e["msgstr"].split("\n"):
                k, _, v = hl.partition(":")
                if k.strip():
                    header[k.strip().lower()] = v.strip()
            continue
        if "msgctxt" in e:
            continue                                    # contexts aren't supported: keep it simple
        if "msgid_plural" in e:
            forms, i = [], 0
            while f"msgstr[{i}]" in e:
                forms.append(e[f"msgstr[{i}]"])
                i += 1
            if any(forms):
                messages[(msgid, e["msgid_plural"])] = forms
        elif e.get("msgstr"):
            messages[msgid] = e["msgstr"]
    return messages, header


_PLURAL_TOKENS = re.compile(r"\s*(?:(\d+)|(n)|(&&|\|\||==|!=|<=|>=|[<>%?:()!+\-*/]))")


def _ternary(s):
    """C's ``a ? b : c`` (right-associative, at any depth) as Python's ``(b if a else c)``."""
    s = s.strip()
    depth, q, nested = 0, -1, 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch == "?":
            if q < 0:
                q = i
            else:
                nested += 1
        elif depth == 0 and ch == ":" and q >= 0:
            if nested:
                nested -= 1
                continue
            return f"(({_ternary(s[q + 1:i])}) if ({_ternary(s[:q])}) else ({_ternary(s[i + 1:])}))"
    out, depth, start = [], 0, None
    for i, ch in enumerate(s):                   # no top-level ?: convert inside each (...) group
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                out.append("(" + _ternary(s[start + 1:i]) + ")")
                continue
        if depth == 0 and ch != ")":
            out.append(ch)
    return "".join(out)


_PLURAL_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.FloorDiv,
                 ast.Mod, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd, ast.Compare, ast.Eq, ast.NotEq, ast.Lt,
                 ast.LtE, ast.Gt, ast.GtE, ast.IfExp, ast.Name, ast.Load, ast.Constant)


def plural_function(expr):
    """A Python function for a gettext ``Plural-Forms`` expression (C syntax, ``n`` only)."""
    expr = (expr or "").strip().rstrip(";")
    pos, out = 0, []
    while pos < len(expr):
        m = _PLURAL_TOKENS.match(expr, pos)
        if not m or m.end() == pos:
            raise ValueError(f"unsupported plural expression: {expr!r}")
        num, var, op = m.groups()
        out.append(num or var or {"&&": " and ", "||": " or ", "!": " not "}.get(op, op))
        if op == "/":
            out[-1] = "//"                     # C integer division
        pos = m.end()
    code = "".join(out)
    code = _ternary(code)
    try:
        tree = ast.parse(code, "<plural>", "eval")
    except SyntaxError:
        raise ValueError(f"unsupported plural expression: {expr!r}") from None
    # Only what plural rules use: no powers or shifts (a hostile catalog could hang the server), small numbers.
    for node in ast.walk(tree):
        if not isinstance(node, _PLURAL_NODES) or (
                isinstance(node, ast.Name) and node.id != "n") or (
                isinstance(node, ast.Constant) and not (isinstance(node.value, int) and 0 <= node.value < 10 ** 6)):
            raise ValueError(f"unsupported plural expression: {expr!r}")
    compiled = compile(tree, "<plural>", "eval")
    return lambda n: int(eval(compiled, {"__builtins__": {}}, {"n": n}))  # noqa: S307 - checked above


def load(lang, folder=None):
    """The catalog for ``lang`` from ``<folder>/<lang>/messages.po`` (cached; reloaded when the file
    changes). An empty catalog when there's no file."""
    folder = folder or _folder["path"]
    if not folder:
        return Catalog()
    path = os.path.join(folder, lang, "messages.po")
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return Catalog()
    key = (path, stamp)
    found = _catalogs.get(key)
    if found is None:
        with open(path, encoding="utf-8") as fh:
            messages, header = parse_po(fh.read())
        plural, nplurals = None, 2
        m = re.search(r"nplurals\s*=\s*(\d+)\s*;\s*plural\s*=\s*([^;]+)", header.get("plural-forms", ""))
        if m:
            nplurals = int(m.group(1))
            try:
                plural = plural_function(m.group(2))
            except (ValueError, SyntaxError):
                plural = None
        found = Catalog(messages, plural, nplurals)
        with _lock:
            _catalogs[key] = found
    return found


# ------------------------------------------------------------------ the current language

def configure(app, app_dir):
    """Called when an app loads: where its catalogs are."""
    if getattr(app, "locales", None):
        _folder["path"] = os.path.join(app_dir, "locales")


def best_match(header, available):
    """The entry of ``available`` that ``Accept-Language`` prefers (or None)."""
    wanted = []
    for i, part in enumerate((header or "").split(",")):
        tag, _, q = part.strip().partition(";q=")
        try:
            weight = float(q) if q else 1.0
        except ValueError:
            weight = 0.0
        if tag and weight > 0:
            wanted.append((-weight, i, tag.strip().lower()))
    for _w, _i, tag in sorted(wanted):
        for lang in available:
            if tag == lang.lower() or tag.split("-")[0] == lang.lower().split("-")[0]:
                return lang
    return None


def begin(app, req):
    """Pick the language for ``req``; returns the token for :func:`end`."""
    locales = list(getattr(app, "locales", None) or ())
    if not locales:
        return _current.set(None)
    cookie = (req.cookies or {}).get("lang")
    lang = cookie if cookie in locales else best_match(req.headers.get("accept-language"), locales)
    return _current.set(lang or getattr(app, "default_locale", None) or locales[0])


def end(token):
    _current.reset(token)


def get_locale():
    """The current request's language (``None`` for an app without ``locales``)."""
    return _current.get()


def set_locale(lang, *, remember=True):
    """Switch the current request to ``lang``; with ``remember`` (the default) a ``lang`` cookie keeps
    it for the visitor's next pages."""
    _current.set(lang)
    if remember:
        from .context import request
        try:
            request.set_cookie("lang", lang, max_age=365 * 24 * 3600, http_only=False)
        except RuntimeError:
            pass


def direction(lang=None):
    lang = (lang or get_locale() or "").split("-")[0].lower()
    return "rtl" if lang in RTL else "ltr"


def _catalog():
    lang = get_locale()
    return load(lang) if lang else Catalog()


def gettext(message):
    """``message`` in the current language (itself when there's no translation)."""
    text = _catalog().gettext(message)
    _record(message, text)
    return text


def ngettext(singular, plural, n):
    """The singular or plural form for ``n`` in the current language, with ``{n}`` in it replaced
    by ``n``: ``ngettext("{n} item", "{n} items", count)``."""
    text = _catalog().ngettext(singular, plural, n)
    _record((singular, plural), _catalog().messages.get((singular, plural)))
    return text.replace("{n}", str(n))


_ = gettext

# Strings a render used, for the page's browser catalog.
_used: contextvars.ContextVar = contextvars.ContextVar("pyweb_i18n_used", default=None)


def _record(key, value):
    used = _used.get()
    if used is not None and value is not None:
        used[key] = value


def browser_catalog(ids):
    """``{msgid: translation, "singular\\u0000plural": [forms], "$plural": expr}`` for the strings a
    page's browser code uses (only those with a translation)."""
    cat = _catalog()
    out = {}
    for key in ids:
        if isinstance(key, (list, tuple)):
            forms = cat.messages.get(tuple(key))
            if forms:
                out["\u0000".join(key)] = forms
        else:
            value = cat.messages.get(key)
            if isinstance(value, str) and value:
                out[key] = value
    if any(isinstance(v, list) for v in out.values()):
        out["$lang"] = get_locale()
    return out


# ------------------------------------------------------------------ formatting

# decimal mark, group separator, currency pattern ("¤" is the symbol, "#" the number)
_NUMBER = {
    "en": (".", ",", "¤#"), "de": (",", ".", "# ¤"), "fr": (",", "\u202f", "# ¤"), "es": (",", ".", "# ¤"),
    "it": (",", ".", "# ¤"), "pt": (",", ".", "¤ #"), "nl": (",", ".", "¤ #"), "sv": (",", "\u00a0", "# ¤"),
    "pl": (",", "\u00a0", "# ¤"), "ru": (",", "\u00a0", "# ¤"), "ja": (".", ",", "¤#"), "zh": (".", ",", "¤#"),
    "ko": (".", ",", "¤#"), "hi": (".", ",", "¤#"), "ar": ("٫", "٬", "# ¤"), "tr": (",", ".", "¤#"),
}
_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "INR": "₹", "CNY": "¥", "KRW": "₩", "BRL": "R$",
           "CHF": "CHF", "CAD": "CA$", "AUD": "A$", "SEK": "kr", "PLN": "zł", "RUB": "₽", "TRY": "₺"}
_DATES = {
    "en": "%b %-d, %Y", "de": "%-d.%-m.%Y", "fr": "%d/%m/%Y", "es": "%-d/%-m/%Y", "it": "%d/%m/%Y",
    "pt": "%d/%m/%Y", "nl": "%-d-%-m-%Y", "ja": "%Y/%m/%d", "zh": "%Y/%m/%d", "ko": "%Y. %-m. %-d.",
}


def _base(lang):
    return (lang or get_locale() or "en").split("-")[0].lower()


def number(value, decimals=None, *, lang=None):
    """``1234.5`` as the current language writes it (``1,234.5``, ``1.234,5``, ``1 234,5``)."""
    dot, group, _pattern = _NUMBER.get(_base(lang), _NUMBER["en"])
    text = f"{value:,.{decimals}f}" if decimals is not None else f"{value:,}"
    return text.replace(",", "\x00").replace(".", dot).replace("\x00", group)


def currency(value, code="USD", *, lang=None):
    """``9.99`` in ``code`` (two decimals; none for JPY and KRW)."""
    _dot, _group, pattern = _NUMBER.get(_base(lang), _NUMBER["en"])
    decimals = 0 if code in ("JPY", "KRW") else 2
    return pattern.replace("#", number(value, decimals, lang=lang)).replace("¤", _SYMBOL.get(code, code))


def date(value, *, lang=None):
    """A date (or datetime) in the current language's short form."""
    if isinstance(value, str):
        value = _dt.date.fromisoformat(value[:10])
    fmt = _DATES.get(_base(lang), "%Y-%m-%d")
    if os.name == "nt":
        fmt = fmt.replace("%-", "%#")
    return value.strftime(fmt)


# ------------------------------------------------------------------ extraction (pyweb i18n)

_CALL = re.compile(r"""\b(?:_|gettext)\(\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')\s*\)""")
_NCALL = re.compile(r"""\bngettext\(\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')\s*,\s*"""
                    r"""("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')""")


def extract(text):
    """The translatable strings in a source file: ``[(msgid_or_pair, line)]``."""
    found = []
    for m in _CALL.finditer(text):
        found.append((_unquote(m.group(1)), text.count("\n", 0, m.start()) + 1))
    for m in _NCALL.finditer(text):
        found.append(((_unquote(m.group(1)), _unquote(m.group(2))), text.count("\n", 0, m.start()) + 1))
    return found


def _po_string(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def write_pot(entries):
    """A ``.pot`` template from ``{msgid_or_pair: ["file:line", ...]}``."""
    out = ['msgid ""', 'msgstr ""', '"Content-Type: text/plain; charset=UTF-8\\n"', ""]
    for key in sorted(entries, key=lambda k: k if isinstance(k, str) else k[0]):
        out.append("#: " + " ".join(entries[key]))
        if isinstance(key, tuple):
            out += [f"msgid {_po_string(key[0])}", f"msgid_plural {_po_string(key[1])}",
                    'msgstr[0] ""', 'msgstr[1] ""', ""]
        else:
            out += [f"msgid {_po_string(key)}", 'msgstr ""', ""]
    return "\n".join(out)


PLURAL_FORMS = {
    "en": "nplurals=2; plural=(n != 1);", "de": "nplurals=2; plural=(n != 1);",
    "es": "nplurals=2; plural=(n != 1);", "it": "nplurals=2; plural=(n != 1);",
    "nl": "nplurals=2; plural=(n != 1);", "pt": "nplurals=2; plural=(n > 1);", "fr": "nplurals=2; plural=(n > 1);",
    "ja": "nplurals=1; plural=0;", "zh": "nplurals=1; plural=0;", "ko": "nplurals=1; plural=0;",
    "ru": "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "pl": "nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    "ar": "nplurals=6; plural=(n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5);",
}


def init_po(pot_text, lang):
    """A new ``messages.po`` for ``lang`` from a template."""
    header = f'"Language: {lang}\\n"\n"Plural-Forms: {PLURAL_FORMS.get(lang.split("-")[0], PLURAL_FORMS["en"])}\\n"'
    return pot_text.replace('"Content-Type: text/plain; charset=UTF-8\\n"',
                            '"Content-Type: text/plain; charset=UTF-8\\n"\n' + header, 1)
