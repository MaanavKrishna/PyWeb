"""Jobs PyWeb itself queues: email delivery and daily cleanup."""

from __future__ import annotations

import logging
import os
import time

from .core import backend, cron, job

log = logging.getLogger("pyweb.jobs")


@job(name="pyweb.mail.deliver", queue="mail", retries=8, timeout=60)
def deliver(message):
    """Send one email (queued by ``pyweb.mail.send``; retried if the mail server fails)."""
    from pyweb import mail
    mail.deliver_now(mail.Message.from_dict(message))


def _days(name, default):
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except ValueError:
        return default


@cron("17 3 * * *", name="pyweb.cleanup", retries=2, timeout=600)
def cleanup():
    """Every night: forget finished jobs (``PYWEB_JOBS_KEEP_DAYS``, 7), expired sign-in links,
    and audit log entries older than ``PYWEB_AUDIT_DAYS`` (365)."""
    now = time.time()
    done = {"jobs": backend().purge(now - _days("PYWEB_JOBS_KEEP_DAYS", 7) * 86400)}
    try:
        from pyweb import models as M
        from pyweb.authkit.models import AuthEvent, AuthToken
        import datetime as dt
        if M.database() is not None:
            from pyweb.db import schema as S
            tables = set(S.table_names(M.database()))
            utc = dt.timezone.utc
            if "auth_tokens" in tables:
                cutoff = dt.datetime.now(utc) - dt.timedelta(days=1)
                done["auth_tokens"] = AuthToken.where(AuthToken.expires_at < cutoff).delete()
            if "auth_events" in tables:
                cutoff = dt.datetime.now(utc) - dt.timedelta(days=_days("PYWEB_AUDIT_DAYS", 365))
                done["auth_events"] = AuthEvent.where(AuthEvent.created_at < cutoff).delete()
    except Exception:  # noqa: BLE001 - cleanup is best effort
        log.exception("cleanup of auth tables failed")
    log.info("cleanup: %s", done)
    return done
