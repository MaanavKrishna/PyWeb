# 12 - Production services (DB, realtime, jobs)

> Sources: `pyweb/db/` (drivers + pool), `pyweb/realtime.py`
> (`RedisBus`), `pyweb/jobs.py` (`RedisQueue`), `pyweb/auth.py`
> (Policy + WebAuthn). Tested in `test_db_production.py`,
> `test_backplane.py`, `test_auth_v1.py`.

## Database: pool once, stream much

```python
db = connect("postgres://app:secret@db:5432/app")  # or mysql://, sqlite:
rows = db.execute("SELECT * FROM users WHERE active = %s", (True,))
for chunk in db.stream("SELECT * FROM events ORDER BY id", size=1000):
    ...
```

- Shared pool base: connections always returned via `finally`
  (a leaked checkout once deadlocked the suite — see BUGLOG).
- Prepared-statement cache, streaming cursors, transient-error
  retries with backoff. `psycopg` absent → guarded import path.

## Realtime: Redis or degrade

`RedisBus` = streams (history + resume) + pub/sub (fanout). No Redis
reachable → in-process degrade, same API. `realtime()` decorator marks
subscription functions the compiler wires to rooms/channels.

## Jobs: persisted, resumable

`Queue.save/load` round-trips job state; `RedisQueue` drains
cross-process so a worker restart never loses queued work.

## Auth: policy + rotation + WebAuthn

```python
Policy("owner-or-admin").allows(user, resource)  # RBAC with owner rules
rotate_session(request)                          # session rotation
verify_webauthn_assertion(...)                   # stdlib ES256, no deps
```

P-256 constants are verified against `openssl ecparam` output
(a missing Gy digit once broke the curve — see BUGLOG); COSE/CBOR
parsing handles negative ints (COSE keys `-1`, `-2`, `-3`, `-7`).
Session cookies are `HttpOnly; Secure; SameSite=Lax`; CSRF tokens gate
mutations; rate limits bound brute force.
