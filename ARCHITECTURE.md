# PyWeb Architecture

## Track C: auth + security (OWASP top 10 for the framework + first-party auth)

Owned files: `pyweb/auth.py`, `pyweb/security.py`, `pyweb/forms.py`
(validation hardening only), `pyweb/uploads.py`,
`pyweb/compiler/placement.py` (security-boundary rules only, API stable).

### Session cookies

Format: `base64url(payload).hexhmac` where `payload` is the JSON session
dict (with `iat` issued-at epoch) encoded as unpadded URL-safe base64, and
`hexhmac` is `HMAC-SHA256(secret, payload_b64)` hex-digested. Verification
uses `hmac.compare_digest` and rejects cookies older than `max_age`
(default 3600s). See `pyweb/auth.py` module docstring.

### Guards & RBAC

- `@auth.required` — page guard; raises `NotAuthenticated` without a
  session dict containing `sub`.
- `@auth.permission("role")` — requires `role` in `session["roles"]`,
  else `Forbidden`.

### OAuth / magic links / MFA

- `OAuthClient(provider, ...)` — authorization-code flow over `urllib`
  (no deps); presets for `google` and `github`.
- Magic links — `issue_magic_token(secret, email, ttl)` /
  `verify_magic_token(...)`; `secrets.token_urlsafe` nonce + expiry.
- TOTP MFA — `totp()` / `verify_totp()` over stdlib `hmac`/`sha1`
  per RFC 6238 (30s step, 6 digits, ±1 window).

### Static serving (expected server behavior)

Serve ONLY paths resolving inside the static root. `security.safe_join`
is the reference helper: escapes raise `PathTraversalError` → respond
403/404 upstream without filesystem or traceback disclosure.

### Uploads

`uploads.validate_upload` enforces extension + magic-byte allowlist
(png/jpg/gif/webp/pdf/txt/csv) and `MAX_BYTES` (5 MiB) size caps.

### Login `next=` (open-redirect guard)

Only same-origin relative paths pass `security.is_safe_redirect`;
`safe_next(param, default)` falls back to `default` otherwise.

### Placement security boundary

`pyweb/compiler/placement.py::check_source(source, filename, placement)`
raises `CompileError` with `file:line` + leak path when browser-placed
code references `@server` secrets/credentials or server-only imports
(`psycopg`, `sqlite3`, `os.environ` secrets, file reads). API stable:
`(source, filename, placement) -> placement`. `SECRET_PATTERNS` holds
the secret regexes.

### Security scan

`security.scan(app)` reports `secret-leak`, `xss-risk`, and
`missing-auth-on-mutating-rpc` findings. CLI: `pyweb --security-scan`
(additive flag only).
