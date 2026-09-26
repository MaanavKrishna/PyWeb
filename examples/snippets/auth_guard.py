"""Snippet: auth guard used by docs/05-auth.md."""

SOURCE = """<section data-pw-id="dashboard" pw-guard="auth">dashboard</section>
# unauthenticated GET /dashboard -> 302 Location: /login
# authenticated GET /dashboard (Authorization header) -> 200
"""

LOGIN_ROUTE = "/login"
PROTECTED_ROUTE = "/dashboard"


def guard(path: str, authorized: bool) -> tuple[int, str]:
    """Return (status, location-or-body) for the protected route contract."""
    if path == PROTECTED_ROUTE and not authorized:
        return 302, LOGIN_ROUTE
    return 200, "dashboard"


def login(username: str, password: str) -> dict:
    if not username or not password:
        raise ValueError("validation: username and password required")
    return {"ok": True, "user": username}


if __name__ == "__main__":
    print(guard("/dashboard", False))
    print(guard("/dashboard", True))
    print(login("ada", "s3cret"))
