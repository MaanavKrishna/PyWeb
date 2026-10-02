"""Deployment: Dockerfile, compose, k8s manifests, env/secret handling."""

from __future__ import annotations

import os
import re


def dockerfile(python="3.12-slim", port=8000, app_file="app.pyweb"):
    """Dockerfile for an app directory (``app.pyweb`` + optional requirements.txt)."""
    from pyweb import __version__
    pin = f"=={__version__}" if __version__[:1].isdigit() and "+" not in __version__ else ""
    return (
        f"FROM python:{python}\n"
        "WORKDIR /app\n"
        f"RUN pip install --no-cache-dir pyweb-stack{pin}\n"
        "COPY requirements.tx[t] ./\n"
        "RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi\n"
        "COPY . .\n"
        f"RUN pyweb build {app_file} --out dist --production\n"
        "ENV PYWEB_ENV=production\n"
        f"EXPOSE {port}\n"
        'HEALTHCHECK --interval=30s --timeout=5s CMD python -c '
        '"import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('
        f"'http://127.0.0.1:{port}/healthz').status==200 else 1)\"\n"
        f'CMD ["pyweb", "serve", "dist", "--host", "0.0.0.0", "--port", "{port}"]\n'
    )


def compose(service="pyweb", port=8000, db_url=""):
    db = f'\n      - DATABASE_URL={db_url}' if db_url else ""
    return (
        "services:\n"
        f"  {service}:\n"
        "    build: .\n"
        "    ports:\n"
        f'      - "{port}:{port}"\n'
        "    environment:\n"
        f"      - PORT={port}{db}\n"
        "    restart: unless-stopped\n"
    )


def k8s_manifest(app="pyweb", image="pyweb:latest", port=8000, replicas=2):
    return (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n"
        f"  name: {app}\n"
        "spec:\n"
        f"  replicas: {replicas}\n"
        "  selector:\n"
        "    matchLabels:\n"
        f"      app: {app}\n"
        "  template:\n"
        "    metadata:\n"
        f"      labels:\n        app: {app}\n"
        "    spec:\n"
        "      containers:\n"
        f"      - name: {app}\n"
        f"        image: {image}\n"
        "        ports:\n"
        f"        - containerPort: {port}\n"
        "        livenessProbe:\n"
        "          httpGet:\n"
        "            path: /healthz\n"
        f"            port: {port}\n"
        "          periodSeconds: 30\n"
        "        readinessProbe:\n"
        "          httpGet:\n"
        "            path: /healthz\n"
        f"            port: {port}\n"
        "          periodSeconds: 5\n"
        "        env:\n"
        "        - name: PORT\n"
        f'          value: "{port}"\n'
        "---\n"
        "apiVersion: v1\nkind: Service\nmetadata:\n"
        f"  name: {app}\n"
        "spec:\n"
        "  selector:\n"
        f"    app: {app}\n"
        "  ports:\n"
        f"  - port: {port}\n"
    )


_ENV_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def expand_env(text, env=None):
    env = env if env is not None else os.environ

    def repl(m):
        return env.get(m.group(1), m.group(2) if m.group(2) is not None else "")
    return _ENV_RE.sub(repl, text)


def required_env(names, env=None):
    env = env if env is not None else os.environ
    missing = [n for n in names if not env.get(n)]
    if missing:
        raise RuntimeError(f"missing required env: {', '.join(missing)}")
    return {n: env[n] for n in names}
