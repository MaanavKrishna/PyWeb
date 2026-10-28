"""What ``pyweb check --production`` (and ``pyweb doctor``, and the MCP security check) report about an
app's production setup: secrets, cookies, proxies, keys, and how its processes fit together."""

from __future__ import annotations

import os
import re


def production_problems(source="", app_dir="."):
    """What would make this app unsafe or unreliable in production, from the environment and the app."""
    from pyweb import config
    out = []
    try:
        s = config.settings()
    except ValueError as exc:
        return [str(exc)]
    if not s.auth_secret:
        out.append("PYWEB_AUTH_SECRET isn't set: sessions would use a development key "
                   "(generate one: python -c \"import secrets; print(secrets.token_hex(32))\")")
    elif len(s.auth_secret) < 32:
        out.append("PYWEB_AUTH_SECRET is shorter than 32 characters; use a long random value")
    if not s.production:
        out.append("PYWEB_ENV isn't 'production' (it makes a missing secret an error instead of a fallback)")
    if not s.cookie_secure:
        out.append("PYWEB_COOKIE_SECURE isn't on: cookies would also travel over plain HTTP "
                   "(set it when you serve over HTTPS, which you should)")
    if not s.trust_proxy:
        out.append("PYWEB_TRUST_PROXY isn't set: behind nginx, Caddy or a load balancer every visitor "
                   "looks like the proxy to rate limits (set it to 1, or the number of proxies; "
                   "ignore this if clients connect directly)")
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1") or 1)
    except ValueError:
        workers = 1
    if workers > 1 and not s.redis_url:
        out.append(f"WEB_CONCURRENCY={workers} without PYWEB_REDIS_URL: each worker would keep its own rate "
                   "limits and live updates (connect Redis to share them)")
    if "use_auth(" in (source or "") and not os.environ.get("PYWEB_ORIGIN") and "origin=" not in source:
        out.append("app.use_auth() without PYWEB_ORIGIN: password-reset and sign-in emails need the site's "
                   "public address (set PYWEB_ORIGIN=https://example.com)")
    if re.search(r"\b(encrypted|searchable)\s*=\s*True", source or ""):
        from pyweb import fieldcrypt
        key = os.environ.get("PYWEB_DATA_KEY", "")
        if not key:
            out.append("the app has encrypted fields but PYWEB_DATA_KEY isn't set (generate one: python -c "
                       "\"import secrets; print(secrets.token_urlsafe(32))\"; keep it safe: without it the data "
                       "can't be read)")
        elif len(key) < 32:
            out.append("PYWEB_DATA_KEY is shorter than 32 characters; use a long random value")
        elif key == os.environ.get("PYWEB_AUTH_SECRET"):
            out.append("PYWEB_DATA_KEY is the same as PYWEB_AUTH_SECRET: keep them apart, so rotating the "
                       "session secret never touches stored data")
        if not fieldcrypt.available():
            out.append('the app has encrypted fields but the cryptography package isn\'t installed '
                       '(pip install "pyweb-stack[crypto]")')
    if re.search(r"\bpwa\s*=\s*(True|PWA\()", source or "") and not re.search(r"\bpush\s*=\s*False", source or ""):
        if not os.environ.get("PYWEB_VAPID_KEY"):
            out.append("the app is installable (pwa=) but PYWEB_VAPID_KEY isn't set: push subscriptions would be "
                       "tied to PYWEB_AUTH_SECRET and lost when it's rotated (make one: pyweb push key)")
        if not os.environ.get("PYWEB_PUSH_CONTACT"):
            out.append("PYWEB_PUSH_CONTACT isn't set: push services (Apple's in particular) want a mailto: or "
                       "https: contact for your app's notifications")
        try:
            from pyweb.push import _crypto
            _crypto()
        except Exception:  # noqa: BLE001 - PushError: the package is missing
            out.append('push notifications need the cryptography package (pip install "pyweb-stack[push]")')
    if "allow_pickle=True" in (source or ""):
        out.append("RedisCache(allow_pickle=True): anyone who can write to Redis could run code; store JSON")
    out += _topology_problems(app_dir)
    if os.path.isfile(os.path.join(app_dir, "pyweb.lock")):
        from pyweb import packages
        for path, why in packages.verify(app_dir)[:5]:
            out.append(f"{path}: {why} (run pyweb add to reinstall)")
    return out


def _topology_problems(app_dir):
    """What the app uses that production must provide (from the same reading `pyweb deploy` does)."""
    from pyweb.deploy import read_app
    app_file = os.path.join(app_dir, "app.pyweb")
    if not os.path.isfile(app_file):
        return []
    facts = read_app(app_file)
    out = []
    env = os.environ
    if facts.models and not facts.migrations:
        out.append("the app has Models but no migrations/: production doesn't create tables by itself "
                   "(run `pyweb db diff --name initial` and commit migrations/)")
    if facts.migrations and (env.get("DATABASE_URL") or facts.database in ("sqlite", "postgres", "mysql")):
        try:
            from pyweb.db import connect
            from pyweb.db import migrate as _mig
            url = env.get("DATABASE_URL")
            if url:
                waiting = _mig.pending(connect(url), os.path.join(app_dir, "migrations"))
                if waiting:
                    out.append(f"{len(waiting)} migration(s) not applied yet: "
                               f"{', '.join(m.label for m in waiting[:5])} (pyweb db upgrade)")
        except Exception as exc:  # noqa: BLE001 - can't reach the database from here: say so, don't fail
            out.append(f"couldn't check migrations against DATABASE_URL ({type(exc).__name__}: {exc})")
    if facts.mail and not env.get("PYWEB_MAIL_URL"):
        out.append("the app sends email (sign-in links, resets) but PYWEB_MAIL_URL isn't set")
    if facts.uploads and not env.get("PYWEB_STORAGE", "").startswith("s3"):
        out.append("the app accepts uploads but PYWEB_STORAGE isn't object storage (s3://...): files on a "
                   "container's disk vanish on redeploy (ignore this with a persistent volume)")
    if facts.jobs and env.get("PYWEB_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
        out.append("PYWEB_WORKER=0: make sure `pyweb worker` processes run, or queued jobs will wait forever "
                   "(this is a reminder; ignore it if they do)")
    return out
