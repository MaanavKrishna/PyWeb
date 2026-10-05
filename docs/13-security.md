# Security model

## Threat model

**PyWeb defends against** (on by default, no code needed):

- other sites making a visitor's browser call your server functions
  (CSRF: JSON-only RPCs, `Origin` and `Sec-Fetch-Site` checks,
  `SameSite` cookies);
- injecting HTML or script through data you display (escaping, URL
  filtering, a strict Content-Security-Policy);
- forged or tampered sessions, live-update feeds and live queries
  (HMAC-signed with separate keys);
- server-only code, imports and secrets leaking into browser code
  (compile errors);
- malformed or hostile requests: wrong argument types, oversized or
  deeply nested bodies, huge headers, slow clients, connection floods,
  too many live connections from one address;
- tampered npm package files (`pyweb.lock` hashes);
- path traversal in static files and unsafe redirects after login.

**Your app is responsible for:**

- authorization: checking *who* may do *what* in `@server` functions and
  page bodies (`session.require(...)`), never in browser code;
- keeping `PYWEB_AUTH_SECRET` secret and serving over HTTPS
  (`PYWEB_COOKIE_SECURE=1`);
- validating business rules beyond types (amounts, ownership, limits);
- what you do with uploads, raw SQL you build yourself, and third-party
  services you call.

Run `pyweb check --production app.pyweb` before deploying: it lists
settings that would leave a gap (see [Deployment](12-deployment.md)).

## What reaches the browser

Only three things are sent to a browser:

1. **HTML** rendered from your markup.
2. **The page module**: JavaScript compiled from event handlers, markup
   expressions, computed values, browser-callable helpers and the
   literal module constants they reference.
3. **The page state**: JSON containing the values of page variables
   that browser code reads.

Everything else stays on the server: imports, database handles,
classes, `@server` function bodies, module objects, and page variables
that browser code doesn't read. Referencing a server-only name from
browser code is a compile error. `pyweb inspect` lists, per name,
whether it runs in the browser or on the server and why.

Treat everything in (1)–(3) as public. Authorization belongs in
`@server` functions and page bodies (`session.require(...)`), never in
browser code.

## Secrets

The compiler rejects, with file and line, a page variable whose name
looks like a credential (`secret`, `password`, `token`, `api_key`,
`private_key`, `credential`) if browser code or markup reads it and its
value is not an empty literal. Empty form fields (`password = ""` bound
to an input) are allowed because the value comes from the user.

`pyweb check` adds pattern-based checks over the source (environment
reads, credential-like literals) and exits non-zero on findings.

## Cross-site scripting

- Text and attribute values are escaped on the server and set as text
  (never as HTML) in the browser.
- URLs in `href`, `src`, `action` and `formaction` that start with
  `javascript:`, `vbscript:` or `data:text/html` are replaced by `#`.
- The default Content-Security-Policy only allows scripts from your
  origin:

  ```text
  default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self';
  frame-ancestors 'self'; img-src 'self' data: https:;
  style-src 'self' 'unsafe-inline' https:; font-src 'self' data: https:;
  connect-src 'self'; worker-src 'self' blob:
  ```

  `worker-src ... blob:` lets npm packages that start Web Workers from
  generated code (canvas-confetti, PDF and map libraries) do so; only
  scripts already allowed by `script-src` can create one. When a page
  uses npm packages, the hash of its import map is added to
  `script-src`. Override the policy with `PYWEB_CSP` if you load
  scripts from a CDN.

## Cross-site request forgery

RPC endpoints only accept `POST` with a JSON content type (cross-site
form posts are rejected with `415`) and reject requests whose `Origin`
header names another site, or that the browser marks
`Sec-Fetch-Site: cross-site` (`403 csrf_failed`). Session cookies are
`SameSite=Lax` and `HttpOnly`. Setting `PYWEB_CSRF_SECRET` adds a
token check for RPCs marked `@pyweb.decorators.auth_required`.

## Signing keys and rotation

`PYWEB_AUTH_SECRET` is a root secret. Sessions, live-update feeds and
live query specs are each signed with their own key derived from it, so
a token made for one purpose can't be used as another.

To change the secret without signing everyone out, set the new value as
`PYWEB_AUTH_SECRET` and move the old one to `PYWEB_AUTH_SECRET_PREVIOUS`
(comma-separate several). Tokens signed with an old secret keep working
until they expire; remove it from the list after a month (the longest a
session lasts). Tokens issued before 0.4.4 also keep working through
0.4.x.

## Server function arguments

Arguments are checked against the function's type hints before it runs:
`str`, `int`, `float`, `bool`, `list[...]`, `dict[str, ...]`,
`Optional[...]`, `Literal[...]`, dataclasses, `Model` subclasses and
`Email`. A `name: str` never receives an object, a dataclass or Model
gets only its own fields, and unknown argument names are refused, all
with `422 validation_error` naming the field. Parameters without a type
hint receive the JSON value as it is.

## SQL injection

`pyweb.db` only executes parameterised statements; `Query` validates
table and column identifiers.

## Other protections

- Request bodies are capped (1 MiB by default, `413` beyond), and RPC
  bodies nested more than 32 levels deep or holding more than 10,000
  values are refused (`422`).
- `pyweb serve` drops clients that stall for 30 seconds
  (`PYWEB_SOCKET_TIMEOUT`), answers `503` beyond 256 simultaneous
  connections (`PYWEB_MAX_CONNECTIONS`) and `431` to headers over 16 KB.
  A client address may hold 20 live connections
  (`PYWEB_MAX_STREAMS_PER_CLIENT`; `429` beyond).
- Pages that take longer than 30 seconds (`PYWEB_RENDER_TIMEOUT`) get a
  `504` instead of holding the server.
- RPC calls are rate-limited (120/minute per client address by default).
  Behind a reverse proxy, set `PYWEB_TRUST_PROXY=1` so the visitor's
  address is used; `X-Forwarded-For` is ignored otherwise, because
  anyone can send it.
- Static file serving is confined to the static directory (path
  traversal returns `403`).
- `pyweb.security.safe_next` / `is_safe_redirect` validate redirect
  targets; `pyweb.uploads.validate_upload` checks size, declared type and
  magic bytes, and generates safe storage names.
- Passwords use PBKDF2-HMAC-SHA256 with per-user salts; session cookies
  are HMAC-SHA256 signed and compared in constant time. Each login
  starts a new session, sessions end 30 days after sign-in, and
  `auth.revoke_user(user_id)` signs a user out everywhere
  (see [Auth](10-auth.md)).
- Every response carries `X-Content-Type-Options`, `Referrer-Policy`,
  `X-Frame-Options`, `Permissions-Policy` (camera, microphone, location
  and payments off), `Cross-Origin-Opener-Policy` and
  `Cross-Origin-Resource-Policy`; with `PYWEB_COOKIE_SECURE=1`, also
  `Strict-Transport-Security`. A page that needs the camera can send its
  own `Permissions-Policy`.
- `RedisCache` stores JSON. Reading pickled data can run code, so it's
  only allowed with `RedisCache(..., allow_pickle=True)`.
- `pyweb.lock` records a SHA-256 for every npm file; `pyweb build`
  refuses files that changed since `pyweb add`.
- Releases carry signed build provenance: check a downloaded file with
  `gh attestation verify FILE --repo MaanavKrishna/PyWeb`.
- Live-update channels can only be read with a feed from `channel()`:
  an HMAC-signed token that expires after 24 hours. A page that shows
  private data should only create a feed after checking the visitor
  (for example with `session.require()`), exactly as it would before
  rendering the data itself.

## Reporting a vulnerability

See [SECURITY.md](https://github.com/MaanavKrishna/PyWeb/blob/main/SECURITY.md).
