"""Typed browser APIs.

Every capability exposes the same Python surface on the server (safe
stub returning documented defaults) and in the browser (real binding).
The compiler maps each stub to its JS implementation via
:meth:`Api._js`; server execution never touches the DOM.

Sensor/action APIs (camera, location, clipboard, notifications, share,
filesystem, bluetooth) raise :class:`BrowserUnavailable` on the server
instead of silently returning fake data — fake sensor data would be
worse than an error. Query-style APIs (fetch, permissions, vibrate,
IDB) return documented safe defaults so SSR code paths keep working.
"""

from __future__ import annotations


class BrowserUnavailable(RuntimeError):
    """Raised when a browser-only API is called outside the browser."""


class Api:
    _js = ""
    _server_default = None

    def _call(self, method, *args):
        raise BrowserUnavailable(
            f"{type(self).__name__}.{method}() needs a browser; "
            "guard with `if platform.web:` or move it into an event handler")


class _KeyValue(Api):
    _js = "localStorage"
    _server_default = None

    def __init__(self):
        self._d: dict = {}

    def __getitem__(self, k):
        return self._d.get(k)

    def __setitem__(self, k, v):
        self._d[k] = v

    def __delitem__(self, k):
        del self._d[k]

    def get(self, k, default=None):
        return self._d.get(k, default)


class _SessionStorage(_KeyValue):
    _js = "sessionStorage"


storage = _KeyValue()
session = _SessionStorage()


class _Clipboard(Api):
    _js = "navigator.clipboard"

    async def write(self, text: str) -> bool:
        self._call("write", text)
        return False

    async def read(self) -> str:
        self._call("read")
        return ""


clipboard = _Clipboard()


class _GeoPosition(dict):
    pass


class _Location(Api):
    _js = "navigator.geolocation"

    async def current(self, *, timeout_ms=10000) -> dict:
        self._call("current")
        return {}

    def watch(self, callback, *, timeout_ms=10000):
        """Subscribe to position updates; returns an unsubscribe callable."""
        self._call("watch", callback)
        return lambda: None


location = _Location()


class _Notifications(Api):
    _js = "Notification"

    async def request_permission(self) -> str:
        self._call("request_permission")
        return "denied"

    async def send(self, title: str, body="", *, tag="") -> bool:
        self._call("send", title)
        return False


notifications = _Notifications()


class _Camera(Api):
    _js = "navigator.mediaDevices.getUserMedia"

    async def photo(self, *, facing="user") -> bytes:
        self._call("photo")
        return b""


camera = _Camera()


class IDB(Api):
    """Typed IndexedDB key-value store (offline persistence)."""

    _js = "indexedDB"

    def __init__(self, db_name="pyweb", store="kv"):
        self._db_name = db_name
        self._store = store
        self._d: dict = {}

    async def get(self, key, default=None):
        try:
            self._call("get", key)
        except BrowserUnavailable:
            return self._d.get(key, default)

    async def set(self, key, value):
        try:
            self._call("set", key, value)
        except BrowserUnavailable:
            self._d[key] = value

    async def delete(self, key):
        try:
            self._call("delete", key)
        except BrowserUnavailable:
            self._d.pop(key, None)


idb = IDB()


class _Fetch(Api):
    _js = "fetch"

    async def get(self, url: str, *, headers=None, timeout_s=30) -> dict:
        return {"status": 0, "body": ""}

    async def post(self, url: str, body=None, *, headers=None,
                   timeout_s=30) -> dict:
        return {"status": 0, "body": ""}


fetch = _Fetch()


class _Permissions(Api):
    _js = "navigator.permissions"

    async def query(self, name: str) -> str:
        """'granted' | 'denied' | 'prompt' — 'denied' on server."""
        return "denied"


permissions = _Permissions()


class _Vibrate(Api):
    _js = "navigator.vibrate"

    def pulse(self, pattern=200) -> bool:
        return False  # no-op on server; real haptics in browser


vibrate = _Vibrate()


class _Share(Api):
    _js = "navigator.share"

    async def text(self, title: str, text: str, *, url="") -> bool:
        self._call("text", title)
        return False


share = _Share()


class _Bluetooth(Api):
    _js = "navigator.bluetooth"

    async def request_device(self, *, accept_all=False,
                             services=()) -> dict:
        self._call("request_device")
        return {}


bluetooth = _Bluetooth()


class _FileSystem(Api):
    _js = "showOpenFilePicker"

    async def pick_text(self, *, types=("text/plain",)) -> str:
        self._call("pick_text")
        return ""


filesystem = _FileSystem()


class _Navigate(Api):
    """``navigate("/path")``: go to another page of the app from browser code (no full reload)."""
    _js = "$py.go"

    def __call__(self, url):
        self._call("navigate", url)


navigate = _Navigate()


def bindings() -> dict:
    """Name -> JS binding for every browser API (used by codegen)."""
    import sys as _sys
    mod = _sys.modules[__name__]
    out = {}
    for name in dir(mod):
        obj = getattr(mod, name)
        js = getattr(obj, "_js", "")
        if isinstance(obj, Api) and js:
            out[name] = js
    return out
