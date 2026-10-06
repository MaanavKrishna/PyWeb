# Security policy

## Supported versions

| Version | Security fixes |
|---|---|
| 0.5.x | yes |
| 0.4.x | critical fixes only, until 0.6.0 is released (see [Upgrading to 0.5](docs/27-upgrading.md)) |
| 0.3.x and older | no (please upgrade: `pip install -U pyweb-stack`) |

`pyweb upgrade --check` lists what an app needs to change to move to the
current version, and `pyweb upgrade --fix` applies the safe rewrites.

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
sessions, call server functions across origins, escape the static
file directory, read or change rows a row policy should hide, get a job or
email to run twice through a retried request, or reach the dev toolbar,
`/admin` or `/metrics` without being allowed to. The security model is described in
[docs/13-security.md](docs/13-security.md).
