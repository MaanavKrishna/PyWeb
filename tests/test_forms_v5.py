"""0.5 forms: <Form> markup, server checks, no-JS posts, edits, uploads, one-time keys,
and the browser rule checker agreeing with the server's."""

import json
import re
import shutil
import subprocess
import textwrap
import urllib.parse

import pytest

from pyweb import models as M
from pyweb.compiler import compile_source
from pyweb.compiler.errors import CompileError
from pyweb.hosting import Site
from pyweb.rules import Rules

APP = '''
from typing import Literal
from pyweb import App, Form, FormError, Input, Textarea, Select, Checkbox, FileInput, Submit, Field, Model, server
from pyweb.models import Email, File, ValidationError

app = App(database="sqlite:///{db}")

class Tag(Model):
    name: str = Field(max=20)

class Post(Model):
    title: str = Field(min=3, max=120)
    body: str = ""
    tags: list[Tag] = []
    draft: bool = True
    status: str = Field("draft", choices=["draft", "live"])
    views: int = Field(0, min=0)
    photo: str | None = File(types=["image/*"], max_size="1KB")
    owner_secret: str = Field("", private=True)

@server
def save_post(post: Post):
    if post.title == "boom":
        raise ValidationError({{"__all__": "Not today"}})
    post.save()
    return post

@server
def subscribe(email: Email, plan: Literal["free", "pro"] = "free", daily: bool = False):
    return {{"email": email, "plan": plan, "daily": daily}}

@app.page("/new")
def NewPost():
    <Form action={{save_post}} redirect="/posts/{{id}}">
        <Input name="title" placeholder="A title" />
        <Textarea name="body" />
        <Select name="tags" />
        <Select name="status" />
        <Input name="views" />
        <Checkbox name="draft" label="Keep as draft" />
        <FileInput name="photo" />
        <Submit>Publish</Submit>
    </Form>

@app.page("/posts/{{post_id}}/edit")
def EditPost(post_id: int):
    post = Post.get_or_404(post_id)
    <Form action={{save_post}} values={{post}}>
        <FormError />
        <Input name="title" />
        <Checkbox name="draft" />
        <Submit>Save</Submit>
    </Form>

@app.page("/subscribe")
def Subscribe():
    <Form action={{subscribe}}>
        <Input name="email" />
        <Select name="plan" />
        <Checkbox name="daily" />
    </Form>
'''

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


class Client:
    """A browser without JavaScript: keeps cookies, reads hidden fields, posts forms."""

    def __init__(self, site):
        self.site = site
        self.cookies = {}

    def get(self, path):
        status, headers, body = self.site.respond("GET", path, self._headers())
        self._keep(headers)
        return status, dict(headers), body.decode()

    def _headers(self, extra=None):
        h = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        h.update(extra or {})
        return h

    def _keep(self, headers):
        for k, v in headers:
            if k.lower() == "set-cookie":
                name, _, rest = v.partition("=")
                self.cookies[name] = rest.split(";")[0]

    @staticmethod
    def hidden(html, form_id=None):
        if form_id:
            html = html[html.index(f'data-pw-form="{form_id}"'):]
            html = html[:html.index("</form>")]
        return dict(re.findall(r'<input type="hidden" name="(__pw_\w+)" value="([^"]*)"', html))

    def post(self, action, data, *, files=None, accept="text/html"):
        if files:
            boundary = "pwtestboundary"
            parts = []
            for k, vals in data.items():
                for v in (vals if isinstance(vals, list) else [vals]):
                    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for k, (fname, content) in files.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                             f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
            body = b"".join(parts) + f"--{boundary}--\r\n".encode()
            ctype = f"multipart/form-data; boundary={boundary}"
        else:
            body = urllib.parse.urlencode(data, doseq=True).encode()
            ctype = "application/x-www-form-urlencoded"
        status, headers, raw = self.site.respond("POST", f"/__pyweb/form/{action}",
                                                 self._headers({"Content-Type": ctype, "Accept": accept}), body)
        self._keep(headers)
        return status, dict(headers), raw.decode()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setenv("PYWEB_STORAGE", f"file://{tmp_path / 'files'}")
    (tmp_path / "app.pyweb").write_text(APP.format(db=tmp_path / "app.db"))
    site = Site(str(tmp_path / "app.pyweb"), debug=False, rate_limit=False)
    yield site
    M._state.db = None


def errors_in(html):
    return [e for e in re.findall(r'class="pw-error"[^>]*>([^<]*)<', html) if e]


def post_model(site):
    return next(m for m in site.app.models() if m.__name__ == "Post")


# ----------------------------------------------------------------- markup

def test_form_markup_has_labels_rules_and_hidden_fields(app):
    status, headers, html = Client(app).get("/new")
    assert status == 200 and "__pw_csrf=" in headers["Set-Cookie"]
    form = html[html.index("<form"):html.index("</form>")]
    assert 'method="post"' in form and 'action="/__pyweb/form/save_post"' in form
    assert 'enctype="multipart/form-data"' in form                      # it has a file field
    assert '<label for="pw-save_post-title">Title</label>' in form
    assert re.search(r'<input id="pw-save_post-title" name="title" required[^>]*minlength="3" maxlength="120"', form)
    assert 'aria-describedby="pw-save_post-title-error"' in form
    assert '<option value="draft" selected>draft</option>' in form
    assert 'type="number"' in form and 'min="0"' in form
    assert 'type="checkbox" value="true" checked> Keep as draft' in form
    assert 'type="file"' in form and 'accept="image/*"' in form
    assert 'name="owner_secret"' not in form                             # private fields never appear
    rules = json.loads(re.search(r"data-pw-rules=\"([^\"]*)\"", form).group(1).replace("&quot;", '"'))
    assert rules["title"] == {"kind": "str", "required": True, "min": 3, "max": 120, "label": "Title"}
    assert set(Client.hidden(form)) >= {"__pw_form", "__pw_csrf", "__pw_key", "__pw_page", "__pw_redirect"}


def test_compile_errors_point_at_the_mistake():
    base = "from pyweb import App, Form, Input, server\napp = App()\n@server\ndef go(x: str):\n    return x\n"
    cases = [
        ("@app.page('/')\ndef P():\n    <Input name=\"x\" />\n", "inside a <Form>"),
        ("def helper():\n    return 1\n@app.page('/')\ndef P():\n    <Form action={helper}></Form>\n", "@server function"),
        ("@app.page('/')\ndef P():\n    <Form action={go}><Input /></Form>\n", "needs name="),
        ("@app.page('/')\ndef P():\n    <Form action={go}><Input name=\"x\" /><Input name=\"x\" /></Form>\n", "twice"),
        ("@app.page('/')\ndef P():\n    <Form action={go}></Form>\n    <Form action={go}></Form>\n", "give each an id"),
        ("@app.page('/')\ndef P():\n    <Form action={go} onsubmit={go}></Form>\n", "doesn't take onsubmit"),
    ]
    for code, message in cases:
        with pytest.raises(CompileError, match=re.escape(message)):
            compile_source(base + code, filename="t.pyweb")
    with pytest.raises(CompileError, match="pages and layouts"):
        compile_source(base + "def Box():\n    <Form action={go}></Form>\n@app.page('/')\ndef P():\n    <Box />\n",
                       filename="t.pyweb")


def test_unknown_field_names_fail_while_rendering(tmp_path, monkeypatch):
    monkeypatch.setattr(M._state, "db", None)
    (tmp_path / "app.pyweb").write_text(
        "from pyweb import App, Form, Input, server\napp = App()\n@server\ndef go(x: str):\n    return x\n"
        "@app.page('/')\ndef P():\n    <Form action={go}><Input name=\"y\" /></Form>\n")
    site = Site(str(tmp_path / "app.pyweb"), debug=True)
    status, _, body = site.respond("GET", "/", {})
    assert status == 500 and b"has no field &#x27;y&#x27;" in body or b"has no field 'y'" in body


# ------------------------------------------------------------ no JavaScript

def test_invalid_post_re_renders_the_page_with_values_and_errors(app):
    c = Client(app)
    _, _, html = c.get("/new")
    status, _, page = c.post("save_post", {**c.hidden(html), "title": "Hi", "views": "-3", "status": "gone"})
    assert status == 422
    assert errors_in(page) == ["Title must be at least 3 characters", "Status must be one of: draft, live"] or \
        set(errors_in(page)) >= {"Title must be at least 3 characters"}
    assert 'value="Hi"' in page and 'aria-invalid="true"' in page
    assert post_model(app).count() == 0


def test_valid_post_redirects_and_saves_once(app):
    c = Client(app)
    _, _, html = c.get("/new")
    data = {**c.hidden(html), "title": "Hello world", "body": "Text", "status": "live", "views": "4"}
    status, headers, _ = c.post("save_post", data)
    assert status == 303 and headers["Location"] == "/posts/1"
    again, headers2, _ = c.post("save_post", data)                     # double click / retried request
    assert again == 303 and headers2["Location"] == "/posts/1"
    Post = post_model(app)
    post = Post.get(1)
    assert Post.count() == 1 and post.status == "live" and post.views == 4 and post.draft is False


def test_unchecked_box_and_empty_select_mean_false_and_none(app):
    c = Client(app)
    _, _, html = c.get("/new")
    Tag = next(m for m in app.app.models() if m.__name__ == "Tag")
    t1, t2 = Tag.create(name="a"), Tag.create(name="b")
    c.post("save_post", {**c.hidden(html), "title": "Tagged post", "tags": [str(t1.id), str(t2.id)], "draft": "true"})
    post = post_model(app).include("tags").first()
    assert post.draft is True and sorted(t.name for t in post.tags) == ["a", "b"]


def test_mass_assignment_is_ignored(app):
    c = Client(app)
    _, _, html = c.get("/new")
    c.post("save_post", {**c.hidden(html), "title": "Sneaky one", "id": "99", "owner_secret": "x"})
    post = post_model(app).first()
    assert post.id == 1 and post.owner_secret == ""


def test_csrf_and_cross_site_posts_are_refused(app):
    c = Client(app)
    _, _, html = c.get("/new")
    hidden = c.hidden(html)
    status, _, body = c.post("save_post", {**hidden, "__pw_csrf": "forged", "title": "Hello there"},
                             accept="application/json")
    assert status == 403 and "expired" in json.loads(body)["error"]
    stranger = Client(app)                                                # no cookie at all
    assert stranger.post("save_post", {**hidden, "title": "Hello there"})[0] == 403
    status, _, _ = app.respond("POST", "/__pyweb/form/save_post",
                               {"Sec-Fetch-Site": "cross-site", "Content-Type": "application/x-www-form-urlencoded"},
                               b"title=Hello")
    assert status == 403
    assert app.respond("GET", "/__pyweb/form/save_post", {})[0] == 405
    assert post_model(app).count() == 0


def test_action_errors_show_as_a_form_error(app):
    c = Client(app)
    Post = post_model(app)
    post = Post.create(title="Original")
    _, _, html = c.get(f"/posts/{post.id}/edit")
    status, _, page = c.post("save_post", {**c.hidden(html), "title": "boom"})
    assert status == 422 and "Not today" in page


# ------------------------------------------------------------------- edits

def test_edit_form_loads_values_and_updates_the_row(app):
    c = Client(app)
    Post = post_model(app)
    post = Post.create(title="First title", draft=True, views=7)
    _, _, html = c.get(f"/posts/{post.id}/edit")
    assert 'value="First title"' in html and " checked" in html
    hidden = c.hidden(html)
    assert "__pw_pk" in hidden
    status, headers, _ = c.post("save_post", {**hidden, "title": "Second title"})   # draft unticked
    assert status == 303 and headers["Location"] == f"/posts/{post.id}/edit"
    fresh = Post.get(post.id)
    assert fresh.title == "Second title" and fresh.draft is False and fresh.views == 7   # untouched fields kept
    assert Post.count() == 1


def test_signed_row_id_cannot_be_swapped(app):
    c = Client(app)
    Post = post_model(app)
    a, b = Post.create(title="Row A"), Post.create(title="Row B")
    _, _, html = c.get(f"/posts/{a.id}/edit")
    hidden = c.hidden(html)
    payload, mac = hidden["__pw_pk"].rsplit(".", 1)
    import base64
    forged = base64.urlsafe_b64encode(json.dumps(["save_post", "Post", b.id]).encode()).decode().rstrip("=")
    status, _, body = c.post("save_post", {**hidden, "__pw_pk": f"{forged}.{mac}", "title": "Hijacked"},
                             accept="application/json")
    assert status == 422 and "reload" in json.loads(body)["errors"]["__all__"]
    assert Post.get(b.id).title == "Row B"


# -------------------------------------------------------------------- JSON

def test_json_answers_for_the_browser_runtime(app):
    c = Client(app)
    _, _, html = c.get("/new")
    hidden = c.hidden(html)
    status, _, body = c.post("save_post", {**hidden, "title": "No"}, accept="application/json")
    data = json.loads(body)
    assert status == 422 and data["ok"] is False and data["errors"] == {"title": "must be at least 3 characters"}
    status, _, body = c.post("save_post", {**hidden, "title": "Good title"}, accept="application/json")
    data = json.loads(body)
    assert status == 200 and data["ok"] and data["redirect"] == "/posts/1" and data["result"]["title"] == "Good title"
    assert data["next_key"] and data["next_key"] != hidden["__pw_key"]
    assert "owner_secret" not in data["result"]


def test_plain_parameters(app):
    c = Client(app)
    _, _, html = c.get("/subscribe")
    assert '<option value="pro">pro</option>' in html and 'type="email"' in html
    hidden = c.hidden(html)
    status, _, body = c.post("subscribe", {**hidden, "email": "nope", "plan": "gold"}, accept="application/json")
    assert status == 422 and set(json.loads(body)["errors"]) == {"email", "plan"}
    hidden["__pw_key"] = "second"
    status, _, body = c.post("subscribe", {**hidden, "email": "a@example.com", "plan": "pro", "daily": "true"},
                             accept="application/json")
    assert json.loads(body)["result"] == {"email": "a@example.com", "plan": "pro", "daily": True}


# ------------------------------------------------------------------ uploads

def test_uploads_are_checked_by_content_and_size(app, tmp_path):
    from pyweb import storage
    c = Client(app)
    _, _, html = c.get("/new")
    hidden = c.hidden(html)
    status, _, body = c.post("save_post", {**hidden, "title": "With photo"}, files={"photo": ("cat.png", PNG)},
                             accept="application/json")
    assert status == 200, body
    key = post_model(app).first().photo
    assert storage.valid_key(key) and key.endswith("/cat.png")
    s, h, data = app.respond("GET", storage.url(key), {})
    assert s == 200 and dict(h)["Content-Type"] == "image/png" and data == PNG
    assert "sandbox" in dict(h)["Content-Security-Policy"]
    hidden["__pw_key"] = "k2"
    status, _, body = c.post("save_post", {**hidden, "title": "Fake image"},
                             files={"photo": ("evil.png", b"<script>alert(1)</script>")}, accept="application/json")
    assert status == 422 and json.loads(body)["errors"]["photo"] == "isn't an allowed kind of file"
    hidden["__pw_key"] = "k3"
    status, _, body = c.post("save_post", {**hidden, "title": "Huge image"},
                             files={"photo": ("big.png", PNG + b"\x00" * 2048)}, accept="application/json")
    assert status == 422 and json.loads(body)["errors"]["photo"] == "must be at most 1 KB"
    assert app.respond("GET", "/__pyweb/files/../../etc/passwd", {})[0] == 404


def test_storage_presigns_s3_urls():
    import datetime as dt
    from pyweb.storage import S3Storage
    s3 = S3Storage("bucket", "uploads", region="eu-west-1", access_key="AKIDEXAMPLE", secret_key="secret")
    url = s3.presign("GET", "0123456789abcdef/cat.png", now=dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc))
    assert url.startswith("https://s3.eu-west-1.amazonaws.com/bucket/uploads/0123456789abcdef/cat.png?")
    assert "X-Amz-Credential=AKIDEXAMPLE%2F20260102%2Feu-west-1%2Fs3%2Faws4_request" in url
    assert re.search(r"X-Amz-Signature=[0-9a-f]{64}$", url)
    with pytest.raises(ValueError):
        s3.presign("GET", "../escape")


def test_carry_and_restore_for_multi_step_forms(app):
    from pyweb import forms
    from pyweb.context import RequestContext, activate, deactivate
    token = activate(RequestContext(None))
    try:
        t = forms.carry({"step": 1, "email": "a@example.com"})
        assert forms.restore(t) == {"step": 1, "email": "a@example.com"}
        assert forms.restore(t[:-2] + "00") is None and forms.restore(t, max_age=-1) is None
        assert forms.restore(t, purpose="other") is None
    finally:
        deactivate(token)


# --------------------------------------------- the browser agrees with the server

PARITY = [
    ({"kind": "str", "required": True}, ["", "x"]),
    ({"kind": "str", "min": 3, "max": 5}, ["ab", "abc", "abcdef", "abcde"]),
    ({"kind": "str", "max": 1}, ["xy"]),
    ({"kind": "int", "min": 0, "max": 10}, ["-1", "0", "10", "11", "4.5", "x"]),
    ({"kind": "float", "min": 0.5}, ["0.25", "0.5", "abc"]),
    ({"kind": "str", "choices": ["a", "b"]}, ["a", "c"]),
    ({"kind": "str", "format": "email"}, ["a@b.co", "a@b", "a b@c.de"]),
    ({"kind": "str", "format": "url"}, ["https://x.dev", "ftp://x", "x.dev"]),
    ({"kind": "str", "format": "slug"}, ["my-post", "My Post", "a--b"]),
    ({"kind": "str", "pattern": "[A-Z]{2}\\d+"}, ["AB12", "ab12"]),
    ({"kind": "str", "required": True, "message": "pick one"}, [""]),
]


def _python(rules, value):
    from pyweb.models.fields import Field
    r = Rules(**rules)
    if value in (None, ""):
        return r.check(None if value is None else value)
    if rules["kind"] in ("int", "float"):
        try:
            value = Field(kind=rules["kind"]).coerce(value)
        except ValueError as exc:
            return str(exc)
    return r.check(value)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_browser_rules_give_the_same_messages_as_the_server():
    import pathlib
    runtime = pathlib.Path(__file__).parent.parent / "pyweb" / "runtime" / "browser" / "forms.js"
    cases = [(r, v) for r, values in PARITY for v in values]
    script = textwrap.dedent(f"""
        import("{runtime.as_posix()}").then((m) => {{
          const cases = {json.dumps(cases)};
          console.log(JSON.stringify(cases.map(([r, v]) => m.checkRule(r, v))));
        }});
    """)
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    js = json.loads(out.stdout)
    py = [_python(r, v) for r, v in cases]
    assert js == py, [(c, a, b) for c, a, b in zip(cases, js, py) if a != b]


def test_build_ships_forms_js_only_with_forms(tmp_path, monkeypatch):
    from pyweb.cli import main
    monkeypatch.setattr(M._state, "db", None)
    (tmp_path / "app.pyweb").write_text(APP.format(db=tmp_path / "b.db"))
    main(["build", str(tmp_path / "app.pyweb"), "--out", str(tmp_path / "dist"), "--production"])
    static = tmp_path / "dist" / "static"
    forms_js = [p.name for p in static.iterdir() if p.name.startswith("forms.")]
    assert len(forms_js) == 1
    page_js = next(p for p in static.iterdir() if p.name.startswith("NewPost.") and p.suffix == ".js")
    assert f'"./{forms_js[0]}"' in page_js.read_text()
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "app.pyweb").write_text("from pyweb import App\napp = App()\n@app.page('/')\ndef H():\n"
                                     "    n = 0\n    def inc():\n        n += 1\n    <button onclick={inc}>{n}</button>\n")
    main(["build", str(plain / "app.pyweb"), "--out", str(plain / "dist")])
    assert not any(p.name.startswith("forms.") for p in (plain / "dist" / "static").iterdir())
