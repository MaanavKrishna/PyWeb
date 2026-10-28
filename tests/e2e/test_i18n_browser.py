"""Translations in the browser: the page's strings come with it; plurals follow the language."""

from pyweb.testing import serve

from .conftest import until

APP = '''
from pyweb import App, _, ngettext

app = App(locales=["en", "de"])


@app.page("/")
def Home():
    count = 1

    def more():
        count += 1

    <h1>{_("Welcome")}</h1>
    <p id="n">{ngettext("{n} item", "{n} items", count)}</p>
    <button id="more" onclick={more}>{_("Add one")}</button>
'''

DE = r"""msgid ""
msgstr ""
"Plural-Forms: nplurals=2; plural=(n != 1);\n"

msgid "Welcome"
msgstr "Willkommen"

msgid "Add one"
msgstr "Eins mehr"

msgid "{n} item"
msgid_plural "{n} items"
msgstr[0] "{n} Artikel"
msgstr[1] "{n} Artikel insgesamt"
"""


def test_german_page_and_plurals_after_a_click(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("PYWEB_ENV", "development")
    (tmp_path / "app.pyweb").write_text(APP)
    (tmp_path / "locales" / "de").mkdir(parents=True)
    (tmp_path / "locales" / "de" / "messages.po").write_text(DE)
    ctx = browser.new_context(locale="de-DE")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        with serve(str(tmp_path / "app.pyweb"), rate_limit=False) as url:
            page.goto(url + "/")
            page.wait_for_selector("[data-pw-ready]", state="attached")
            assert page.text_content("h1") == "Willkommen" and page.text_content("#more") == "Eins mehr"
            assert page.text_content("#n") == "1 Artikel"
            page.click("#more")
            until(page, "document.querySelector('#n').textContent === '2 Artikel insgesamt'")
            assert page.evaluate("document.documentElement.lang") == "de"
        assert not errors, errors
    finally:
        ctx.close()
