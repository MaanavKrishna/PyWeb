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
    df = Path(tmp_path / "d" / "Dockerfile").read_text()
    assert "FROM ${PYTHON_IMAGE}" in df and "ARG PYTHON_IMAGE=python:3.12-slim" in df


def test_deploy_compose_with_db(tmp_path):
    r = run("deploy", "--target", "compose", "--out", str(tmp_path / "c"),
            "--db-url", "postgres://db/app")
    assert r.returncode == 0, r.stderr
    import yaml
    doc = yaml.safe_load(Path(tmp_path / "c" / "compose.yaml").read_text())
    assert doc["services"]["web"]["environment"]["DATABASE_URL"] == "postgres://db/app"
    assert "postgres" not in doc["services"]                     # an existing database: nothing provisioned


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
    import yaml
    r = run("deploy", "--target", "compose", "--out", str(tmp_path / "c"), "--domain", "shop.example.com")
    assert r.returncode == 0, r.stderr
    services = yaml.safe_load((tmp_path / "c" / "compose.yaml").read_text())["services"]
    app, caddy = services["web"], services["caddy"]
    assert "ports" not in app and app["expose"] == ["8000"]                    # only reachable through Caddy
    assert app["environment"]["PYWEB_TRUST_PROXY"] == "1" and app["environment"]["PYWEB_COOKIE_SECURE"] == "1"
    assert caddy["ports"] == ["80:80", "443:443"]
    assert "reverse_proxy web:8000" in (tmp_path / "c" / "Caddyfile").read_text()
    assert "docker compose up" in r.stdout


def test_deploy_k8s_trusts_the_ingress_and_checks_readiness(tmp_path):
    import yaml
    run("deploy", "--target", "k8s", "--out", str(tmp_path / "k"))
    docs = list(yaml.safe_load_all((tmp_path / "k" / "k8s.yaml").read_text()))
    config = next(d for d in docs if d["kind"] == "ConfigMap")
    web = next(d for d in docs if d["kind"] == "Deployment")
    assert config["data"]["PYWEB_TRUST_PROXY"] == "1"
    assert web["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["httpGet"]["path"] == "/readyz"
