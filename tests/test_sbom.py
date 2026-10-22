"""CycloneDX bills of materials for built apps and for releases."""

import json
import subprocess
import sys

from pyweb import __version__, sbom

LOCK = {"lockfileVersion": 1, "packages": {
    "chart.js": {"version": "4.4.1", "direct": True, "needs": ["@kurkle/color"],
                 "integrity": "sha512-" + "QUJD" * 21 + "QQ==", "files": []},
    "@kurkle/color": {"version": "0.3.4", "direct": False, "needs": [], "integrity": "sha256-" + "QUJD" * 10 + "QUI=",
                      "files": []}}}


def test_app_sbom_lists_pyweb_and_vendored_npm_packages(tmp_path):
    (tmp_path / "pyweb.lock").write_text(json.dumps(LOCK))
    doc = sbom.for_app(str(tmp_path), name="shop")
    assert doc["bomFormat"] == "CycloneDX" and doc["specVersion"] == "1.6"
    by_name = {c["name"]: c for c in doc["components"]}
    assert by_name["pyweb-stack"]["purl"] == f"pkg:pypi/pyweb-stack@{__version__}"
    assert by_name["chart.js"]["purl"] == "pkg:npm/chart.js@4.4.1"
    assert by_name["@kurkle/color"]["purl"] == "pkg:npm/%40kurkle/color@0.3.4"
    assert by_name["chart.js"]["hashes"][0]["alg"] == "SHA-512"
    assert by_name["@kurkle/color"]["hashes"][0]["alg"] == "SHA-256"
    deps = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
    assert deps["app:shop"] == ["pyweb-stack", "npm:chart.js@4.4.1"]
    assert deps["npm:chart.js@4.4.1"] == ["npm:@kurkle/color@0.3.4"]


def test_build_writes_sbom_json(tmp_path):
    app = tmp_path / "app.pyweb"
    app.write_text('from pyweb import App\napp = App()\n@app.page("/")\ndef H():\n    <p>hi</p>\n')
    done = subprocess.run([sys.executable, "-m", "pyweb.cli", "build", str(app), "--out", str(tmp_path / "dist")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    doc = json.loads((tmp_path / "dist" / "sbom.json").read_text())
    assert [c["name"] for c in doc["components"]] == ["pyweb-stack"]


def test_release_sbom_hashes_the_files(tmp_path):
    (tmp_path / "pyweb_stack-1.0-py3-none-any.whl").write_bytes(b"wheel")
    (tmp_path / "pyweb_stack-1.0.tar.gz").write_bytes(b"sdist")
    (tmp_path / "notes.txt").write_text("ignored")
    out = tmp_path / "bom.json"
    assert sbom.main(["--release", str(tmp_path), "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    files = {c["name"]: c["hashes"][0]["content"] for c in doc["components"] if c["type"] == "file"}
    assert files == {"pyweb_stack-1.0-py3-none-any.whl": __import__("hashlib").sha256(b"wheel").hexdigest(),
                     "pyweb_stack-1.0.tar.gz": __import__("hashlib").sha256(b"sdist").hexdigest()}
    assert {c["name"] for c in doc["components"] if c.get("scope") == "optional"} >= {"orjson", "cryptography"}


def test_workflows_pin_every_action_by_commit():
    import glob
    import os
    import re
    paths = glob.glob(os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "*.yml"))
    assert len(paths) >= 4
    for path in paths:
        for ref in re.findall(r"uses:\s*\S+@(\S+)", open(path).read()):
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (path, ref)
