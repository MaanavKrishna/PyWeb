"""<Image>: srcset on the server and in the browser, and /__pyweb/img/<w>/... resizing."""

import io
import re

import pytest

from pyweb import images
from pyweb.hosting import Site

APP = '''
from pyweb import App
from pyweb.ui import Image

app = App()

@app.page("/")
def Home():
    <Image src="/static/cat.jpg" alt="A cat" width={400} height={300} />
    <Image src="/static/logo.svg" alt="Logo" width={64} height={64} priority={True} />
    <Image src="https://cdn.example.com/x.png" alt="Remote" />
'''


@pytest.fixture
def site(tmp_path):
    PIL = pytest.importorskip("PIL.Image")
    (tmp_path / "static").mkdir()
    im = PIL.new("RGB", (1600, 1200), (200, 30, 30))
    exif = im.getexif()
    exif[0x010F] = "SecretCam"                       # camera make: must not survive resizing
    im.save(tmp_path / "static" / "cat.jpg", "JPEG", exif=exif)
    (tmp_path / "static" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (tmp_path / "app.pyweb").write_text(APP)
    images._cache.clear()
    return Site(str(tmp_path / "app.pyweb"), debug=False, rate_limit=False)


def test_markup(site):
    _, _, body = site.respond("GET", "/", {})
    tags = re.findall(r"<img[^>]*>", body.decode())
    assert 'srcset="/__pyweb/img/320/cat.jpg 320w, /__pyweb/img/640/cat.jpg 640w, /__pyweb/img/960/cat.jpg 960w"' in tags[0]
    assert 'sizes="(max-width: 400px) 100vw, 400px"' in tags[0] and 'loading="lazy"' in tags[0]
    assert 'width="400" height="300"' in tags[0]
    assert 'loading="eager"' in tags[1] and 'fetchpriority="high"' in tags[1]
    assert "srcset" not in tags[2]


def test_resized_without_metadata(site):
    from PIL import Image
    status, headers, body = site.respond("GET", "/__pyweb/img/640/cat.jpg", {})
    assert status == 200 and dict(headers)["Content-Type"] == "image/jpeg"
    im = Image.open(io.BytesIO(body))
    assert im.size == (640, 480) and 0x010F not in im.getexif()
    etag = dict(headers)["ETag"]
    assert etag.endswith('-640"')
    status, _, body = site.respond("GET", "/__pyweb/img/640/cat.jpg", {"If-None-Match": etag})
    assert status == 304 and body == b""
    _, _, big = site.respond("GET", "/__pyweb/img/2560/cat.jpg", {})       # never made larger
    assert Image.open(io.BytesIO(big)).size == (1600, 1200)


def test_only_known_widths_and_static_files(site):
    assert site.respond("GET", "/__pyweb/img/641/cat.jpg", {})[0] == 404
    assert site.respond("GET", "/__pyweb/img/640/missing.jpg", {})[0] == 404
    assert site.respond("GET", "/__pyweb/img/640/../app.pyweb", {})[0] in (403, 404)
    status, headers, body = site.respond("GET", "/__pyweb/img/320/logo.svg", {})     # not resizable: as it is
    assert status == 200 and body.startswith(b"<svg")


def test_without_pillow_the_original_is_sent(site, monkeypatch):
    monkeypatch.setattr(images, "_pillow", lambda: None)
    status, _, body = site.respond("GET", "/__pyweb/img/640/cat.jpg", {})
    assert status == 200 and body == site.respond("GET", "/static/cat.jpg", {})[2]
