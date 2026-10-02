"""DOM behaviour of the runtime in a real browser."""

from pathlib import Path

RUNTIME = (Path(__file__).parent.parent.parent / "pyweb" / "runtime" / "browser" / "runtime.js").read_text()


def open_runtime(page):
    def handler(route):
        url = route.request.url
        if url.endswith("/runtime.js"):
            route.fulfill(body=RUNTIME, content_type="text/javascript")
        else:
            route.fulfill(body="<!doctype html><div id=root></div>", content_type="text/html")
    page.route("http://pw.test/**", handler)
    page.goto("http://pw.test/index.html")


def run(page, body):
    return page.evaluate(f"""async () => {{
        const R = await import("http://pw.test/runtime.js");
        const root = document.getElementById("root");
        {body}
    }}""")


def test_keyed_list_keeps_existing_row_nodes(page):
    open_runtime(page)
    out = run(page, """
        const items = R.signal(["a", "b"]);
        root.append(R.list(() => items(), (x) => [R.h("li", null, [x])]));
        const firstB = root.querySelectorAll("li")[1];
        items(["z", "a", "b", "c"]);
        const lis = [...root.querySelectorAll("li")];
        return {texts: lis.map(l => l.textContent), sameNode: lis[2] === firstB};
    """)
    assert out == {"texts": ["z", "a", "b", "c"], "sameNode": True}


def test_text_binding_updates_single_node(page):
    open_runtime(page)
    out = run(page, """
        const n = R.signal(1);
        const el = R.h("p", null, ["n=", () => n()]);
        root.append(el);
        const textNode = [...el.childNodes].find(c => c.nodeType === 3 && c.data === "1");
        n(2);
        return {html: el.textContent, same: textNode.data === "2"};
    """)
    assert out == {"html": "n=2", "same": True}


def test_when_swaps_and_disposes_branch(page):
    open_runtime(page)
    out = run(page, """
        const on = R.signal(true), v = R.signal(0); let runs = 0;
        root.append(R.when(() => on(), () => [R.h("b", null, [() => { runs++; return v(); }])], () => [R.h("i", null, ["off"])]));
        v(1);
        on(false);
        v(2); v(3);   // the disposed branch must not re-run
        return {html: root.innerHTML.replace(/<!--[^>]*-->/g, ""), runs};
    """)
    assert out == {"html": "<i>off</i>", "runs": 2}


def test_javascript_urls_are_blocked(page):
    open_runtime(page)
    out = run(page, """
        const a = R.h("a", {href: "javascript:alert(1)"}, ["x"]);
        const b = R.h("a", {href: " JaVaScRiPt:alert(1)"}, ["x"]);
        const c = R.h("a", {href: "/ok"}, ["x"]);
        return [a.getAttribute("href"), b.getAttribute("href"), c.getAttribute("href")];
    """)
    assert out == ["#", "#", "/ok"]


def test_bind_updates_signal_before_input_handlers(page):
    open_runtime(page)
    out = run(page, """
        const q = R.signal(""); const seen = [];
        const el = R.h("input", {"$bind": q, oninput: () => seen.push(q())});
        root.append(el);
        el.value = "hi"; el.dispatchEvent(new Event("input"));
        return seen;
    """)
    assert out == ["hi"]
