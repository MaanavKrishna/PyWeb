"""Sending email.

``PYWEB_MAIL_URL`` chooses how:

* not set (while developing): nothing is sent. Messages are printed to the
  server log and kept in :data:`OUTBOX` (the last 50), so sign-up and
  password-reset links can be followed from the terminal and checked in tests.
* ``smtp://user:password@host:587`` (STARTTLS) or ``smtps://...:465``.

``PYWEB_MAIL_FROM`` is the sender (``Acme <no-reply@acme.dev>``).

With durable jobs, :func:`send` queues the email (the "outbox"): it leaves
only if the request's database writes commit, and a failing mail server is
retried with backoff (job ``pyweb.mail.deliver``, queue ``mail``).

In production with no ``PYWEB_MAIL_URL``, sending raises: a password reset
that silently goes nowhere is worse than an error.
"""

from __future__ import annotations

import collections
import logging
import os
import smtplib
import ssl
import threading
import urllib.parse
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

log = logging.getLogger("pyweb.mail")

#: Messages "sent" while developing (newest last).
OUTBOX: collections.deque = collections.deque(maxlen=50)
_lock = threading.Lock()
_sender = None


class Message:
    def __init__(self, to, subject, text, html=None, sender=None, reply_to=None):
        self.to = [to] if isinstance(to, str) else list(to)
        self.subject = subject
        self.text = text
        self.html = html
        self.sender = sender or os.environ.get("PYWEB_MAIL_FROM") or "PyWeb <no-reply@localhost>"
        self.reply_to = reply_to

    def build(self):
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.to)
        msg["Subject"] = self.subject
        msg["Message-ID"] = make_msgid()
        if self.reply_to:
            msg["Reply-To"] = self.reply_to
        msg.set_content(self.text)
        if self.html:
            msg.add_alternative(self.html, subtype="html")
        return msg

    def __repr__(self):
        return f"<Message to={self.to} subject={self.subject!r}>"

    def to_dict(self):
        return {"to": self.to, "subject": self.subject, "text": self.text, "html": self.html,
                "sender": self.sender, "reply_to": self.reply_to}

    @classmethod
    def from_dict(cls, d):
        return cls(d["to"], d["subject"], d["text"], d.get("html"), d.get("sender"), d.get("reply_to"))


class ConsoleSender:
    """Development: log the message and keep it in OUTBOX."""

    def send(self, message):
        with _lock:
            OUTBOX.append(message)
        log.warning("email to %s: %s\n%s", ", ".join(message.to), message.subject, message.text)


class SMTPSender:
    def __init__(self, url):
        u = urllib.parse.urlparse(url)
        self.host = u.hostname or "localhost"
        self.ssl = u.scheme == "smtps"
        self.port = u.port or (465 if self.ssl else 587)
        self.user = urllib.parse.unquote(u.username or "")
        self.password = urllib.parse.unquote(u.password or "")

    def send(self, message):
        context = ssl.create_default_context()
        if self.ssl:
            server = smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=30)
        try:
            if not self.ssl:
                server.starttls(context=context)
            if self.user:
                server.login(self.user, self.password)
            server.send_message(message.build())
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass


def sender():
    global _sender
    if _sender is not None:
        return _sender
    url = os.environ.get("PYWEB_MAIL_URL")
    if url:
        return SMTPSender(url)
    if os.environ.get("PYWEB_ENV", "").lower() == "production":
        raise RuntimeError("set PYWEB_MAIL_URL (e.g. smtp://user:pass@smtp.example.com:587) to send email")
    return ConsoleSender()


def use_sender(obj):
    """Send with ``obj`` (anything with ``.send(message)``); None restores the default."""
    global _sender
    _sender = obj


def outbox_enabled():
    """Whether emails go through the job queue: ``PYWEB_MAIL_OUTBOX=1``/``0``, or by default when a
    worker runs in this process or workers run elsewhere (``PYWEB_WORKER=0``), with a durable store."""
    flag = os.environ.get("PYWEB_MAIL_OUTBOX", "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    from . import jobs
    from .jobs import worker as _worker
    if flag not in ("1", "true", "yes", "on") and not (
            _worker.local_worker_running() or not _worker.worker_enabled()):
        return False
    try:
        return jobs.durable()
    except Exception:  # noqa: BLE001
        return False


def send(to, subject, text, *, html=None, sender_address=None, reply_to=None):
    """Send an email. Returns the :class:`Message`.

    With durable jobs (a database or Redis, and a worker), the email is queued
    as a job: it's sent only if the current request's writes commit, and
    retried if the mail server is down. Otherwise it's sent now.
    """
    message = Message(to, subject, text, html, sender_address, reply_to)
    current = sender()
    if not isinstance(current, ConsoleSender) and outbox_enabled():
        from .jobs.builtin import deliver
        deliver.enqueue(message.to_dict())
        return message
    current.send(message)
    return message


def deliver_now(message):
    """Send ``message`` with the configured sender right away (what the outbox job does)."""
    sender().send(message)
    return message


def address(name, email):
    return formataddr((name, email))
