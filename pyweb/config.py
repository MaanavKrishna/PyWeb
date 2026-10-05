"""Settings from the environment, in one place.

Every ``PYWEB_*`` variable the servers read is parsed and checked here.
``settings()`` reads the environment each time it's called (cheap), so a
changed variable takes effect without restarting tests or tools; a bad
value raises ``ValueError`` naming the variable.

=============================  ===========  =====================================================
Variable                       Default      Meaning
=============================  ===========  =====================================================
``PYWEB_ENV``                  development  ``production`` requires ``PYWEB_AUTH_SECRET``
``PYWEB_AUTH_SECRET``          (dev key)    root signing secret (sessions, feeds, live queries)
``PYWEB_AUTH_SECRET_PREVIOUS`` (none)       old secrets still accepted, comma-separated
``PYWEB_CSRF_SECRET``          (none)       token CSRF checks for ``@auth_required`` RPCs
``PYWEB_COOKIE_SECURE``        off          ``Secure`` cookies and HSTS (set behind HTTPS)
``PYWEB_CSP``                  built in     Content-Security-Policy for pages
``PYWEB_TRUST_PROXY``          0            reverse proxies in front (for ``X-Forwarded-For``)
``PYWEB_REDIS_URL``            (none)       share rate limits and live updates across servers
``PYWEB_MAX_CONNECTIONS``      256          simultaneous connections (``pyweb serve``)
``PYWEB_SOCKET_TIMEOUT``       30           seconds before a stalled client is dropped
``PYWEB_MAX_STREAMS_PER_CLIENT`` 20         open live connections per client address
``PYWEB_RENDER_TIMEOUT``       30           seconds a page may take to render (504 after)
=============================  ===========  =====================================================
"""

from __future__ import annotations

import dataclasses
import os

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("", "0", "false", "no", "off")


def _bool(name, env):
    raw = env.get(name, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ValueError(f"{name}={env[name]!r}: use 1/true or 0/false")


def _int(name, env, default, minimum=1):
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} isn't a whole number") from None
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _proxies(env):
    raw = env.get("PYWEB_TRUST_PROXY", "").strip().lower()
    if raw in _FALSE:
        return 0
    if raw in _TRUE:
        return 1
    return _int("PYWEB_TRUST_PROXY", env, 0, minimum=0)


@dataclasses.dataclass(frozen=True)
class Settings:
    env: str = "development"
    auth_secret: str | None = None
    previous_secrets: tuple = ()
    csrf_secret: str | None = None
    cookie_secure: bool = False
    csp: str | None = None
    trust_proxy: int = 0
    redis_url: str | None = None
    max_connections: int = 256
    socket_timeout: int = 30
    max_streams_per_client: int = 20
    render_timeout: int = 30

    @property
    def production(self):
        return self.env == "production"

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(
            env=env.get("PYWEB_ENV", "development").strip() or "development",
            auth_secret=env.get("PYWEB_AUTH_SECRET") or None,
            previous_secrets=tuple(s.strip() for s in env.get("PYWEB_AUTH_SECRET_PREVIOUS", "").split(",")
                                   if s.strip()),
            csrf_secret=env.get("PYWEB_CSRF_SECRET") or None,
            cookie_secure=_bool("PYWEB_COOKIE_SECURE", env),
            csp=env.get("PYWEB_CSP") or None,
            trust_proxy=_proxies(env),
            redis_url=env.get("PYWEB_REDIS_URL") or None,
            max_connections=_int("PYWEB_MAX_CONNECTIONS", env, 256),
            socket_timeout=_int("PYWEB_SOCKET_TIMEOUT", env, 30),
            max_streams_per_client=_int("PYWEB_MAX_STREAMS_PER_CLIENT", env, 20),
            render_timeout=_int("PYWEB_RENDER_TIMEOUT", env, 30),
        )


def settings(env=None) -> Settings:
    """The current settings (read from the environment now)."""
    return Settings.from_env(env)


def startup():
    """Validate settings when a server starts (a bad value stops it with a clear error) and
    connect shared services: with ``PYWEB_REDIS_URL``, live updates go through Redis so
    every server process sees them."""
    current = settings()
    if current.redis_url:
        from . import realtime
        if type(realtime.current_bus()) is realtime.Bus:
            try:
                realtime.use_bus(realtime.RedisBus(current.redis_url))
            except ImportError:
                pass
    return current
