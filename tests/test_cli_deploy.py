"""CLI deploy scaffolding for docker/compose/k8s targets."""

import os
import subprocess
import sys
from pathlib import Path


def run(*args):
    return subprocess.run([sys.executable, "-m", "pyweb.cli", *args],
                          capture_output=True, text=True, cwd=".")


def test_deploy_docker(tmp_path):
    r = run("deploy", "--target", "docker", "--out", str(tmp_path / "d"))
    assert r.returncode == 0, r.stderr
    assert "FROM python" in Path(tmp_path / "d" / "Dockerfile").read_text()


def test_deploy_compose_with_db(tmp_path):
    r = run("deploy", "--target", "compose", "--out", str(tmp_path / "c"),
            "--db-url", "postgres://db/app")
    assert r.returncode == 0, r.stderr
    assert "DATABASE_URL=postgres://db/app" in Path(tmp_path / "c" / "compose.yaml").read_text()


def test_deploy_k8s(tmp_path):
    r = run("deploy", "--target", "k8s", "--out", str(tmp_path / "k"),
            "--app", "shop", "--image", "shop:2")
    assert r.returncode == 0, r.stderr
    assert "shop:2" in Path(tmp_path / "k" / "k8s.yaml").read_text()


def test_deploy_unknown_target_fails(tmp_path):
    r = run("deploy", "--target", "nomad", "--out", str(tmp_path / "n"))
    assert r.returncode != 0


def test_package_exports_new_modules():
    import pyweb
    for name in ("sync", "live", "deploy", "uploads", "lsp"):
        assert name in pyweb.__all__ and hasattr(pyweb, name)
    assert os.path.exists("pyweb/sync.py")
