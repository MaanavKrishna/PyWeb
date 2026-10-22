"""Software bills of materials (CycloneDX 1.6 JSON), written with the standard library.

* ``pyweb build`` writes ``sbom.json`` next to the built app: PyWeb itself and
  every npm package vendored through ``pyweb add`` (name, version, package
  URL and the registry's integrity hash), so scanners such as Grype, Trivy or
  Dependency-Track can check what the app ships to browsers.
* ``python -m pyweb.sbom --release dist/`` describes a PyWeb release: the
  wheel and sdist with their SHA-256, and the optional extras. The release
  workflow attaches it to each GitHub release.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import uuid

SPEC = "1.6"


def _doc(component, components, dependencies=None):
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "tools": {"components": [{"type": "application", "name": "pyweb-stack", "version": _version()}]},
            "component": component,
        },
        "components": components,
        "dependencies": dependencies or [],
    }


def _version():
    from pyweb import __version__
    return __version__


def _pyweb_component(bom_ref="pyweb-stack"):
    v = _version()
    return {"type": "framework", "bom-ref": bom_ref, "name": "pyweb-stack", "version": v,
            "purl": f"pkg:pypi/pyweb-stack@{v}", "licenses": [{"license": {"id": "MIT"}}]}


def _integrity_hash(integrity):
    """A CycloneDX hash from an npm ``integrity`` value (``sha512-<base64>``)."""
    algo, _, b64 = (integrity or "").partition("-")
    names = {"sha512": "SHA-512", "sha384": "SHA-384", "sha256": "SHA-256", "sha1": "SHA-1"}
    if algo not in names or not b64:
        return None
    try:
        return {"alg": names[algo], "content": base64.b64decode(b64).hex()}
    except ValueError:
        return None


def _npm_purl(name, version):
    if name.startswith("@"):
        scope, _, bare = name[1:].partition("/")
        return f"pkg:npm/%40{scope}/{bare}@{version}"
    return f"pkg:npm/{name}@{version}"


def for_app(app_dir, name="app", version="0"):
    """The bill of materials for the app in ``app_dir`` (PyWeb plus its vendored npm packages)."""
    from .packages import read_lock
    lock = read_lock(app_dir)
    pyweb = _pyweb_component()
    components, deps = [pyweb], []
    app_ref = f"app:{name}"
    direct = []
    for pkg_name, pkg in sorted((lock.get("packages") or {}).items()):
        ref = f"npm:{pkg_name}@{pkg.get('version', '')}"
        comp = {"type": "library", "bom-ref": ref, "name": pkg_name, "version": pkg.get("version", ""),
                "purl": _npm_purl(pkg_name, pkg.get("version", "")),
                "scope": "required"}
        h = _integrity_hash(pkg.get("integrity"))
        if h:
            comp["hashes"] = [h]
        components.append(comp)
        deps.append({"ref": ref, "dependsOn": [f"npm:{n}@{(lock['packages'].get(n) or {}).get('version', '')}"
                                               for n in pkg.get("needs", ()) if n in lock["packages"]]})
        if pkg.get("direct", True):
            direct.append(ref)
    deps.insert(0, {"ref": app_ref, "dependsOn": ["pyweb-stack", *direct]})
    return _doc({"type": "application", "bom-ref": app_ref, "name": name, "version": version}, components, deps)


def write_for_app(app_dir, out_dir, name="app"):
    path = os.path.join(out_dir, "sbom.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(for_app(app_dir, name=name), fh, indent=2)
    return path


EXTRAS = {"postgres": "psycopg", "mysql": "PyMySQL", "redis": "redis", "crypto": "cryptography",
          "asgi": "uvicorn", "auth": "argon2-cffi", "speed": "orjson",
          "otel": "opentelemetry-sdk"}


def for_release(dist_dir):
    """The bill of materials for the release files in ``dist_dir`` (wheel and sdist)."""
    pyweb = _pyweb_component()
    components = []
    for name in sorted(os.listdir(dist_dir)):
        if not name.endswith((".whl", ".tar.gz")):
            continue
        with open(os.path.join(dist_dir, name), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        components.append({"type": "file", "bom-ref": f"file:{name}", "name": name,
                           "hashes": [{"alg": "SHA-256", "content": digest}]})
    for extra, package in EXTRAS.items():
        components.append({"type": "library", "bom-ref": f"pypi:{package}", "name": package,
                           "purl": f"pkg:pypi/{package.lower()}", "scope": "optional",
                           "properties": [{"name": "pyweb:extra", "value": extra}]})
    deps = [{"ref": "pyweb-stack", "dependsOn": [c["bom-ref"] for c in components if c["type"] == "library"]}]
    return _doc(pyweb, components, deps)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m pyweb.sbom", description="CycloneDX bills of materials")
    ap.add_argument("--release", metavar="DIST", help="describe the wheel and sdist in DIST")
    ap.add_argument("--app", metavar="DIR", help="describe the app in DIR (its pyweb.lock)")
    ap.add_argument("--out", default="-", help="file to write (default: standard output)")
    args = ap.parse_args(argv)
    if not (args.release or args.app):
        ap.error("pass --release DIST or --app DIR")
    doc = for_release(args.release) if args.release else for_app(args.app, name=os.path.basename(
        os.path.abspath(args.app)))
    text = json.dumps(doc, indent=2)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
