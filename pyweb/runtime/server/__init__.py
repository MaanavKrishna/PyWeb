"""Server runtime: routing, SSR, typed RPC dispatcher, static assets."""

from __future__ import annotations

import inspect
import json
import re
import uuid


class Request:
    def __init__(self, method, path, headers=None, body=b"", cookies=None):
        self.method = method.upper()
        self.path = path
        self.headers = headers or {}
        self.body = body
        self.cookies = cookies or {}
        self.id = uuid.uuid4().hex[:12]


class Response:
    def __init__(self, status=200, body="", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}


def _coerce(value, ann):
    if ann in ("int", "Integer"):
        return int(value)
    if ann in ("float", "Float", "Decimal"):
        return float(value)
    if ann in ("bool", "Boolean"):
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes")
        return bool(value)
    return value


class Server:
    def __init__(self, compiled):
        self.compiled = compiled
        self.rpc_impls: dict[str, object] = {}
        self.routes: list[tuple[re.Pattern, str]] = []
        for name, page in compiled["pages"].items():
            pat = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", page["route"]) + "$"
            self.routes.append((re.compile(pat), name))

    def register_rpc(self, fn):
        self.rpc_impls[fn.__name__] = fn

    def _validate(self, fn, args: dict):
        sig = inspect.signature(fn)
        out = {}
        for pname, param in sig.parameters.items():
            ann = getattr(param.annotation, "__name__", str(param.annotation)) if param.annotation is not inspect.Parameter.empty else "Any"
            if pname in args:
                try:
                    out[pname] = _coerce(args[pname], ann)
                except (ValueError, TypeError):
                    raise TypeError(f"{fn.__name__}.{pname} expects {ann}")
                if "Email" in ann and "@" not in str(out[pname]):
                    raise ValueError(f"{fn.__name__}.{pname} must be a valid email")
            elif param.default is inspect.Parameter.empty:
                raise TypeError(f"{fn.__name__} missing required argument {pname!r}")
        return out

    def handle_rpc(self, req: Request):
        m = re.match(r"^/__pyweb/rpc/(\w+)$", req.path)
        if not m:
            return None
        name = m.group(1)
        fn = self.rpc_impls.get(name)
        if fn is None:
            return Response(404, json.dumps({"error": f"unknown rpc {name}"}), {"Content-Type": "application/json"})
        try:
            payload = json.loads(req.body or b"{}")
        except json.JSONDecodeError:
            return Response(400, json.dumps({"error": "invalid JSON"}), {"Content-Type": "application/json"})
        args = payload.get("args", {}) if isinstance(payload, dict) else {}
        try:
            clean = self._validate(fn, args if isinstance(args, dict) else {})
            result = fn(**clean)
            return Response(200, json.dumps({"result": result}), {"Content-Type": "application/json"})
        except (TypeError, ValueError) as exc:
            return Response(422, json.dumps({"error": str(exc)}), {"Content-Type": "application/json"})
        except Exception as exc:  # noqa: BLE001
            return Response(500, json.dumps({"error": f"{type(exc).__name__}: {exc}"}), {"Content-Type": "application/json"})

    def handle(self, req: Request):
        if req.path.startswith("/__pyweb/rpc/"):
            return self.handle_rpc(req)
        for pat, name in self.routes:
            m = pat.match(req.path)
            if m:
                page = self.compiled["pages"][name]
                return Response(200, page["html"], {"Content-Type": "text/html", "X-Request-Id": req.id})
        return Response(404, "not found", {"Content-Type": "text/plain"})
