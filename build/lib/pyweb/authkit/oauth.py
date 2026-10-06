"""Signing in with GitHub, Google or Microsoft (OAuth 2 authorization code + PKCE).

Each provider needs a client id and secret, read from
``PYWEB_OAUTH_<PROVIDER>_ID`` / ``PYWEB_OAUTH_<PROVIDER>_SECRET`` unless
passed to ``use_auth(providers={...})``. The callback URL to register with
the provider is ``https://your.site/auth/<provider>/callback``.

Only emails the provider says are verified are used, and a sign-in never
quietly attaches itself to an existing password account (see the kit).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.parse
import urllib.request

PROVIDERS = {
    "github": {
        "label": "GitHub",
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "scope": "read:user user:email",
    },
    "google": {
        "label": "Google",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
    },
    "microsoft": {
        "label": "Microsoft",
        "authorize": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo": "https://graph.microsoft.com/oidc/userinfo",
        "scope": "openid email profile",
    },
}


class Provider:
    def __init__(self, name, client_id=None, client_secret=None, **overrides):
        if name not in PROVIDERS and not overrides.get("authorize"):
            raise ValueError(f"unknown sign-in provider {name!r} (known: {', '.join(PROVIDERS)})")
        self.name = name
        conf = dict(PROVIDERS.get(name, {}), **overrides)
        self.label = conf.get("label", name.title())
        self.authorize_endpoint = conf["authorize"]
        self.token_endpoint = conf["token"]
        self.userinfo_endpoint = conf.get("userinfo")
        self.scope = conf.get("scope", "openid email profile")
        env = name.upper()
        self.client_id = client_id or os.environ.get(f"PYWEB_OAUTH_{env}_ID", "")
        self.client_secret = client_secret or os.environ.get(f"PYWEB_OAUTH_{env}_SECRET", "")
        self.http = _http_json

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    def start(self, redirect_uri):
        """``(url, state, verifier)``: send the visitor to ``url``; keep state and verifier."""
        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params = {"client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
                  "scope": self.scope, "state": state, "code_challenge": challenge,
                  "code_challenge_method": "S256"}
        if self.name == "google":
            params["prompt"] = "select_account"
        return f"{self.authorize_endpoint}?{urllib.parse.urlencode(params)}", state, verifier

    def finish(self, code, redirect_uri, verifier):
        """Exchange the code; ``{"subject", "email", "email_verified", "name"}``."""
        token = self.http(self.token_endpoint, data={
            "client_id": self.client_id, "client_secret": self.client_secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code_verifier": verifier})
        access = token.get("access_token") if isinstance(token, dict) else None
        if not access:
            raise ValueError(f"{self.label} didn't sign you in ({(token or {}).get('error', 'no token')})")
        if self.name == "github":
            user = self.http("https://api.github.com/user", token=access)
            emails = self.http("https://api.github.com/user/emails", token=access) or []
            primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
            return {"subject": str(user.get("id")), "email": primary["email"] if primary else None,
                    "email_verified": primary is not None, "name": user.get("name") or user.get("login") or ""}
        info = self.http(self.userinfo_endpoint, token=access)
        verified = info.get("email_verified")
        if self.name == "microsoft" and verified is None:
            verified = False          # Microsoft doesn't always say; treat as unverified
        return {"subject": str(info.get("sub")), "email": info.get("email"),
                "email_verified": bool(verified), "name": info.get("name") or ""}


def _http_json(url, *, data=None, token=None, timeout=10):
    headers = {"Accept": "application/json", "User-Agent": "pyweb"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - provider endpoints
        return json.loads(resp.read().decode() or "{}")
