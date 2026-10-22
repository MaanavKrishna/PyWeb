"""Fuzzing the request parsers: whatever a client sends, the answer is a 2xx/4xx, never a crash or a 500."""

import json
import string

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pyweb import forms
from pyweb.hosting import Site

APP = '''
from pyweb import App, Field, Form, Input, Submit, server
from typing import Literal, Optional
import dataclasses

app = App()


@dataclasses.dataclass
class Point:
    x: int
    y: int = 0


@server
def echo(text: str, n: int = 0, tags: list[str] = [], opt: Optional[float] = None,
         mode: Literal["a", "b"] = "a", point: Optional[Point] = None, extra: dict[str, int] = {}) -> str:
    return text


@server
def anything(value=None):
    return value


@server
def signup(name: str = Field(min=2, max=40), email: str = Field(format="email"), age: int = Field(0, min=0)):
    return {"ok": True}


@app.page("/")
def Home():
    <Form action={signup}>
        <Input name="name" />
        <Input name="email" />
        <Input name="age" />
        <Submit>Go</Submit>
    </Form>
'''

FUZZ = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow,
                                                                       HealthCheck.function_scoped_fixture])


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    path = tmp_path_factory.mktemp("fuzz") / "app.pyweb"
    path.write_text(APP)
    return Site(str(path), rate_limit=False)


JSON = st.recursive(
    st.none() | st.booleans() | st.integers(-2**70, 2**70) | st.floats(allow_nan=False) |
    st.text(max_size=30),
    lambda kids: st.lists(kids, max_size=5) | st.dictionaries(st.text(max_size=10), kids, max_size=5),
    max_leaves=40)
OK = set(range(200, 300)) | set(range(400, 500))


def post(site, path, body, ctype, accept="application/json"):
    status, _, _ = site.respond("POST", path, {"Content-Type": ctype, "Accept": accept, "Host": "localhost"}, body)
    return status


@FUZZ
@given(name=st.sampled_from(["echo", "anything", "signup", "nope", "__init__"]),
       args=st.dictionaries(st.sampled_from(["text", "n", "tags", "opt", "mode", "point", "extra", "value",
                                             "name", "email", "age", "zzz"]), JSON, max_size=6))
def test_rpc_arguments(site, name, args):
    assert post(site, f"/__pyweb/rpc/{name}", json.dumps({"args": args}).encode(), "application/json") in OK


@FUZZ
@given(body=st.binary(max_size=300) | st.text(max_size=300).map(str.encode) | JSON.map(lambda v: json.dumps(v).encode()),
       ctype=st.sampled_from(["application/json", "application/json; charset=utf-8", "text/plain", "",
                              "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"]))
def test_rpc_raw_bodies(site, body, ctype):
    assert post(site, "/__pyweb/rpc/echo", body, ctype) in OK


def _multipart(draw_parts, boundary):
    out = b""
    for head, data in draw_parts:
        out += b"--" + boundary + b"\r\n" + head + b"\r\n\r\n" + data + b"\r\n"
    return out + b"--" + boundary + b"--\r\n"


PART_HEAD = st.one_of(
    st.builds(lambda n: f'Content-Disposition: form-data; name="{n}"'.encode(),
              st.sampled_from(["name", "email", "age", "__pw_csrf", "__pw_form", "x", ""])),
    st.builds(lambda n, f: f'Content-Disposition: form-data; name="{n}"; filename="{f}"\r\nContent-Type: image/png'.encode(),
              st.text(string.ascii_letters, max_size=8), st.text(max_size=12)),
    st.binary(max_size=60))


@FUZZ
@given(parts=st.lists(st.tuples(PART_HEAD, st.binary(max_size=80)), max_size=8),
       boundary=st.text(string.ascii_letters + string.digits + "-'()+_,./:=?", min_size=1, max_size=40).map(str.encode),
       accept=st.sampled_from(["application/json", "text/html"]))
def test_multipart_forms(site, parts, boundary, accept):
    body = _multipart(parts, boundary)
    ctype = f"multipart/form-data; boundary={boundary.decode()}"
    assert post(site, "/__pyweb/form/signup", body, ctype, accept) in OK


@FUZZ
@given(body=st.binary(max_size=400) | st.lists(st.tuples(st.text(max_size=10), st.text(max_size=20)), max_size=20).map(
           lambda kv: "&".join(f"{k}={v}" for k, v in kv).encode()),
       accept=st.sampled_from(["application/json", "text/html"]))
def test_urlencoded_forms(site, body, accept):
    assert post(site, "/__pyweb/form/signup", body, "application/x-www-form-urlencoded", accept) in OK


@FUZZ
@given(body=st.binary(max_size=500), ctype=st.text(max_size=80))
def test_parse_body_only_raises_value_error(body, ctype):
    class Req:
        headers = {"Content-Type": ctype}
    Req.body = body
    try:
        fields, files = forms.parse_body(Req)
    except ValueError:
        return
    assert sum(map(len, fields.values())) <= forms.MAX_FIELDS


def test_too_many_fields_are_refused(site):
    many = "&".join(f"f{i}=1" for i in range(forms.MAX_FIELDS + 1)).encode()
    assert post(site, "/__pyweb/form/signup", many, "application/x-www-form-urlencoded") == 400
    parts = [(b'Content-Disposition: form-data; name="f"', b"1")] * (forms.MAX_FIELDS + 5)
    assert post(site, "/__pyweb/form/signup", _multipart(parts, b"b"), "multipart/form-data; boundary=b") == 400
