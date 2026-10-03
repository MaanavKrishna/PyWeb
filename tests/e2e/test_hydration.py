"""Hydration: the runtime adopts server-rendered DOM instead of rebuilding it."""

from playwright.sync_api import expect

from pyweb.testing import serve
from tests.e2e.conftest import ready
from tests.e2e.test_runtime_dom import open_runtime, run


def hydrate(page, html, body):
    """Put server HTML in a page root, then run ``body`` (which calls R.mount)."""
    return run(page, f"""
        root.innerHTML = {html!r};
        const host = root.firstElementChild;
        const before = [...host.querySelectorAll("*")];
        {body}
        const after = [...host.querySelectorAll("*")];
        return {{mode: host.getAttribute("data-pw-mode"), html: host.innerHTML.replace(/<!--[^>]*-->/g, ""),
                 kept: before.length > 0 && before.every((n, i) => after[i] === n), ...(window.out || {{}})}};
    """)


def test_adopts_lists_conditionals_and_text_then_stays_reactive(page):
    open_runtime(page)
    out = hydrate(page, '<div data-pw-root="P"><ul><li>a!</li><li>b!</li></ul><p>2 left</p><em>yes</em></div>', """
        const items = R.signal(["a", "b"]);
        const on = R.signal(true);
        R.mount("P", () => [
          R.h("ul", null, () => [R.list(() => items(), (x) => [R.h("li", null, () => [R.dyn(() => x), R.t("!")])])]),
          R.h("p", null, () => [R.dyn(() => items().length), R.t(" left")]),
          R.when(() => on(), () => [R.h("em", null, () => [R.t("yes")])], () => [R.h("i", null, () => [R.t("no")])]),
        ]);
        const kept = [...host.querySelectorAll("*")].every((n, i) => n === before[i]);
        items(["a", "b", "c"]);
        on(false);
        window.out = {keptAtHydration: kept};
    """)
    assert out["mode"] == "hydrated" and out["keptAtHydration"]
    assert out["html"] == "<ul><li>a!</li><li>b!</li><li>c!</li></ul><p>3 left</p><i>no</i>"


def test_mismatch_falls_back_to_client_render(page):
    open_runtime(page)
    out = hydrate(page, '<div data-pw-root="P"><p>stale</p><span>extra</span></div>', """
        R.mount("P", () => [R.h("p", null, () => [R.t("fresh")])]);
    """)
    assert out["mode"] == "rendered" and out["html"] == "<p>fresh</p>"


def test_value_typed_before_hydration_is_kept(page):
    open_runtime(page)
    out = hydrate(page, '<div data-pw-root="P"><input value=""><input type="checkbox"><b></b></div>', """
        const [text, box] = host.querySelectorAll("input");
        text.value = "typed early";
        box.checked = true;
        text.focus();
        const v = R.signal(""), c = R.signal(false);
        R.mount("P", () => [R.h("input", {$bind: v}), R.h("input", {type: "checkbox", $bind: c}),
                            R.h("b", null, () => [R.dyn(() => v() + (c() ? "+" : ""))])]);
        window.out = {value: text.value, focused: document.activeElement === text};
    """)
    assert out["mode"] == "hydrated" and out["kept"]
    assert out["value"] == "typed early" and out["focused"]
    assert out["html"].endswith("<b>typed early+</b>")


def test_real_app_keeps_typing_done_before_the_script_loads(page):
    """Delay the page module, type into the server-rendered input, then let it load."""
    def delayed(route):
        resp = route.fetch()
        gate = ("await new Promise((r) => { const t = setInterval(() => "
                "{ if (window.__go) { clearInterval(t); r(); } }, 10); });\n")
        route.fulfill(response=resp, body=gate + resp.text())

    with serve("examples/todo/app.pyweb") as url:
        page.route("**/static/Home.js*", delayed)
        page.goto(url)
        page.fill("#new", "buy milk")
        page.evaluate("window.__h1 = document.querySelector('h1')")
        page.evaluate("window.__go = true")
        ready(page)
        expect(page.locator("[data-pw-root]")).to_have_attribute("data-pw-mode", "hydrated")
        expect(page.locator("#new")).to_have_value("buy milk")
        expect(page.locator("#add")).to_be_enabled()  # state saw the typed text
        assert page.evaluate("document.querySelector('h1') === window.__h1")
        page.press("#new", "Enter")
        expect(page.locator("#list li span")).to_have_text(["buy milk"])


def test_examples_hydrate_without_falling_back(page, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(tmp_path / "blog.db"))
    warnings = []
    page.on("console", lambda m: warnings.append(m.text) if m.type == "warning" else None)
    for app, path in [("counter", "/"), ("todo", "/"), ("showcase", "/"), ("auth", "/register")]:
        with serve(f"examples/{app}/app.pyweb") as url:
            page.goto(url.rstrip("/") + path)
            ready(page)
            expect(page.locator("[data-pw-root]")).to_have_attribute("data-pw-mode", "hydrated")
    with serve("examples/blog/app.pyweb") as url:  # a server-rendered list
        page.goto(url)
        ready(page)
        page.fill("#title", "First")
        page.fill("#body", "Body")
        page.click("#publish")
        expect(page.locator("article h2")).to_have_text(["First"])
        page.reload()
        ready(page)
        expect(page.locator("[data-pw-root]")).to_have_attribute("data-pw-mode", "hydrated")
        expect(page.locator("article h2")).to_have_text(["First"])
    assert not [w for w in warnings if "did not match" in w], warnings
