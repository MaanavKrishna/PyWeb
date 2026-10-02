# Security policy

## Supported versions

| Version | Supported |
|---|---|
| latest 0.x release | yes |
| older releases | no (please upgrade: `pip install -U pyweb-stack`) |

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's
[security advisory form](https://github.com/MaanavKrishna/PyWeb/security/advisories/new)
rather than a public issue. Include a minimal `.pyweb` file or request
sequence that reproduces the problem, and the PyWeb version.

You can expect an acknowledgement within a week. Fixes are released as
patch versions and credited in the changelog unless you prefer
otherwise.

## Scope

In scope: anything that lets an attacker run script in a PyWeb page,
read data the developer did not send to the browser, forge or bypass
sessions, call server functions across origins, or escape the static
file directory. The security model is described in
[docs/13-security.md](docs/13-security.md).
