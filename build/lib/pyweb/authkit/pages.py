"""The auth kit's routes: sign-in, sign-up, reset, account, passkeys and OAuth.

Every page works without JavaScript (plain forms that POST back to the same
URL); ``auth.js`` adds passkeys where the browser has them.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import urllib.parse

from pyweb.rules import ValidationError

from . import PENDING_2FA, OAUTH_COOKIE, WEBAUTHN_COOKIE, _safe_next, passwords
from . import html as H
from .models import AuthEvent, AuthToken, Credential, Identity, User

FRESH = 15 * 60          # account changes need a sign-in this recent
ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "runtime", "browser")


class Ctx:
    def __init__(self, kit, server, req):
        from pyweb import forms
        self.kit, self.server, self.req = kit, server, req
        self.path, _, qs = req.path.partition("?")
        self.query = {k: v[-1] for k, v in urllib.parse.parse_qs(qs).items()}
        self.method = req.method
        self.headers = {k.lower(): v for k, v in (req.headers or {}).items()}
        self.fields = {}
        self.json = None
        if self.method == "POST":
            ctype = self.headers.get("content-type", "")
            if "json" in ctype:
                try:
                    self.json = json.loads(req.body or b"{}")
                except ValueError:
                    self.json = {}
            else:
                try:
                    raw, _files = forms.parse_body(req)
                except ValueError:
                    raw = {}
                self.fields = {k: v[-1] for k, v in raw.items()}
        self.csrf = forms.csrf_token()

    def get(self, name, default=""):
        return self.fields.get(name, default)

    def csrf_ok(self):
        from pyweb import forms
        if self.headers.get("sec-fetch-site") == "cross-site":
            return False
        origin = self.headers.get("origin")
        host = self.headers.get("x-forwarded-host") or self.headers.get("host")
        if origin and host and origin != "null" and urllib.parse.urlparse(origin).netloc != host:
            return False
        token = self.fields.get("__pw_csrf") or self.headers.get("x-pw-csrf", "")
        return forms.check_csrf(self.req.cookies.get(forms.CSRF_COOKIE), token)


# ------------------------------------------------------------- responses

def _resp(status, body, ctype="text/html; charset=utf-8", extra=None):
    from pyweb.runtime.server import Response
    headers = {"Content-Type": ctype, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
               "X-Robots-Tag": "noindex"}
    headers.update(extra or {})
    return Response(status, body, headers)


def redirect(url):
    from pyweb.runtime.server import Response
    return Response(303, "", {"Location": url, "Cache-Control": "no-store"})


def json_resp(status, data):
    return _resp(status, json.dumps(data), "application/json")


def page(c, title, body, *, status=200, wide=False, lead="", passkeys=False):
    css = list(getattr(c.kit.app, "stylesheets", []) or [])
    scripts = ["/__pyweb/auth/auth.js"] if passkeys else []
    html = H.shell(title, H.card(title, body, wide=wide, lead=lead), site=c.kit.site_name, css=css, scripts=scripts,
                   lang=getattr(c.kit.app, "lang", "en") or "en")
    return _resp(status, html)


def forbidden(c):
    return page(c, "This form expired", H.notice("Reload the page and try again.", "error") +
                '<p><a href="javascript:history.back()">Go back</a></p>', status=403)


def set_cookie(name, value, max_age):
    from pyweb.context import request
    request.set_cookie(name, value, max_age=max_age, same_site="Lax")


# ------------------------------------------------------------------ login

def _next(c):
    return _safe_next(c.fields.get("next") or c.query.get("next") or "", c.kit.after_login)


def login_page(c, *, error="", info="", email="", status=200):
    kit = c.kit
    nxt = c.fields.get("next") or c.query.get("next") or ""
    fresh = c.query.get("fresh") or c.fields.get("fresh")
    body = H.notice(error, "error") + H.notice(info, "ok")
    if fresh:
        body += H.notice("Please sign in again to continue.", "info")
    parts = ""
    if "password" in kit.methods or "magic_link" in kit.methods:
        fields = H.field("email", "Email", type="email", value=email, autocomplete="username webauthn", form="login")
        if "password" in kit.methods:
            fields += H.field("password", "Password", type="password", autocomplete="current-password",
                              required=False, form="login")
        fields += f'<input type="hidden" name="next" value="{H.esc(nxt)}">'
        if "password" in kit.methods:
            fields += H.button("Sign in", name="action", value="password")
        if "magic_link" in kit.methods:
            fields += H.button("Email me a sign-in link", name="action", value="magic", kind="secondary",
                               formnovalidate=True)
        parts += H.form("/login", fields, csrf=c.csrf)
    if "passkey" in kit.methods:
        parts += ('<div data-pw-passkey-only hidden><p class="pw-or">or</p>'
                  f'{H.button("Sign in with a passkey", kind="secondary", data_pw_passkey_login=True, type="button")}'
                  '<p data-pw-passkey-status hidden></p></div>')
    providers = [p for p in kit.providers.values() if p.configured]
    if providers:
        parts += '<p class="pw-or">or</p>'
        q = urllib.parse.urlencode({"next": nxt}) if nxt else ""
        for p in providers:
            parts += (f'<a class="pw-button pw-secondary" href="/auth/{p.name}/start{("?" + q) if q else ""}">'
                      f"Continue with {H.esc(p.label)}</a>")
    links = []
    if kit.signup:
        links.append('<a href="/signup">Create an account</a>')
    if "password" in kit.methods:
        links.append('<a href="/reset">Forgot your password?</a>')
    body += parts + (f'<p class="pw-links">{"".join(links)}</p>' if links else "")
    return page(c, "Sign in", body, status=status, passkeys="passkey" in kit.methods)


def login(c):
    kit = c.kit
    if c.method == "GET":
        if kit.user() is not None and not c.query.get("fresh"):
            return redirect(_next(c))
        return login_page(c)
    if not c.csrf_ok():
        return forbidden(c)
    email = c.get("email").strip()
    if c.get("action") == "magic" and "magic_link" in kit.methods:
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return login_page(c, error="Enter your email to get a sign-in link.", email=email, status=422)
        kit.send_magic_link(email, c.get("next"))
        return page(c, "Check your email", H.notice(
            f"If {email} can sign in here, a link is on its way. It works once, for 15 minutes.", "ok"))
    if "password" not in kit.methods:
        return login_page(c, status=400)
    try:
        user = kit.authenticate(email, c.get("password"))
    except ValidationError as exc:
        return login_page(c, error=exc.errors.get("__all__", "Sign-in failed."), email=email, status=422)
    return _finish_login(c, user, "password", _next(c))


def _finish_login(c, user, method, nxt):
    kit = c.kit
    if kit.two_factor and user.totp_secret:
        set_cookie(PENDING_2FA, kit._sign("2fa", {"uid": user.id, "m": method, "next": nxt}, 300), 300)
        return redirect("/login/2fa")
    kit.login(user, method)
    return redirect(nxt)


def two_factor(c):
    kit = c.kit
    pending = kit._unsign("2fa", c.req.cookies.get(PENDING_2FA))
    if not pending:
        return redirect("/login")
    user = User.get(pending["uid"])
    if user is None or not user.totp_secret:
        return redirect("/login")
    error = ""
    if c.method == "POST":
        if not c.csrf_ok():
            return forbidden(c)
        key = f"2fa:{user.id}"
        wait = kit.throttle.wait(key)
        code = re.sub(r"\s+", "", c.get("code"))
        if wait:
            error = f"Too many attempts. Try again in {wait} seconds."
        elif _check_code(user, code):
            kit.throttle.clear(key)
            set_cookie(PENDING_2FA, "", 0)
            kit.login(user, pending.get("m", "password") + "+2fa")
            return redirect(_safe_next(pending.get("next"), kit.after_login))
        else:
            kit.throttle.fail(key)
            kit.event("2fa_failed", user=user, ok=False)
            error = "That code didn't work."
    body = H.notice(error, "error") + H.form("/login/2fa", H.field(
        "code", "Code from your authenticator app", inputmode="numeric", autocomplete="one-time-code",
        error="", form="tfa", hint="Or one of your recovery codes.") + H.button("Continue"), csrf=c.csrf)
    return page(c, "Two-step sign-in", body, status=422 if error else 200)


def _check_code(user, code):
    from pyweb import auth
    if re.fullmatch(r"\d{6}", code or ""):
        return auth.verify_totp(base64.b32decode(user.totp_secret), code)
    digest = hashlib.sha256((code or "").lower().encode()).hexdigest()
    codes = list(user.recovery_codes or [])
    if digest in codes:
        codes.remove(digest)
        user.recovery_codes = codes
        user.save(validate=False)
        return True
    return False


def logout(c):
    if c.method == "POST":
        if not c.csrf_ok():
            return forbidden(c)
        c.kit.logout()
        return redirect(c.kit.after_logout)
    return page(c, "Sign out", H.form("/logout", H.button("Sign out"), csrf=c.csrf))


# ---------------------------------------------------------------- sign-up

def signup(c):
    kit = c.kit
    if not kit.signup:
        return None
    errors, values = {}, {"name": "", "email": ""}
    if c.method == "POST":
        if not c.csrf_ok():
            return forbidden(c)
        values = {"name": c.get("name").strip(), "email": c.get("email").strip().lower()}
        try:
            if kit.signup == "verify":
                kit.start_signup(values["email"], c.get("password"), values["name"])
                return page(c, "Check your email", H.notice(
                    f"We sent a link to {values['email']} to finish signing up. It works for 24 hours.", "ok"))
            existing = User.where(email=values["email"]).first()
            if existing is not None:
                raise ValidationError({"email": "already has an account; sign in or reset your password"})
            problem = passwords.problem(c.get("password"), email=values["email"], name=values["name"])
            if problem:
                raise ValidationError({"password": problem})
            user = kit.create_user(values["email"], c.get("password"), name=values["name"])
            kit.event("signup", user=user)
            return _finish_login(c, user, "signup", kit.after_login)
        except ValidationError as exc:
            errors = exc.errors
    body = H.form("/signup",
                  H.field("name", "Name", value=values["name"], autocomplete="name", required=False,
                          error=errors.get("name", ""), form="signup") +
                  H.field("email", "Email", type="email", value=values["email"], autocomplete="email",
                          error=errors.get("email", ""), form="signup") +
                  H.field("password", "Password", type="password", autocomplete="new-password",
                          error=errors.get("password", ""), minlength=passwords.MIN_LENGTH, form="signup",
                          hint=f"At least {passwords.MIN_LENGTH} characters. A few random words work well.") +
                  H.button("Create account"), csrf=c.csrf)
    body += '<p class="pw-links"><a href="/login">I already have an account</a></p>'
    return page(c, "Create an account", body, status=422 if errors else 200)


def verify(c, token):
    kit = c.kit
    row = AuthToken.redeem("signup", token)
    if row is None:
        row = AuthToken.redeem("verify", token)
        if row is not None and row.user_id:
            user = User.get(row.user_id)
            user.email_verified = True
            user.save(validate=False)
            kit.event("email_verified", user=user)
            return redirect("/account")
        return page(c, "This link doesn't work", H.notice(
            "It was already used, or it's more than a day old. Sign up again to get a new one.", "error"),
            status=410)
    if User.where(email=row.email).exists():
        return redirect("/login")
    data = row.data or {}
    user = User(email=row.email, name=data.get("name", ""), email_verified=True,
                password_hash=data.get("password_hash"))
    user.save()
    kit.event("signup", user=user)
    kit.login(user, "signup")
    return redirect(kit.after_login)


def magic(c, token):
    kit = c.kit
    if c.method == "GET":
        # Mail scanners open links: only a click on this button signs in.
        body = H.form(f"/magic/{token}", H.button("Sign in"), csrf=c.csrf)
        return page(c, f"Sign in to {kit.site_name}", body)
    if not c.csrf_ok():
        return forbidden(c)
    row = AuthToken.redeem("magic", token)
    if row is None:
        return page(c, "This link doesn't work", H.notice(
            "It was already used or has expired. Ask for a new one on the sign-in page.", "error"), status=410)
    user = User.where(email=row.email).first()
    if user is None:
        if not kit.signup:
            return redirect("/login")
        user = kit.create_user(row.email, verified=True)
        kit.event("signup", user=user, detail="magic link")
    if not user.is_active:
        return redirect("/login")
    if not user.email_verified:
        user.email_verified = True
        user.save(validate=False)
    nxt = _safe_next((row.data or {}).get("next"), kit.after_login)
    return _finish_login(c, user, "magic_link", nxt)


# ------------------------------------------------------------------ reset

def reset(c):
    kit = c.kit
    if c.method == "POST":
        if not c.csrf_ok():
            return forbidden(c)
        kit.send_reset(c.get("email"))
        return page(c, "Check your email", H.notice(
            "If an account uses that email, a link to choose a new password is on its way.", "ok"))
    body = H.form("/reset", H.field("email", "Email", type="email", autocomplete="email", form="reset") +
                  H.button("Send me a link"), csrf=c.csrf)
    return page(c, "Reset your password", body, lead="We'll email you a link to choose a new password.")


def reset_token(c, token):
    kit = c.kit
    digest = AuthToken.digest(token)
    row = AuthToken.where(purpose="reset", token_hash=digest, used_at__isnull=True).first()
    if row is None or row.expires_at < dt.datetime.now(dt.timezone.utc):
        return page(c, "This link doesn't work", H.notice(
            "It was already used or has expired. Ask for a new one.", "error") +
            '<p><a href="/reset">Reset your password</a></p>', status=410)
    error = ""
    if c.method == "POST":
        if not c.csrf_ok():
            return forbidden(c)
        user = User.get(row.user_id)
        problem = passwords.problem(c.get("password"), email=user.email, name=user.name)
        if problem:
            error = problem
        elif AuthToken.redeem("reset", token) is None:
            return redirect("/reset")
        else:
            kit.set_password(user, c.get("password"))        # signs out every other session
            kit.event("password_reset", user=user)
            return _finish_login(c, user, "reset", kit.after_login)
    body = H.form(f"/reset/{token}", H.field(
        "password", "New password", type="password", autocomplete="new-password", error=error,
        minlength=passwords.MIN_LENGTH, form="reset") + H.button("Save and sign in"), csrf=c.csrf)
    return page(c, "Choose a new password", body, status=422 if error else 200,
                lead="You'll be signed out on every other device.")


# ---------------------------------------------------------------- account

def account(c):
    kit = c.kit
    user = kit.user()
    if user is None:
        return redirect("/login?" + urllib.parse.urlencode({"next": "/account"}))
    msg, err, extra = "", "", ""
    if c.method == "POST":
        if not c.csrf_ok():
            return forbidden(c)
        action = c.get("action")
        sensitive = action in ("passkey_remove", "totp_start", "totp_confirm", "totp_disable", "unlink", "email")
        if sensitive and not kit.fresh(FRESH):
            return redirect("/login?" + urllib.parse.urlencode({"next": "/account", "fresh": "1"}))
        try:
            msg, extra = _account_action(c, user, action)
        except ValidationError as exc:
            err = "; ".join((v if k == "__all__" else f"{k.replace('_', ' ').capitalize()} {v}")
                            for k, v in exc.errors.items())
    return page(c, "Your account", _account_body(c, user, msg, err, extra), wide=True,
                passkeys="passkey" in kit.methods, status=422 if err else 200)


def _account_action(c, user, action):
    kit = c.kit
    if action == "profile":
        user.name = c.get("name").strip()
        user.save()
        return "Saved.", ""
    if action == "password":
        if user.password_hash and not passwords.verify_password(c.get("current"), user.password_hash):
            raise ValidationError({"current_password": "is wrong"})
        kit.set_password(user, c.get("new"))
        kit.event("password_changed", user=user)
        kit.login(user, "password_change")             # this device stays signed in
        return "Password changed. Other devices were signed out.", ""
    if action == "revoke":
        kit.revoke(user)
        kit.event("signed_out_everywhere", user=user)
        kit.login(user, "revoke")
        return "Signed out of every other device.", ""
    if action == "passkey_remove":
        cred = Credential.where(id=c.get("id"), user=user).first()
        if cred is not None:
            cred.delete()
            kit.event("passkey_removed", user=user, detail=cred.name)
        return "Passkey removed.", ""
    if action == "unlink":
        ident = Identity.where(id=c.get("id"), user=user).first()
        if ident is not None:
            if not user.password_hash and Identity.where(user=user).count() == 1 and \
                    not Credential.where(user=user).exists():
                raise ValidationError({"__all__": "Add a password or passkey first, or you couldn't sign in."})
            ident.delete()
            kit.event("identity_removed", user=user, detail=ident.provider)
        return "Disconnected.", ""
    if action == "totp_start" and kit.two_factor:
        secret = base64.b32encode(secrets.token_bytes(20)).decode()
        uri = (f"otpauth://totp/{urllib.parse.quote(kit.site_name)}:{urllib.parse.quote(user.email)}?"
               f"secret={secret}&issuer={urllib.parse.quote(kit.site_name)}")
        token = kit._sign("totp", {"s": secret, "uid": user.id}, 600)
        form = H.form("/account", f'<input type="hidden" name="action" value="totp_confirm">'
                      f'<input type="hidden" name="setup" value="{H.esc(token)}">' +
                      H.field("code", "6-digit code from the app", inputmode="numeric", autocomplete="one-time-code",
                              form="totp") + H.button("Turn on"), csrf=c.csrf)
        extra = (f'<h2>Set up your authenticator app</h2><p class="pw-muted">Add this key in the app (Google '
                 f'Authenticator, 1Password, ...), or <a href="{H.esc(uri)}">open it on this device</a>.</p>'
                 f'<p class="pw-code">{" ".join(secret[i:i + 4] for i in range(0, len(secret), 4))}</p>{form}')
        return "", extra
    if action == "totp_confirm" and kit.two_factor:
        from pyweb import auth
        setup = kit._unsign("totp", c.get("setup"))
        if not setup or setup.get("uid") != user.id:
            raise ValidationError({"__all__": "That took too long; start again."})
        if not auth.verify_totp(base64.b32decode(setup["s"]), re.sub(r"\s+", "", c.get("code"))):
            raise ValidationError({"code": "didn't match; check the time on your phone and try again"})
        codes = [secrets.token_hex(5) for _ in range(10)]
        user.totp_secret = setup["s"]
        user.recovery_codes = [hashlib.sha256(code.encode()).hexdigest() for code in codes]
        user.save(validate=False)
        kit.event("2fa_enabled", user=user)
        extra = ("<h2>Recovery codes</h2><p class=\"pw-muted\">Keep these somewhere safe. Each one signs you in once "
                 "if you lose your phone. They won't be shown again.</p>"
                 f'<p class="pw-code">{" ".join(codes)}</p>')
        return "Two-step sign-in is on.", extra
    if action == "totp_disable":
        user.totp_secret = None
        user.recovery_codes = None
        user.save(validate=False)
        kit.event("2fa_disabled", user=user)
        return "Two-step sign-in is off.", ""
    return "", ""


def _account_body(c, user, msg, err, extra):
    kit = c.kit
    out = H.notice(msg, "ok") + H.notice(err, "error") + extra
    out += f'<p class="pw-muted">Signed in as <b>{H.esc(user.email)}</b></p>'
    out += "<h2>Profile</h2>" + H.form("/account", '<input type="hidden" name="action" value="profile">' +
                                       H.field("name", "Name", value=user.name, required=False, form="acct") +
                                       H.button("Save", kind="secondary pw-inline"), csrf=c.csrf)
    if "password" in kit.methods:
        current = H.field("current", "Current password", type="password", autocomplete="current-password",
                          form="pwd") if user.password_hash else ""
        out += "<h2>Password</h2>" + H.form("/account", '<input type="hidden" name="action" value="password">' +
                                            current + H.field("new", "New password", type="password",
                                                              autocomplete="new-password", form="pwd",
                                                              minlength=passwords.MIN_LENGTH) +
                                            H.button("Change password", kind="secondary pw-inline"), csrf=c.csrf)
    if "passkey" in kit.methods:
        rows = "".join(
            f'<tr><td>{H.esc(p.name)}</td><td>{p.created_at:%Y-%m-%d}</td><td>'
            f'{p.last_used_at.strftime("%Y-%m-%d") if p.last_used_at else "never"}</td><td>'
            + H.form("/account", f'<input type="hidden" name="action" value="passkey_remove">'
                     f'<input type="hidden" name="id" value="{p.id}">' + H.button("Remove", kind="danger pw-inline"),
                     csrf=c.csrf) + "</td></tr>"
            for p in Credential.where(user=user).order("id"))
        table = (f'<table class="pw-table"><tr><th>Passkey</th><th>Added</th><th>Last used</th><th></th></tr>{rows}'
                 "</table>") if rows else '<p class="pw-muted">No passkeys yet.</p>'
        out += ("<h2>Passkeys</h2><p class=\"pw-muted\">Sign in with your fingerprint, face or device PIN.</p>" + table +
                '<div data-pw-passkey-only hidden>' + H.field("passkey_name", "Name for the new passkey",
                                                              value="Passkey", required=False, form="pk") +
                H.button("Add a passkey", kind="secondary pw-inline", type="button", data_pw_passkey_add=True) +
                f'<input type="hidden" name="__pw_csrf" value="{H.esc(c.csrf)}">'
                "<p data-pw-passkey-status hidden></p></div>")
    if kit.two_factor:
        if user.totp_secret:
            left = len(user.recovery_codes or [])
            out += (f'<h2>Two-step sign-in</h2><p class="pw-muted">On. {left} recovery code(s) left.</p>' +
                    H.form("/account", '<input type="hidden" name="action" value="totp_disable">' +
                           H.button("Turn off", kind="danger pw-inline"), csrf=c.csrf))
        else:
            out += ('<h2>Two-step sign-in</h2><p class="pw-muted">Ask for a code from an authenticator app '
                    'when signing in with a password.</p>' +
                    H.form("/account", '<input type="hidden" name="action" value="totp_start">' +
                           H.button("Set up", kind="secondary pw-inline"), csrf=c.csrf))
    providers = [p for p in kit.providers.values() if p.configured]
    if providers:
        linked = {i.provider: i for i in Identity.where(user=user)}
        rows = ""
        for p in providers:
            ident = linked.get(p.name)
            if ident:
                action = H.form("/account", f'<input type="hidden" name="action" value="unlink">'
                                f'<input type="hidden" name="id" value="{ident.id}">' +
                                H.button("Disconnect", kind="danger pw-inline"), csrf=c.csrf)
            else:
                action = f'<a href="/auth/{p.name}/start?next=/account">Connect</a>'
            rows += f"<tr><td>{H.esc(p.label)}</td><td>{H.esc(ident.email or '') if ident else ''}</td><td>{action}</td></tr>"
        out += f'<h2>Connected accounts</h2><table class="pw-table">{rows}</table>'
    events = AuthEvent.where(user=user).order("-id").limit(8).all()
    rows = "".join(f"<tr><td>{e.created_at:%Y-%m-%d %H:%M}</td><td>{H.esc(e.kind.replace('_', ' '))}"
                   f"{'' if e.ok else ' (failed)'}</td><td>{H.esc(e.ip or '')}</td></tr>" for e in events)
    out += f'<h2>Recent activity</h2><table class="pw-table">{rows}</table>'
    out += ("<h2>Sessions</h2>" + H.form("/account", '<input type="hidden" name="action" value="revoke">' +
                                        H.button("Sign out of every other device", kind="secondary pw-inline"),
                                        csrf=c.csrf))
    out += H.form("/logout", H.button("Sign out", kind="secondary pw-inline"), csrf=c.csrf)
    if kit.admin_enabled and user.has_role("admin"):
        out += '<p class="pw-links"><a href="/admin">Admin</a></p>'
    return out


# ------------------------------------------------------------------ OAuth

def oauth_start(c, provider):
    kit = c.kit
    prov = kit.providers.get(provider)
    if prov is None or not prov.configured:
        return None
    url, state, verifier = prov.start(kit._origin() + f"/auth/{provider}/callback")
    user = kit.user()
    data = {"s": state, "v": verifier, "p": provider, "next": c.query.get("next", ""),
            "link": user.id if user is not None else None}
    set_cookie(OAUTH_COOKIE, kit._sign("oauth", data, 600), 600)
    return redirect(url)


def oauth_callback(c, provider):
    kit = c.kit
    prov = kit.providers.get(provider)
    if prov is None or not prov.configured:
        return None
    data = kit._unsign("oauth", c.req.cookies.get(OAUTH_COOKIE))
    set_cookie(OAUTH_COOKIE, "", 0)

    def fail(text):
        kit.event("oauth_failed", ok=False, detail=f"{provider}: {text}")
        return page(c, "Couldn't sign you in", H.notice(text, "error") + '<p><a href="/login">Back to sign in</a></p>',
                    status=400)

    if not data or data.get("p") != provider or not c.query.get("state") or \
            not secrets.compare_digest(str(data.get("s")), c.query.get("state", "")):
        return fail("This sign-in attempt expired. Please try again.")
    if c.query.get("error"):
        return fail(f"{prov.label} said: {c.query.get('error_description') or c.query['error']}")
    try:
        info = prov.finish(c.query.get("code", ""), kit._origin() + f"/auth/{provider}/callback", data["v"])
    except Exception as exc:  # noqa: BLE001 - network or provider errors
        return fail(f"{prov.label} didn't answer as expected ({exc}).")
    subject = info.get("subject")
    if not subject or subject == "None":
        return fail(f"{prov.label} didn't say who you are.")
    nxt = _safe_next(data.get("next"), kit.after_login)
    ident = Identity.where(provider=provider, subject=subject).first()
    current = kit.user()
    if data.get("link"):
        if current is None or current.id != data["link"]:
            return fail("Sign in again, then connect the account.")
        if ident is not None and ident.user_id != current.id:
            return fail(f"That {prov.label} account is connected to someone else.")
        if ident is None:
            Identity.create(user=current, provider=provider, subject=subject, email=info.get("email"))
            kit.event("identity_added", user=current, detail=provider)
        return redirect(nxt or "/account")
    if ident is not None:
        user = User.get(ident.user_id)
        if user is None or not user.is_active:
            return fail("This account is closed.")
        return _finish_login(c, user, provider, nxt)
    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified"):
        return fail(f"Your {prov.label} account has no verified email address, so we can't create an account "
                    "from it. Verify an email there, or sign up with your email here.")
    if User.where(email=email).exists():
        # Never attach a new sign-in method to an existing account without its owner signing in.
        return fail(f"An account with {email} already exists. Sign in with it first, then connect "
                    f"{prov.label} on your account page.")
    if not kit.signup:
        return fail("New accounts can't be created here.")
    user = kit.create_user(email, name=info.get("name") or "", verified=True)
    Identity.create(user=user, provider=provider, subject=subject, email=email)
    kit.event("signup", user=user, detail=provider)
    return _finish_login(c, user, provider, nxt)


# --------------------------------------------------------------- passkeys

def _challenge(c, kind, uid=None):
    # The challenge is a one-time token row, so it can be used once on any server, even by
    # someone replaying a captured cookie and response (synced passkeys keep a zero counter,
    # so the counter check alone wouldn't stop that). The cookie ties it to this browser.
    challenge = AuthToken.issue("webauthn", ttl=300, data={"k": kind, "u": uid})
    set_cookie(WEBAUTHN_COOKIE, c.kit._sign("webauthn", {"c": challenge, "k": kind, "u": uid}, 300), 300)
    return challenge


def _take_challenge(c, kind):
    data = c.kit._unsign("webauthn", c.req.cookies.get(WEBAUTHN_COOKIE))
    set_cookie(WEBAUTHN_COOKIE, "", 0)                    # one ceremony per challenge
    if not data or data.get("k") != kind:
        return None
    if AuthToken.redeem("webauthn", data.get("c")) is None:
        return None                                       # used already, or expired
    return data


def passkey_register_options(c):
    kit = c.kit
    user = kit.user()
    if user is None:
        return json_resp(401, {"error": "Sign in first."})
    if not kit.fresh(FRESH):
        return json_resp(401, {"error": "Please sign in again before adding a passkey."})
    existing = [{"type": "public-key", "id": cr.credential_id} for cr in Credential.where(user=user)]
    return json_resp(200, {"publicKey": {
        "challenge": _challenge(c, "register", user.id),
        "rp": {"name": kit.site_name, "id": kit._rp_id()},
        "user": {"id": base64.urlsafe_b64encode(str(user.id).encode()).decode().rstrip("="),
                 "name": user.email, "displayName": user.name or user.email},
        "pubKeyCredParams": [{"type": "public-key", "alg": -7}, {"type": "public-key", "alg": -257}],
        "authenticatorSelection": {"residentKey": "required", "userVerification": "preferred"},
        "attestation": "none", "timeout": 120000, "excludeCredentials": existing}})


def passkey_register(c):
    from pyweb.auth import AuthError
    from . import webauthn
    kit = c.kit
    user = kit.user()
    data = _take_challenge(c, "register")
    if user is None or data is None or data.get("u") != user.id:
        return json_resp(400, {"error": "That took too long. Try again."})
    body = c.json or {}
    try:
        info = webauthn.verify_registration(
            attestation_object=body.get("attestationObject", ""), client_data_json=body.get("clientDataJSON", ""),
            rp_id=kit._rp_id(), challenge=data["c"], origins=[kit._origin()])
    except (AuthError, ValueError, KeyError, TypeError) as exc:
        return json_resp(400, {"error": f"The passkey couldn't be added ({exc})."})
    if Credential.where(credential_id=info["credential_id"]).exists():
        return json_resp(400, {"error": "That passkey is already added."})
    name = (body.get("name") or "Passkey").strip()[:80] or "Passkey"
    Credential.create(user=user, credential_id=info["credential_id"], public_key=info["public_key"],
                      algorithm=info["algorithm"], sign_count=info["sign_count"], name=name)
    kit.event("passkey_added", user=user, detail=name)
    return json_resp(200, {"ok": True})


def passkey_login_options(c):
    return json_resp(200, {"publicKey": {"challenge": _challenge(c, "login"), "rpId": c.kit._rp_id(),
                                         "userVerification": "preferred", "timeout": 120000,
                                         "allowCredentials": []}})


def passkey_login(c):
    from pyweb.auth import AuthError
    from . import webauthn
    kit = c.kit
    data = _take_challenge(c, "login")
    if data is None:
        return json_resp(400, {"error": "That took too long. Try again."})
    body = c.json or {}
    cred = Credential.where(credential_id=str(body.get("id") or "")).first()
    if cred is None:
        kit.event("passkey_failed", ok=False, detail="unknown passkey")
        return json_resp(400, {"error": "This passkey isn't registered here."})
    try:
        count = webauthn.verify_assertion(
            public_key=cred.public_key, authenticator_data=body.get("authenticatorData", ""),
            client_data_json=body.get("clientDataJSON", ""), signature=body.get("signature", ""),
            rp_id=kit._rp_id(), challenge=data["c"], origins=[kit._origin()], prior_sign_count=cred.sign_count)
    except (AuthError, ValueError, KeyError, TypeError) as exc:
        kit.event("passkey_failed", ok=False, detail=str(exc))
        return json_resp(400, {"error": "The passkey didn't check out."})
    user = User.get(cred.user_id)
    if user is None or not user.is_active:
        return json_resp(403, {"error": "This account is closed."})
    cred.sign_count = count
    cred.last_used_at = dt.datetime.now(dt.timezone.utc)
    cred.save(validate=False)
    kit.login(user, "passkey")            # a passkey is already two factors (device + biometric/PIN)
    return json_resp(200, {"ok": True, "redirect": _safe_next(body.get("next"), kit.after_login)})


# ---------------------------------------------------------------- routing

ROUTES = [
    (re.compile(r"^/login$"), login, ("GET", "POST")),
    (re.compile(r"^/login/2fa$"), two_factor, ("GET", "POST")),
    (re.compile(r"^/logout$"), logout, ("GET", "POST")),
    (re.compile(r"^/signup$"), signup, ("GET", "POST")),
    (re.compile(r"^/verify/(?P<token>[\w-]{20,100})$"), verify, ("GET",)),
    (re.compile(r"^/magic/(?P<token>[\w-]{20,100})$"), magic, ("GET", "POST")),
    (re.compile(r"^/reset$"), reset, ("GET", "POST")),
    (re.compile(r"^/reset/(?P<token>[\w-]{20,100})$"), reset_token, ("GET", "POST")),
    (re.compile(r"^/account$"), account, ("GET", "POST")),
    (re.compile(r"^/auth/(?P<provider>\w+)/start$"), oauth_start, ("GET",)),
    (re.compile(r"^/auth/(?P<provider>\w+)/callback$"), oauth_callback, ("GET",)),
]
JSON_ROUTES = {
    "/auth/passkey/register/options": passkey_register_options,
    "/auth/passkey/register": passkey_register,
    "/auth/passkey/login/options": passkey_login_options,
    "/auth/passkey/login": passkey_login,
}


def dispatch(kit, server, req):
    path = req.path.split("?")[0]
    if path == "/__pyweb/auth/auth.css":
        return _resp(200, H.CSS, "text/css; charset=utf-8", {"Cache-Control": "public, max-age=3600"})
    if path == "/__pyweb/auth/auth.js":
        with open(os.path.join(ASSET_DIR, "auth.js"), encoding="utf-8") as fh:
            return _resp(200, fh.read(), "text/javascript; charset=utf-8", {"Cache-Control": "public, max-age=3600"})
    if path in JSON_ROUTES:
        if "passkey" not in kit.methods:
            return None
        if req.method != "POST":
            return json_resp(405, {"error": "POST only"})
        c = Ctx(kit, server, req)
        if not c.csrf_ok():
            return json_resp(403, {"error": "This page expired. Reload it and try again."})
        return JSON_ROUTES[path](c)
    if path == "/admin" or path.startswith("/admin/"):
        if not kit.admin_enabled:
            return None
        from . import admin
        return admin.dispatch(kit, server, req)
    for pattern, handler, methods in ROUTES:
        m = pattern.match(path)
        if not m:
            continue
        if req.method not in methods and not (req.method == "HEAD" and "GET" in methods):
            return _resp(405, "method not allowed", "text/plain", {"Allow": ", ".join(methods)})
        c = Ctx(kit, server, req)
        if c.method == "HEAD":
            c.method = "GET"
        return handler(c, **m.groupdict())
    return None
