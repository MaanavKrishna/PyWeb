# PyWeb threat model

This is what PyWeb defends against, how, and which test proves it. It is
organised by trust boundary (where data crosses from something we don't
control into something we do) and, within each, by STRIDE category:
**S**poofing, **T**ampering, **R**epudiation, **I**nformation disclosure,
**D**enial of service, **E**levation of privilege.

Every row names a test. If you change a mitigation, the test that proves
it should fail first. Report gaps privately (see [SECURITY.md](SECURITY.md)).

The user-facing summary is in [docs/13-security.md](docs/13-security.md).

## What we protect

- **Accounts and sessions:** who a request belongs to.
- **App data:** rows in the database, uploaded files, and what each user may see.
- **Secrets:** `PYWEB_AUTH_SECRET`, `PYWEB_DATA_KEY`, API keys in the environment.
- **The visitor's browser:** what script runs in it and what it sends where.
- **Availability:** a server process stays up and answers others while one client misbehaves.
- **The release:** what `pip install pyweb-stack` delivers is what this repository built.

## Who we defend against

- **A visitor or a script on the internet** sends any request it likes:
  forged, oversized, slow, replayed, malformed.
- **Another website** a signed-in visitor opens. It can make the browser
  send requests with the visitor's cookies, and it can frame or link to pages.
- **A signed-in user** who tries to reach other users' data or the admin.
- **Someone with a copy of the database or a backup**, but not the keys.
- **A compromised or typo-squatted npm package** between `pyweb add` and `pyweb build`.

Out of scope: someone who controls the server, its environment variables
or the app's code; the developer deliberately writing unsafe code (raw
SQL from strings, `os.system`), though `pyweb check` flags the common
cases; and denial of service by sheer traffic volume, which needs a CDN or
load balancer in front.

## 1. Browser to server: pages, server functions, forms

| | Threat | Mitigation | Proof |
|---|---|---|---|
| S | Another site calls a server function with the visitor's cookies (CSRF) | RPCs are `POST` with a JSON content type only (a cross-site form can't send one), `Origin` and `Sec-Fetch-Site` are checked, cookies are `SameSite=Lax`; optional token check (`PYWEB_CSRF_SECRET`) | `test_request_hardening.py::test_cross_site_requests_are_refused`, `test_rpc_production.py::test_csrf_gate_blocks_cookie_calls_without_token` |
| S | Another site posts a `<Form>` | A per-visitor CSRF token in every form (double-submit cookie) plus the `Origin` check | `test_forms_v5.py::test_csrf_and_cross_site_posts_are_refused` |
| T | Arguments of the wrong shape reach a server function (an object where a string was expected, extra fields) | Arguments are checked against the type hints; unknown names are refused; Models only take their own non-readonly fields | `test_forms_v5.py::test_mass_assignment_is_ignored`, `test_fuzz_requests.py::test_rpc_arguments` |
| T | Request smuggling past a proxy | Ambiguous lengths (`Content-Length` with `Transfer-Encoding`, repeated lengths, folded headers) are refused | `test_net.py::test_refuses_ambiguous_or_malformed_requests` |
| I | Server-only code, imports or secrets end up in the page's JavaScript | The compiler places each name in the browser or on the server, and referencing a server-only name from browser code is a compile error; credential-like page variables are refused | `test_compiler.py::test_server_only_names_cannot_reach_the_browser`, `test_compiler.py::test_secret_never_goes_to_browser` |
| I | Stack traces or internal errors reach the visitor | Error responses carry a message and a request id, never a traceback | `test_auth_security.py::test_error_responses_never_leak_tracebacks` |
| I | Encrypted columns reach the browser through `to_dict()` | Encrypted and private fields are left out of `to_dict()` | `test_encrypted_fields.py::test_encrypted_values_stay_out_of_to_dict`, `test_data_layer.py::test_private_fields_stay_out_of_dicts` |
| D | Huge, deeply nested or very wide bodies | 1 MiB body limit (413), 32 levels / 10,000 values for JSON, 1,000 fields / 100 files for forms, 16 KB of headers (431) | `test_request_hardening.py::test_deeply_nested_json_is_refused_not_a_crash`, `test_request_hardening.py::test_huge_headers_get_431`, `test_fuzz_requests.py::test_too_many_fields_are_refused` |
| D | Slow clients hold connections (slowloris) | Request heads must arrive within 10 s, idle sockets close after 30 s, 10,000 connections per process | `test_request_hardening.py::test_slow_clients_are_disconnected` |
| D | One client floods server functions | 120 calls a minute per address by default, per-function `@server(rate=...)`; `X-Forwarded-For` is ignored unless `PYWEB_TRUST_PROXY` says a proxy sets it | `test_rpc_production.py::test_rate_limit_is_per_client_address_and_ignores_spoofed_headers`, `test_rate_per_function.py` |
| D | Malformed input crashes a handler | The RPC and form parsers are fuzzed: every answer is 2xx or 4xx | `test_fuzz_requests.py` (Hypothesis) |
| E | A visitor who isn't signed in reaches a protected page or function | `login=`, `roles=` and `fresh=` guards on pages, layouts and server functions | `test_authkit.py::test_instant_signup_and_guards`, `test_auth_security.py::test_required_page_guard_blocks_anonymous` |
| E | A signed-in user reads or changes another user's rows | Row policies apply to every query and save made for a request | `test_authkit.py::test_row_policies_follow_the_signed_in_user` |

## 2. Server to browser: what the page runs

| | Threat | Mitigation | Proof |
|---|---|---|---|
| T | Data shown on a page injects markup or script (XSS) | Text and attributes are escaped on the server and set as text in the browser; Markdown escapes everything it doesn't build | `test_auth_security.py::test_escape_attr_neutralizes_quote_breakout`, `e2e/test_trusted_types_browser.py::test_pages_enforce_trusted_types_and_the_runtime_works_under_them` |
| T | `javascript:` and `data:text/html` URLs in links | Replaced by `#` on the server and in the browser | `e2e/test_runtime_dom.py::test_javascript_urls_are_blocked` |
| T | Injected markup runs script anyway | Content-Security-Policy: scripts only from the site, no inline script; inline styles only by hash | `test_static_serving.py` (the header is sent), `test_trusted_types.py::test_default_policy_enforces_trusted_types_and_reports`, every browser test (a CSP violation is a console error, which fails the test) |
| T | A bug passes visitor text to an HTML sink (`innerHTML`) | Trusted Types: the browser refuses strings in HTML and script sinks; the runtime's two sinks use private policies | `e2e/test_trusted_types_browser.py::test_strings_in_html_sinks_are_refused_and_reported`, `test_trusted_types.py::test_the_runtime_only_reaches_html_sinks_through_its_policies` |
| S | The site is framed to trick clicks (clickjacking) | `frame-ancestors 'self'` and `X-Frame-Options` | `test_production_gaps.py::test_body_cap_and_head_and_security_headers`, ZAP rule 10020 in CI |
| I | Open redirects after sign-in send visitors to another site | `next=` only accepts paths on this site | `test_auth_security.py::test_open_redirect_guard_on_next_param`, `test_auth_security.py::test_login_redirects_stay_on_this_site` |
| R | Script injection attempts go unnoticed | Browsers report CSP violations to `/__pyweb/csp-report`: a log line and `pyweb_csp_violations_total` | `test_trusted_types.py::test_the_endpoint_counts_logs_and_refuses_junk` |

## 3. Sessions and accounts

| | Threat | Mitigation | Proof |
|---|---|---|---|
| S | A forged or edited session cookie | HMAC-SHA256 signed with a key derived for sessions only, compared in constant time | `test_auth.py::test_session_sign_tamper_expiry` |
| S | Session fixation | Every sign-in starts a new session id | `test_keys_sessions.py::test_each_login_is_a_new_session` |
| S | A stolen session keeps working | Sessions end 30 days after sign-in; a password change or reset signs out other devices; any one device can be signed out from `/account` | `test_authkit.py::test_password_reset_signs_out_other_devices`, `test_auth_sessions.py::test_account_lists_sessions_and_signs_one_out` |
| S | Revocation silently stops working when the database is unwell | The check fails closed: if it can't run, the session is refused | `integration/test_authkit_backends.py::test_session_check_fails_closed_when_the_database_errors` |
| S | Password guessing | Slowed down per account and per address (not a lockout, which would let anyone lock others out); breached passwords refused | `test_authkit.py::test_guessing_is_slowed_down_not_locked` |
| S | Account enumeration through sign-in or sign-up | The same message and timing for unknown emails and wrong passwords | `test_authkit.py::test_signup_with_a_taken_email_looks_the_same` |
| S | A replayed or cross-site passkey sign-in | WebAuthn challenges are single-use and bound to the site's origin and RP id | `test_authkit.py::test_a_captured_passkey_sign_in_cant_be_replayed`, `test_authkit.py::test_passkey_from_another_site_is_refused` |
| S | Taking over an account through an OAuth provider with the same email | A provider sign-in links to an existing account only after signing in to it | `test_authkit.py::test_oauth_sign_up_link_and_no_silent_takeover` |
| S | Emailed links pointing at an attacker's host | Links use `PYWEB_ORIGIN`, never the `Host` header, in production; each link works once | `test_authkit.py::test_emailed_links_never_trust_the_host_header_in_production`, `test_authkit.py::test_magic_links_need_a_click_and_work_once` |
| E | A stolen session changes the password or 2FA | Sensitive account changes need a sign-in within the last 10 minutes | `test_authkit.py::test_account_changes_need_a_recent_sign_in` |
| R | Disputed sign-ins and changes | The audit log (`auth_events`) records sign-ins, failures, resets, 2FA and role changes, with address and device | `test_authkit.py` |
| T | Header injection through email fields | Mail refuses newlines in headers | `test_authkit_units.py::test_mail_refuses_header_injection` |

## 4. Live updates: WebSockets and event streams

| | Threat | Mitigation | Proof |
|---|---|---|---|
| S | Another site opens the page's WebSocket with the visitor's cookies | Only the site's own `Origin` (or `PYWEB_ALLOWED_ORIGINS`) may connect | `test_net.py::test_cross_site_sockets_are_refused` |
| I | Reading a channel or live query without permission | Feeds and live query specs are HMAC-signed tokens, made by the server after its own checks, and expire | `test_realtime_transport.py::test_feeds_are_signed_and_expire`, `test_realtime_transport.py::test_events_and_poll_reject_missing_or_forged_feeds`, `test_livedata.py::test_specs_are_signed` |
| S | A signed-out session keeps receiving updates | Sockets close when their session is revoked | `test_net.py::test_revoked_sessions_lose_their_socket` |
| D | Many sockets from one client, or a slow reader | 20 live connections per address; slow sockets get one resync instead of a growing backlog | `test_net.py::test_a_lagging_live_query_gets_one_resync_instead_of_a_backlog` |

## 5. Server to database

| | Threat | Mitigation | Proof |
|---|---|---|---|
| T | SQL injection | Every value is a bound parameter; identifiers come from the Models; `pyweb check` flags SQL built from strings | `test_models_db.py::test_param_queries_not_interpolated`, `test_forms_security_obs.py::test_security_flags_sql_and_cmd` |
| I | A leaked backup or read-only SQL injection exposes personal data | `Field(encrypted=True)`: AES-256-GCM per column under `PYWEB_DATA_KEY`, kept apart from the session secret | `test_encrypted_fields.py::test_values_are_encrypted_at_rest_and_read_back` |
| T | Ciphertext copied into another column or row to make it show elsewhere | The table and column are the associated data; decryption fails | `test_encrypted_fields.py::test_a_ciphertext_moved_to_another_column_or_row_tamper_fails` |
| I | Key loss on rotation | Old keys stay readable from `PYWEB_DATA_KEY_PREVIOUS`; `pyweb db reencrypt` rewrites rows | `test_encrypted_fields.py::test_rotation_reads_old_rows_and_reencrypt_rewrites_them` |
| E | Background jobs or admin bypass row policies | Jobs and `system()` blocks run without a user on purpose (they're server code); the admin requires the `admin` role and a recent sign-in, and never shows private fields | `test_authkit.py::test_admin_lists_edits_and_respects_private_fields` |

## 6. Server to other services

| | Threat | Mitigation | Proof |
|---|---|---|---|
| S/I | Server-side request forgery: a user-supplied URL makes the server call internal addresses (cloud metadata, Redis, admin pages) | `pyweb.http.fetch` refuses non-public addresses, pins the checked address against DNS rebinding, checks every redirect, drops credentials across sites | `test_http_fetch.py` |
| T | Code execution through cached data | `RedisCache` stores JSON; pickle only with `allow_pickle=True` (and `pyweb check --production` warns) | `test_systems.py::test_redis_cache_refuses_pickle_unless_allowed` |
| I | Secrets in logs | Log fields and URLs with credential-like names are redacted | `test_telemetry.py::test_redaction_of_fields_text_and_urls` |
| I | Metrics reveal internals | `/metrics` needs `PYWEB_METRICS_TOKEN` in production | `test_telemetry.py::test_metrics_endpoint_needs_a_token_in_production` |

## 7. The developer's machine and the build

| | Threat | Mitigation | Proof |
|---|---|---|---|
| T | A vendored npm file changes between `pyweb add` and `pyweb build` | `pyweb.lock` records a SHA-256 per file; the build refuses changed files | `test_packages.py::test_vendored_files_are_checked_against_the_lock` |
| I | API keys committed with the app | `pyweb check` fails on keys in known formats and warns about random values in secret-named variables | `test_secret_scan.py` |
| I | The dev server exposed to the network | `pyweb dev` binds to `127.0.0.1`; static paths can't leave the static folder | `test_production_gaps.py::test_dev_server_traversal_blocked_over_http` |
| — | Production misconfiguration (no secret, no HTTPS cookies, no proxy setting, missing data key) | `pyweb check --production` lists each gap | `test_encrypted_fields.py::test_production_check_flags_a_missing_or_shared_key`, `test_deploy_v5.py::test_check_production_knows_what_the_app_needs` |

## 8. The release

| | Threat | Mitigation | Proof |
|---|---|---|---|
| T | A modified package on PyPI or a GitHub release | Releases are built by the release workflow with Trusted Publishing (no API token), signed build provenance and PEP 740 attestations | `.github/workflows/release.yml` |
| T | A compromised GitHub Action | Every action is pinned to a commit; Dependabot proposes updates | `test_sbom.py::test_workflows_pin_every_action_by_commit` |
| I | Unknown contents | Each release has a CycloneDX bill of materials; `pyweb build` writes one for apps | `test_sbom.py` |
| — | Regressions in headers and cookies | An OWASP ZAP baseline scan runs on every push against the saas example served as in production | CI job `zap` (`.github/zap/rules.tsv`) |

## Your app's part

PyWeb can't decide these for you:

- **Authorization rules:** who may do what. Use guards and row policies,
  and never rely on browser code for them.
- **Business rules:** amounts, ownership and limits beyond types and
  `Field` rules.
- **What you do with uploads** after `validate_upload`, raw SQL you build
  yourself, and third-party services you call. Use `pyweb.http.fetch` for
  URLs other people give you.
- **Keys and HTTPS:** keeping `PYWEB_AUTH_SECRET` and `PYWEB_DATA_KEY`
  secret and backed up, and serving over HTTPS (`PYWEB_COOKIE_SECURE=1`).
