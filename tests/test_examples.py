"""Item 6: every example app compiles, builds, and passes `check`.

If an example rots, this fails — docs code that doesn't run is a lie.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyweb import build as _build
from pyweb.compiler import compile_source
from pyweb.security import check_source as _check_source

EXAMPLES = sorted(
    p for p in (Path(__file__).parent.parent / "examples").glob("*/app.pyweb"))


@pytest.mark.parametrize("path", [str(p) for p in EXAMPLES], ids=lambda p: p.split("/")[-2])
def test_example_compiles_builds_checks(path, tmp_path):
    src = Path(path).read_text()
    out = compile_source(src, filename="app.pyweb")
    assert out["pages"], path
    dist = str(tmp_path / "dist")
    _build.build(out, dist)
    assert (Path(dist) / "manifest.json").exists()
    findings = _check_source(src, path)
    leaks = [f for f in findings
             if "secret" in str(f.get("kind", "")).lower()
             or f.get("kind") == "compile-error"]
    assert not leaks, (path, leaks)
