"""Routing: typed params, redirects, middleware, sitemap, reverse URLs."""

import pytest

from pyweb.routing import Route, Router


def test_typed_params():
    r = Route("/users/{user_id:int}")
    assert r.match("/users/42") == {"user_id": 42}
    assert r.match("/users/x") is None


def test_float_and_path():
    assert Route("/p/{v:float}").match("/p/1.5") == {"v": 1.5}
    assert Route("/f/{rest:path}").match("/f/a/b") == {"rest": "a/b"}


def test_default_str_and_multi():
    r = Route("/a/{x}/b/{y:int}")
    assert r.match("/a/hi/b/3") == {"x": "hi", "y": 3}


def test_no_match():
    assert Route("/a").match("/b") is None


def test_url_reverse():
    r = Route("/users/{user_id:int}")
    assert r.url(user_id=7) == "/users/7"
    with pytest.raises(KeyError):
        r.url()


def test_unknown_type():
    with pytest.raises(ValueError):
        Route("/x/{y:uuid}")


def test_router_resolve():
    rt = Router()
    rt.add("/a")
    rt.add("/users/{user_id:int}", name="user")
    hit = rt.resolve("/users/9")
    assert hit["params"] == {"user_id": 9} and hit["route"].name == "user"
    assert rt.resolve("/missing") is None


def test_redirects_first():
    rt = Router()
    rt.add("/new")
    rt.redirect("/old", "/new")
    assert rt.resolve("/old") == {"redirect": "/new"}


def test_middleware_and_sitemap():
    rt = Router()
    rt.use(lambda p: p)
    rt.add("/a")
    rt.add("/b/{x:int}")
    assert rt.middlewares and rt.sitemap("https://x") == ["https://x/a", "https://x/b/{x:int}"]
