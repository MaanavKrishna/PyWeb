"""File storage for uploads: a local folder, or S3-compatible object storage.

``PYWEB_STORAGE`` chooses where files go:

* ``file://./uploads`` (the default): a folder; files are served from
  ``/__pyweb/files/<key>``.
* ``s3://bucket/prefix`` with ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``,
  ``AWS_REGION`` (and ``S3_ENDPOINT`` for R2, MinIO, Spaces...). Uploads and
  downloads use presigned URLs (AWS Signature V4, no SDK needed), so large
  files can go straight from the browser to the bucket.

Keys are random (``ab12cd34ef56/photo.jpg``): knowing a key is what grants
access, like a private link.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import re
import secrets
import urllib.parse
import urllib.request

from pyweb.uploads import safe_filename

_KEY = re.compile(r"^[a-f0-9]{16}/[A-Za-z0-9._-]{1,120}$")


def new_key(filename):
    return f"{secrets.token_hex(8)}/{safe_filename(filename)}"


def valid_key(key):
    return bool(_KEY.match(key or ""))


class LocalStorage:
    """Files in a folder on this server (single-server apps, development)."""

    def __init__(self, root="uploads", url_prefix="/__pyweb/files/"):
        self.root = os.path.abspath(root)
        self.url_prefix = url_prefix

    def _path(self, key):
        if not valid_key(key):
            raise ValueError("invalid file key")
        path = os.path.abspath(os.path.join(self.root, key))
        if not path.startswith(self.root + os.sep):
            raise ValueError("invalid file key")
        return path

    def save(self, data, filename, content_type=None):
        key = new_key(filename)
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".part", "wb") as fh:
            fh.write(data)
        os.replace(path + ".part", path)
        return key

    def open(self, key):
        with open(self._path(key), "rb") as fh:
            return fh.read()

    def exists(self, key):
        try:
            return os.path.isfile(self._path(key))
        except ValueError:
            return False

    def delete(self, key):
        try:
            os.remove(self._path(key))
        except (FileNotFoundError, ValueError):
            pass

    def url(self, key):
        return self.url_prefix + urllib.parse.quote(key)

    def keys(self):
        for d in sorted(os.listdir(self.root)) if os.path.isdir(self.root) else ():
            sub = os.path.join(self.root, d)
            if os.path.isdir(sub):
                for name in sorted(os.listdir(sub)):
                    if not name.endswith(".part"):
                        yield f"{d}/{name}"


class S3Storage:
    """An S3-compatible bucket, signed with AWS Signature V4."""

    def __init__(self, bucket, prefix="", *, region=None, endpoint=None, access_key=None, secret_key=None,
                 expires=900):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region or os.environ.get("AWS_REGION") or "us-east-1"
        self.endpoint = (endpoint or os.environ.get("S3_ENDPOINT") or f"https://s3.{self.region}.amazonaws.com").rstrip("/")
        self.access_key = access_key or os.environ.get("AWS_ACCESS_KEY_ID", "")
        self.secret_key = secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
        self.expires = expires

    def _object(self, key):
        if not valid_key(key):
            raise ValueError("invalid file key")
        return f"{self.prefix}/{key}" if self.prefix else key

    def presign(self, method, key, *, expires=None, now=None, content_type=None):
        """A URL that allows ``method`` (GET/PUT) on ``key`` for ``expires`` seconds."""
        now = now or _dt.datetime.now(_dt.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        day = now.strftime("%Y%m%d")
        host = urllib.parse.urlparse(self.endpoint).netloc
        path = "/" + urllib.parse.quote(f"{self.bucket}/{self._object(key)}")
        scope = f"{day}/{self.region}/s3/aws4_request"
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires or self.expires),
            "X-Amz-SignedHeaders": "host",
        }
        query = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
                         for k, v in sorted(params.items()))
        canonical = "\n".join([method, path, query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        k = ("AWS4" + self.secret_key).encode()
        for part in (day, self.region, "s3", "aws4_request"):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        return f"{self.endpoint}{path}?{query}&X-Amz-Signature={signature}"

    def save(self, data, filename, content_type=None):
        key = new_key(filename)
        req = urllib.request.Request(self.presign("PUT", key), data=data, method="PUT",
                                     headers={"Content-Type": content_type or "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - our own signed URL
            resp.read()
        return key

    def open(self, key):
        with urllib.request.urlopen(self.presign("GET", key), timeout=60) as resp:  # noqa: S310
            return resp.read()

    def delete(self, key):
        req = urllib.request.Request(self.presign("DELETE", key), method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                resp.read()
        except Exception:  # noqa: BLE001 - already gone
            pass

    def exists(self, key):
        req = urllib.request.Request(self.presign("HEAD", key), method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=30):  # noqa: S310
                return True
        except Exception:  # noqa: BLE001
            return False

    def url(self, key):
        return self.presign("GET", key)

    def upload_url(self, filename):
        """``(key, url)``: the browser PUTs the file to ``url`` itself (big files never touch the app)."""
        key = new_key(filename)
        return key, self.presign("PUT", key)


_current = None


def storage():
    """The storage chosen by ``PYWEB_STORAGE``."""
    global _current
    spec = os.environ.get("PYWEB_STORAGE", "file://./uploads")
    if _current is not None and getattr(_current, "_spec", None) == spec:
        return _current
    if spec.startswith("s3://"):
        rest = spec[5:]
        bucket, _, prefix = rest.partition("/")
        found = S3Storage(bucket, prefix)
    else:
        root = spec[7:] if spec.startswith("file://") else spec
        found = LocalStorage(root)
    found._spec = spec
    _current = found
    return found


def url(key):
    """The URL a page can use for a stored file."""
    return storage().url(key) if key else None


# --------------------------------------------------------------- file types

_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x1a\x45\xdf\xa3", "video/webm"),
    (b"OggS", "audio/ogg"),
    (b"ID3", "audio/mpeg"),
    (b"fLaC", "audio/flac"),
)


def sniff(data):
    """The real type of a file from its first bytes (what it claims doesn't matter)."""
    head = data[:64]
    for magic, kind in _SIGNATURES:
        if head.startswith(magic):
            return kind
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "image/avif"
        if brand in (b"heic", b"heix", b"mif1"):
            return "image/heic"
        return "video/mp4"
    try:
        text = data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return "application/octet-stream"
    if "\x00" in text:
        return "application/octet-stream"
    stripped = text.lstrip().lower()
    if stripped.startswith(("<svg", "<?xml", "<!doctype html", "<html", "<script")):
        return "text/html"             # never served as an image: scripts could run
    return "text/plain"


def type_allowed(kind, patterns):
    if not patterns:
        return kind != "text/html"
    for p in patterns:
        if p == kind or (p.endswith("/*") and kind.startswith(p[:-1])):
            return True
    return False


def parse_size(value):
    """``"5MB"`` / ``"300KB"`` / ``1048576`` -> bytes."""
    if value is None or isinstance(value, int):
        return value
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(b|kb|mb|gb)?\s*", str(value).lower())
    if not m:
        raise ValueError(f"not a size: {value!r} (use e.g. \"5MB\")")
    n = float(m.group(1))
    return int(n * {"b": 1, None: 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}[m.group(2)])
