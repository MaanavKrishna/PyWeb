# 05 - Auth

> Source: `examples/snippets/auth_guard.py` (executed by the suite).

## Guarded markup

<!-- snippet: examples/snippets/auth_guard.py -->
```python
<section data-pw-id="dashboard" pw-guard="auth">dashboard</section>
# unauthenticated GET /dashboard -> 302 Location: /login
# authenticated GET /dashboard (Authorization header) -> 200
```

Full app: `examples/auth/app.pyweb`.

## Guard logic

<!-- snippet: examples/snippets/auth_guard.py -->
```python
def guard(path: str, authorized: bool) -> tuple[int, str]:
    """Return (status, location-or-body) for the protected route contract."""
    if path == PROTECTED_ROUTE and not authorized:
        return 302, LOGIN_ROUTE
    return 200, "dashboard"


def login(username: str, password: str) -> dict:
    if not username or not password:
        raise ValueError("validation: username and password required")
    return {"ok": True, "user": username}
```

Contract (`tests/test_e2e.py::TestAuthApp`): unauthenticated
`GET /dashboard` (no redirect following) returns `302` with
`Location: /login`; an authenticated request returns `200`. Login with
blank credentials fails validation like any other RPC.
