"""Translations: catalogs, the request's language, markup and browser code, pyweb i18n."""

import json
import subprocess
import sys

import pytest

from pyweb import i18n
from pyweb.testing import TestClient

APP = '''
from pyweb import App, _, ngettext, server
from pyweb.i18n import set_locale, currency

app = App(title="Shop", locales=["en", "de", "ar"])


@server
def use(lang: str) -> bool:
    set_locale(lang)
    return True


@app.page("/")
def Home():
    count = 1
    price = currency(9.99, "EUR")

    def more():
        count += 1

    <h1>{_("Welcome")}</h1>
    <p id="n">{ngettext("{n} item", "{n} items", count)}</p>
    <p id="price">{price}</p>
    <button onclick={more}>{_("Add one")}</button>
'''

DE = r"""msgid ""
msgstr ""
"Language: de\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

msgid "Welcome"
msgstr "Willkommen"

msgid "Add one"
msgstr "Eins mehr"

msgid "{n} item"
msgid_plural "{n} items"
msgstr[0] "{n} Artikel"
msgstr[1] "{n} Artikel (mehrere)"
"""


@pytest.fixture
def app_dir(tmp_path):
    (tmp_path / "app.pyweb").write_text(APP)
    (tmp_path / "locales" / "de").mkdir(parents=True)
    (tmp_path / "locales" / "de" / "messages.po").write_text(DE)
    return tmp_path


def test_the_request_language_picks_the_catalog(app_dir):
    c = TestClient(str(app_dir / "app.pyweb"))
    en = c.get("/")
    assert en.find("h1").text == "Welcome" and '<html lang="en"' in en.text
    de = c.request("GET", "/", headers={"Accept-Language": "de-AT,de;q=0.9,en;q=0.5"})
    assert de.find("h1").text == "Willkommen" and de.find("#n").text == "1 Artikel"
    assert de.find("#price").text == "9,99 €" and '<html lang="de"' in de.text
    strings = json.loads(de.find("#pw-i18n").children[0])
    assert strings["Welcome"] == "Willkommen" and strings["Add one"] == "Eins mehr"
    assert strings["{n} item\u0000{n} items"] == ["{n} Artikel", "{n} Artikel (mehrere)"]
    assert c.rpc("use", lang="ar") is True                       # remembered in a cookie
    ar = c.get("/")
    assert '<html lang="ar" dir="rtl"' in ar.text and ar.find("h1").text == "Welcome"   # no ar catalog yet


def test_browser_code_translates_with_the_page_strings(app_dir):
    c = TestClient(str(app_dir / "app.pyweb"))
    js = c.site.app.compiled["pages"]["Home"]["js"]
    assert 'from "./i18n.js"' in js and '$_("Add one")' in js
    assert sorted(map(str, c.site.app.compiled["pages"]["Home"]["i18n"])) == \
        sorted(["Add one", "Welcome", "['{n} item', '{n} items']"])
    assert c.get("/static/i18n.js").status == 200


def test_po_parsing_and_plurals():
    messages, header = i18n.parse_po(DE + '\n#, fuzzy\nmsgid "Draft"\nmsgstr "Entwurf"\n')
    assert messages["Welcome"] == "Willkommen" and "Draft" not in messages
    assert "nplurals=2" in header["plural-forms"]
    ru = i18n.plural_function("(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2)")
    assert [ru(n) for n in (1, 2, 5, 11, 21, 22, 111)] == [0, 1, 2, 2, 0, 1, 2]
    with pytest.raises(ValueError):
        i18n.plural_function("__import__('os').system('x')")
    assert i18n.best_match("fr-CA,fr;q=0.9,de;q=0.8", ["en", "de", "fr"]) == "fr"
    assert i18n.number(1234567.5, 2, lang="de") == "1.234.567,50"


def test_cli_extract_init_and_check(app_dir):
    run = lambda *a: subprocess.run([sys.executable, "-m", "pyweb.cli", "i18n", *a, "--app",  # noqa: E731
                                     str(app_dir / "app.pyweb")], capture_output=True, text=True)
    r = run("extract")
    assert r.returncode == 0 and "3 string(s)" in r.stdout
    pot = (app_dir / "locales" / "messages.pot").read_text()
    assert 'msgid "Welcome"' in pot and 'msgid_plural "{n} items"' in pot and "app.pyweb:" in pot
    r = run("init", "fr")
    assert r.returncode == 0
    fr = (app_dir / "locales" / "fr" / "messages.po").read_text()
    assert "plural=(n > 1)" in fr and 'msgid "Add one"' in fr
    r = run("check")
    assert "de: 3/3 translated" in r.stdout and "fr: 0/3 translated" in r.stdout
    assert run("check", "--strict").returncode == 1
