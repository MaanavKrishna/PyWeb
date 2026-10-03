"""The <Markdown> component: safe Markdown rendering on the server (the browser runs the same rules)."""

import pytest

from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError
from pyweb.markdown import render

# (markdown, expected html). tests/e2e/test_ai_browser.py checks the browser renders all of them identically.
CASES = [
    ("# Title\n\nSome **bold**, *it*, `co*de*` and ~~gone~~.",
     "<h1>Title</h1><p>Some <strong>bold</strong>, <em>it</em>, <code>co*de*</code> and <del>gone</del>.</p>"),
    ("line one  \nline two\\\nthree", "<p>line one<br>line two<br>three</p>"),
    ("- a\n- b\n  - nested *x*\n- c", "<ul><li>a</li><li>b<ul><li>nested <em>x</em></li></ul></li><li>c</li></ul>"),
    ("3. three\n4. four", '<ol start="3"><li>three</li><li>four</li></ol>'),
    ("- one\n\n  more text\n- two", "<ul><li><p>one</p><p>more text</p></li><li>two</li></ul>"),
    ("```python\nprint('<hi>')\n```\nafter",
     '<pre><code class="language-python">print(&#39;&lt;hi&gt;&#39;)</code></pre><p>after</p>'),
    ("```\nunclosed fence while streaming", "<pre><code>unclosed fence while streaming</code></pre>"),
    ("> quote\n> more\n\n---", "<blockquote><p>quote\nmore</p></blockquote><hr>"),
    ("| a | b |\n|---|:-:|\n| 1 | **2** |\n| 3 |",
     "<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td>1</td><td><strong>2</strong></td></tr>"
     "<tr><td>3</td><td></td></tr></tbody></table>"),
    ("[docs](https://x.test/a_b_c) and [w](https://en.wikipedia.org/wiki/Python_(language))",
     '<p><a href="https://x.test/a_b_c" rel="noopener noreferrer">docs</a> and '
     '<a href="https://en.wikipedia.org/wiki/Python_(language)" rel="noopener noreferrer">w</a></p>'),
    ("![cat](/static/cat.png) <https://ok.test/x>",
     '<p><img src="/static/cat.png" alt="cat"> <a href="https://ok.test/x" rel="noopener noreferrer">'
     "https://ok.test/x</a></p>"),
    ("snake_case_name and 2 * 3 * 4 and __strong__", "<p>snake_case_name and 2 * 3 * 4 and <strong>strong</strong></p>"),
    ("## Heading ##\n#nospace", "<h2>Heading</h2><p>#nospace</p>"),
    ("", ""),
]

# Unsafe input and what it must turn into.
ATTACKS = [
    ("<script>alert(1)</script>", "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"),
    ("[x](javascript:alert(1))", "<p>x</p>"),
    ("[x](JaVaScRiPt:alert(1))", "<p>x</p>"),
    ("![i](data:image/svg+xml,<svg onload=alert(1)>)",
     "<p>![i](data:image/svg+xml,&lt;svg onload=alert(1)&gt;)</p>"),
    ("![i](data:image/png;base64,AAAA)", "<p>i</p>"),
    ('[x](https://ok.test/"onmouseover="alert(1))', '<p><a href="https://ok.test/&quot;onmouseover=&quot;alert(1)" '
                                                     'rel="noopener noreferrer">x</a></p>'),
    ("<img src=x onerror=alert(1)>", "<p>&lt;img src=x onerror=alert(1)&gt;</p>"),
    ("`</code><script>`", "<p><code>&lt;/code&gt;&lt;script&gt;</code></p>"),
    ("a\x000\x00b", "<p>a0b</p>"),
]


@pytest.mark.parametrize("text, html", CASES + ATTACKS)
def test_render(text, html):
    assert render(text) == html


def test_markdown_component_renders_on_the_server_and_updates_in_the_browser():
    src = ("from pyweb import App, Markdown\napp = App()\n@app.page('/')\ndef H():\n    reply = '**hi** <b>'\n"
           "    def more():\n        reply += ' *more*'\n"
           "    <Markdown text={reply} class=\"chat\" />\n    <Markdown text=\"- a\" />\n"
           "    <button onclick={more}>m</button>\n")
    page = compile_source(src)["pages"]["H"]
    assert page["html_body"].startswith('<div class="markdown chat"><p><strong>hi</strong> &lt;b&gt;</p></div>'
                                        '<div class="markdown"><ul><li>a</li></ul></div>')
    assert '$markdown(() => reply(), {"class": "chat"})' in page["js"] and '$markdown("- a", null)' in page["js"]


@pytest.mark.parametrize("markup, message", [
    ("<Markdown />", "needs text="),
    ("<Markdown text={x}>hi</Markdown>", "takes its text as text={...}"),
    ("<Markdown text={x} onclick={f} />", "doesn't take onclick="),
])
def test_markdown_mistakes(markup, message):
    src = (f"from pyweb import App, Markdown\napp = App()\n@app.page('/')\ndef H():\n    x = ''\n"
           f"    def f():\n        pass\n    {markup}\n")
    with pytest.raises(CompileError, match=message):
        compile_source(src)


def test_markdown_from_python():
    from pyweb import Markdown
    assert Markdown("*x*") == '<div class="markdown"><p><em>x</em></p></div>'
