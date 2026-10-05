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


def test_deploy_compose_with_a_domain_puts_caddy_in_front(tmp_path):
    r = run("deploy", "--target", "compose", "--out", str(tmp_path / "c"), "--domain", "shop.example.com")
    assert r.returncode == 0, r.stderr
    spec = (tmp_path / "c" / "compose.yaml").read_text()
    app, caddy = spec.split("  caddy:\n")
    assert "ports:" not in app and 'expose:\n      - "8000"' in app          # only reachable through Caddy
    assert "- PYWEB_TRUST_PROXY=1" in app and "- PYWEB_COOKIE_SECURE=1" in app
    assert '- "80:80"' in caddy and '- "443:443"' in caddy
    assert "reverse_proxy pyweb:8000" in (tmp_path / "c" / "Caddyfile").read_text()
    assert "docker compose up" in r.stdout


def test_deploy_k8s_trusts_the_ingress_and_checks_readiness(tmp_path):
    run("deploy", "--target", "k8s", "--out", str(tmp_path / "k"))
    manifest = (tmp_path / "k" / "k8s.yaml").read_text()
    readiness = manifest.split("readinessProbe:", 1)[1]
    assert "path: /readyz" in readiness.split("env:", 1)[0]
    assert '- name: PYWEB_TRUST_PROXY\n          value: "1"' in manifest
