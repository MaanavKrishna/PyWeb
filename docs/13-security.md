# Security model

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
header names another site (`403 csrf_failed`). Session cookies are
`SameSite=Lax` and `HttpOnly`. Setting `PYWEB_CSRF_SECRET` adds a
token check for RPCs marked `@pyweb.decorators.auth_required`.

## SQL injection

`pyweb.db` only executes parameterised statements; `Query` validates
table and column identifiers.

## Other protections

- Request bodies are capped (1 MiB by default, `413` beyond).
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
  are HMAC-SHA256 signed and compared in constant time.
- Live-update channels can only be read with a feed from `channel()`:
  an HMAC-signed token that expires after 24 hours. A page that shows
  private data should only create a feed after checking the visitor
  (for example with `session.require()`), exactly as it would before
  rendering the data itself.

## Reporting a vulnerability

See [SECURITY.md](https://github.com/MaanavKrishna/PyWeb/blob/main/SECURITY.md).
