# Authentication

## The auth kit

One line gives an app accounts, sign-in pages and an admin:

```pyweb
from pyweb import App, server

app = App(title="Acme", database="sqlite:///app.db")
auth = app.use_auth(providers=["github"])


@app.page("/dashboard", login=True)
def Dashboard():
    name = auth.user().name
    <h1>Hello {name}</h1>


@app.page("/billing", login=True, roles=["owner"], fresh=600)
def Billing():
    <p>Only owners who signed in within the last 10 minutes see this.</p>


@server(login=True)
def rename(name: str) -> str:
    user = auth.user()
    user.name = name
    user.save()
    return user.name
```

It adds these pages, rendered on the server so they work without
JavaScript:

| Route | What it does |
|---|---|
| `/signup` | Create an account. With `signup="verify"` (the default) the account is made only once the emailed link is clicked; `"instant"` signs in at once; `False` turns sign-up off. |
| `/login` | Password, an emailed sign-in link, a passkey (also offered in the email field's autofill), or GitHub / Google / Microsoft. `?next=/path` returns there afterwards (only paths on your site). |
| `/login/2fa` | The authenticator-app code (or a recovery code) for accounts with two-step sign-in. |
| `/logout` | Signs out (a POST with the page's token; a GET only shows the button). |
| `/reset` | Emails a link to choose a new password. Using it signs out every other device. |
| `/account` | Name, email, password, passkeys, two-step sign-in with recovery codes, linked accounts, "sign out everywhere else" and recent activity. |
| `/admin` | Browse, search, edit and delete rows of every Model. Only for users with the `admin` role, who must have signed in within the hour to change anything. Private fields (password hashes, secrets) never appear. |

To replace one, define a page at the same route; the others stay. The
kit's Python API does the work for custom pages:

| Call | Effect |
|---|---|
| `auth.user()` | The signed-in `User` row, or `None`. |
| `auth.require(*roles, fresh=None)` | The user, or raises `RPCError` (`unauthenticated`, or `forbidden` without the roles). |
| `auth.authenticate(email, password)` | The `User` if the password is right; otherwise raises `ValidationError` with the same message for a wrong password and an unknown email (and the same timing). Throttled. |
| `auth.login(user)` / `auth.logout()` | Start (with a new session id) or end the session. |
| `auth.create_user(email, password, name=, roles=)` | A new account. |
| `auth.set_password(user, pw)`, `auth.set_roles(user, roles)` | Both sign the user out of their other sessions. |
| `auth.revoke(user)` | Signs the user out everywhere. |
| `auth.send_reset(email)`, `auth.send_magic_link(email)` | Email a link (nothing happens, silently, for unknown emails). |

### Guards

`login=True`, `roles=[...]` and `fresh=seconds` work on `@app.page`,
`@app.layout` (guarding every page under it) and `@server`. A signed-out
visitor is sent to `/login?next=...`; a server function answers 401.
A signed-in user without the role gets 403. `fresh` asks people to sign in
again when their last sign-in is older than that, for things like changing
billing or deleting an account.

### Row policies

A policy limits which rows each user can see and change, on every query
and save made while handling a request:

```python
from pyweb import Field, Model


class Note(Model):
    text: str = Field(max=500)
    owner_id: int = 0


Note.policy(read=lambda user: Note.owner_id == (user.id if user else -1),
            write=lambda user, note: user is not None and note.owner_id == user.id)
```

`Note.query()` then returns only the signed-in user's notes, and saving or
deleting someone else's answers 403, so guessing another row's id
(an IDOR bug) gets nothing. Scripts, jobs and tests run outside requests
and skip policies; inside a request, `with system():` (from
`pyweb.models`) skips them on purpose.

### The tables

`users`, `auth_identities` (linked GitHub/Google/Microsoft accounts),
`auth_credentials` (passkeys), `auth_tokens` (emailed links, stored only
as hashes) and `auth_events` (the audit log). They're ordinary Models:
`from pyweb.authkit import User, AuthEvent`, query them, add them to
migrations with `pyweb db diff`, or see them in `/admin`.

### Sign-in with GitHub, Google or Microsoft

Register `https://your.site/auth/<provider>/callback` with the provider,
then set `PYWEB_OAUTH_GITHUB_ID` and `PYWEB_OAUTH_GITHUB_SECRET` (or
`GOOGLE`, `MICROSOFT`). The flow uses PKCE and a `state` check. Only
emails the provider has verified are used. A provider sign-in never
attaches itself to an existing password account by itself: the person
signs in first and links it from `/account`.

### Email

Sign-up confirmations, resets and sign-in links go through `pyweb.mail`.
While developing nothing is sent: messages are printed in the terminal
(follow the link from there) and kept in `pyweb.mail.OUTBOX` for tests.
In production set:

| Variable | Purpose |
|---|---|
| `PYWEB_ORIGIN` | The site's public address, e.g. `https://acme.dev`. **Required** in production: links in emails are never built from the request's `Host` header, which anyone can set (that would let an attacker get reset links pointing at their own site). |
| `PYWEB_MAIL_URL` | `smtp://user:password@smtp.example.com:587` (STARTTLS) or `smtps://...:465`. Without it, sending fails in production rather than silently dropping a reset email. |
| `PYWEB_MAIL_FROM` | The sender, e.g. `Acme <no-reply@acme.dev>`. |
| `PYWEB_RP_ID` | The passkey domain, if it should differ from `PYWEB_ORIGIN`'s host (e.g. `acme.dev` to share passkeys with `app.acme.dev`). |

`pyweb check --production` reports a missing `PYWEB_ORIGIN`.

### What it protects against

- **Passwords** are hashed with scrypt (standard library), or argon2id with
  `pip install "pyweb-stack[auth]"`. Older hashes are upgraded at the next
  sign-in. Passwords need 10+ characters, can't be the email or name, and in
  production are checked against known breaches (Pwned Passwords; only 5
  characters of a hash leave the server; `PYWEB_BREACHED_PASSWORDS=0` turns
  it off).
- **Guessing** is slowed down per account and per IP address: after 5
  failures each attempt waits twice as long (1 s, 2 s, 4 s ... up to 15
  minutes). There's no hard lockout, which would let anyone lock a victim
  out. With `PYWEB_REDIS_URL` every server shares the counts.
- **Account discovery.** Sign-in, sign-up and reset answer the same way, and
  take the same time, whether or not an email has an account.
- **Emailed links** are random, stored only as hashes, expire after 15
  minutes (24 hours for sign-up), and work once, even if clicked twice at the
  same moment. Opening a sign-in link shows a button rather than signing in
  directly, so email scanners that follow links can't use it up or sign in.
- **Sessions** get a new id at sign-in. Password resets and changes, role
  changes and "sign out everywhere" end the other sessions at once; the
  session version is kept in the `users` table, so this holds across
  servers and restarts. If that check can't reach the database, sessions
  are refused rather than trusted.
- **Passkeys** check the origin, the challenge (5 minutes, single use on
  every server, so a captured response can't be replayed),
  user presence and the signature counter, for ES256 and RS256 keys.
- Every sign-in, failure, reset, 2FA change, role change and admin edit is
  recorded in `auth_events`, shown on `/account` and in `/admin`.

## Building it yourself

The rest of this page covers the lower-level pieces the kit is built on,
for apps that keep their own user store.

### Sessions

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

#### How long sessions last

- Each `session.login(...)` starts a new session with its own id, so a
  session someone planted before login is never reused.
- A session ends after **7 days without a visit** (`session.max_age`).
  Visiting renews it (the cookie is reissued at most once a day).
- A session ends **30 days after sign-in** however active the user is
  (`session.absolute_age`); they then sign in again.

#### Signing someone out everywhere

```python
from pyweb import auth

auth.revoke_user(user_id)        # e.g. after a password change or "log out of all devices"
```

Every session the user has, on every device, stops working; new logins
work as usual. By default this is remembered in the server process. With
several processes, keep it in Redis:

```python
auth.use_session_versions(auth.RedisSessionVersions("redis://localhost:6379/0"))
```

#### Configuration

| Variable | Purpose |
|---|---|
| `PYWEB_AUTH_SECRET` | Signing key. **Required** when `PYWEB_ENV=production`; generate with `python -c "import secrets; print(secrets.token_hex(32))"`. Without it, development uses a key derived from the machine and project directory. |
| `PYWEB_COOKIE_SECURE=1` | Adds `Secure` to cookies. Set it whenever you serve over HTTPS. |
| `PYWEB_CSRF_SECRET` | Enables token-based CSRF checks for RPCs marked `@pyweb.decorators.auth_required`, in addition to the always-on checks (RPCs must be `application/json`, and a cross-origin `Origin` header is rejected). |

### Passwords

`pyweb.auth.hash_password(password)` uses PBKDF2-HMAC-SHA256 with a
random salt; `verify_password(password, stored)` compares in constant
time.

### Redirecting after login

If your login page takes a `?next=` address to return to, pass it
through `auth.safe_next(...)` before redirecting. It keeps paths on
your site (`/orders?page=2`) and turns anything else
(`//evil.example`, `https://...`) into `/`, so a crafted link can't
send people to another site after they sign in. `login_response`
already does this for its `next_url`.

### One-time codes and magic links

```python
from pyweb import auth

code = auth.totp(secret_bytes)                       # RFC 6238, 6 digits, 30 s
ok = auth.verify_totp(secret_bytes, "123456")        # ±1 step tolerance

token = auth.issue_magic_token(SECRET, "ada@example.com", ttl=900)
email = auth.verify_magic_token(SECRET, token)       # None if expired/forged
```

Magic-link tokens are signed differently from session cookies, so one
can never be used as the other. Links issued before 0.4.3 stop working
after the upgrade; they last 15 minutes by default.

### Passkeys (WebAuthn)

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
`pip install "pyweb-stack[crypto]"` the signature is verified by the
`cryptography` library; otherwise by a pure-Python implementation.
For registration too, and RS256 keys, use
`pyweb.authkit.webauthn.verify_registration` and `verify_assertion`
(what the kit uses), or the kit itself.

### OAuth / OpenID Connect

`pyweb.auth.oidc_userinfo(endpoint, access_token)` fetches the user
profile once your OAuth flow has an access token. For the whole
redirect and callback flow (PKCE, `state`, account linking), use the kit's
`providers=` option above, or `pyweb.authkit.oauth.Provider` on its own.
