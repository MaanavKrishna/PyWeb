"""Content-Security-Policy for pages: PyWeb's default policy and the per-page hashes it adds.

Kept apart from ``serve`` and ``hosting`` (both use it) so neither has to import the other.
"""

from __future__ import annotations

import os
import re

#: Content-Security-Policy for HTML responses (override with PYWEB_CSP).
#: Scripts only from this origin; the page-state JSON block is data, not code.
# No inline script ever runs; inline <style> blocks only by their hash (csp_for), so injected
# markup can't add a stylesheet (CSS can leak a page's secrets). style="..." attributes stay
# allowed: markup's style={...} and components use them.
DEFAULT_CSP = ("default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'self'; "
               "frame-ancestors 'self'; form-action 'self'; img-src 'self' data: https:; "
               "style-src 'self' https:; style-src-attr 'unsafe-inline'; font-src 'self' data: https:; "
               "connect-src 'self'; worker-src 'self' blob:; "
               "require-trusted-types-for 'script'; trusted-types pyweb-md pyweb-nav pyweb-sw; "
               "report-uri /__pyweb/csp-report")
# Trusted Types: the browser refuses strings in HTML/script sinks (innerHTML, DOMParser, script
# text, ...) unless one of PyWeb's two policies made them. So even a bug that passed visitor
# text to such a sink couldn't run script. Pages that use npm packages don't get it (csp_for):
# many libraries write innerHTML themselves.
# Violations are reported with report-uri only: with report-to as well, Chrome ignores
# report-uri and queues reports for the Reporting API, which may send them minutes later or,
# over plain HTTP, never.
_TRUSTED_TYPES = re.compile(r"\s*(require-trusted-types-for|trusted-types)\b[^;]*;?")


def csp_is_default(csp):
    """True for PyWeb's own policy (Trusted Types are only switched off on a policy PyWeb wrote)."""
    return "trusted-types pyweb-md pyweb-nav" in csp and "PYWEB_CSP" not in os.environ


def csp_for(csp, html):
    """``csp`` allowing the page's own inline import map (npm packages) and ``<style>`` blocks by
    their hashes: exactly those, so anything injected into the page still doesn't run."""
    import base64
    import hashlib

    def sha(data):
        return "'sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode() + "'"

    def add(policy, directive, hashes):
        if not hashes or not re.search(rf"(^|;)\s*{directive}\s", policy):
            return policy
        if "'unsafe-inline'" in re.search(rf"{directive} ([^;]*)", policy).group(1):
            return policy                  # hashes would switch 'unsafe-inline' off: leave a custom policy be
        return re.sub(rf"{directive} ([^;]*)", lambda x: f"{directive} {x.group(1)} {' '.join(hashes)}",
                      policy, count=1)

    body = html if isinstance(html, bytes) else html.encode()
    m = re.search(rb'<script type="importmap">(.*?)</script>', body, re.S)
    csp = add(csp, "script-src", [sha(m.group(1))] if m else [])
    if m and csp_is_default(csp):
        csp = _TRUSTED_TYPES.sub("", csp).rstrip("; ")
    styles = list(dict.fromkeys(sha(s) for s in re.findall(rb"<style(?:\s[^>]*)?>(.*?)</style>", body, re.S)))
    return add(csp, "style-src", styles)
