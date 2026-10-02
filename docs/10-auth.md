# Authentication

## Sessions

`pyweb.session` keeps the signed-in user in a signed, `HttpOnly`,
`SameSite=Lax` cookie (HMAC-SHA256). No server-side session store is
needed, so any server process can verify it.

```pyweb
from pyweb import App, RPCError, redirect, server, session
from pyweb.auth import hash_password, verify_password

app = App(title="Accounts")
USERS = {}   # use a database table in a real app


@server
def register(email: str, password: str) -> bool:
    if len(password) < 8:
        raise RPCError("validation_error", "Use at least 8 characters.")
    USERS[email] = {"hash": hash_password(password), "roles": ["member"]}
    session.login(email, roles=["member"])
    return True


@server
def login(email: str, password: str) -> bool:
    user = USERS.get(email)
    if user is None or not verify_password(password, user["hash"]):
        return False
    session.login(email, roles=user["roles"])
    return True


@server
def admin_report() -> str:
    session.require("admin")          # 401 if signed out, 403 without the role
    return "secret numbers"


@app.page("/account")
def Account():
    user = session.user()
    if not user:
        return redirect("/login")
    email = user["sub"]
    <p>Signed in as {email}</p>
```

| Call | Effect |
|---|---|
| `session.login(user_id, **claims)` | Sets the cookie. Claims (e.g. `roles`, `name`) are stored in it, signed. |
| `session.user()` | The payload `{"sub": user_id, **claims}` or `None`. |
| `session.require(*roles)` | Returns the payload or raises `RPCError` (`unauthenticated` / `forbidden`). |
| `session.logout()` | Clears the cookie. |

Sessions last 7 days (`session.max_age`).

### Configuration

| Variable | Purpose |
|---|---|
| `PYWEB_AUTH_SECRET` | Signing key. **Required** when `PYWEB_ENV=production`; generate with `python -c "import secrets; print(secrets.token_hex(32))"`. Without it, development uses a key derived from the machine and project directory. |
| `PYWEB_COOKIE_SECURE=1` | Adds `Secure` to cookies. Set it whenever you serve over HTTPS. |
| `PYWEB_CSRF_SECRET` | Enables token-based CSRF checks for RPCs marked `@pyweb.decorators.auth_required`, in addition to the always-on checks (RPCs must be `application/json`, and a cross-origin `Origin` header is rejected). |

## Passwords

`pyweb.auth.hash_password(password)` uses PBKDF2-HMAC-SHA256 with a
random salt; `verify_password(password, stored)` compares in constant
time.

## One-time codes and magic links

```python
from pyweb import auth

code = auth.totp(secret_bytes)                       # RFC 6238, 6 digits, 30 s
ok = auth.verify_totp(secret_bytes, "123456")        # ±1 step tolerance

token = auth.issue_magic_token(SECRET, "ada@example.com", ttl=900)
email = auth.verify_magic_token(SECRET, token)       # None if expired/forged
```

## Passkeys (WebAuthn)

`pyweb.auth.verify_webauthn_assertion` verifies a login assertion for an
ES256 (P-256) credential:

```python
parsed = auth.verify_webauthn_assertion(
    credential_public_key=stored_point,        # 65-byte raw point; see cose_to_raw_point
    auth_data=auth_data, client_data_json=client_data_json, signature=signature,
    rp_id="example.com",
    expected_challenge=challenge_you_issued,   # bytes or base64url
    expected_origin="https://example.com",
    prior_sign_count=stored_count,
)
```

It checks the relying-party hash, user presence (and optionally
verification), the ceremony type, the challenge, the origin and the
signature counter, and accepts the DER signatures browsers send. With
`pip install "pyweb-framework[crypto]"` the signature is verified by the
`cryptography` library; otherwise by a pure-Python implementation.
Registration (attestation) parsing is not included; store the
credential's COSE key from your registration flow and convert it with
`cose_to_raw_point`.

## OAuth / OpenID Connect

`pyweb.auth.oidc_userinfo(endpoint, access_token)` fetches the user
profile once your OAuth flow has an access token. The redirect/callback
flow itself is left to your provider's SDK.
