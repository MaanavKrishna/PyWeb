"""Turn a :class:`~pyweb.deploy.plan.Plan` into files for each target.

All targets share the same image (a multi-stage Dockerfile: dependencies and
the production build in one stage, a small non-root runtime in the next) and
the same processes: web (``pyweb serve``), worker (``pyweb worker``) and a
release step (``pyweb db upgrade``) that runs once per deploy, before the new
version takes traffic.
"""

from __future__ import annotations

import json
import re

# ------------------------------------------------------------------- YAML


def _scalar(v):
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or re.search(r"[:#{}\[\],&*!|>'\"%@`\n]|^[-?\s]|\s$", s) or s.lower() in (
            "true", "false", "yes", "no", "on", "off", "null", "~") or re.fullmatch(r"[\d.e+-]+", s):
        return json.dumps(s)
    return s


def yaml(obj, indent=0):
    """A small YAML writer for dicts, lists and scalars (enough for deploy files; no dependency)."""
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            return pad + "{}\n"
        out = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{_scalar(k)}:\n{yaml(v, indent + 1)}")
            else:
                out.append(f"{pad}{_scalar(k)}: {_inline(v)}\n")
        return "".join(out)
    if isinstance(obj, list):
        if not obj:
            return pad + "[]\n"
        out = []
        for item in obj:
            if isinstance(item, dict) and item:
                body = yaml(item, indent + 1)
                out.append(f"{pad}- {body.lstrip()}")
            elif isinstance(item, list) and item:
                out.append(f"{pad}-\n{yaml(item, indent + 1)}")
            else:
                out.append(f"{pad}- {_inline(item)}\n")
        return "".join(out)
    return pad + _scalar(obj) + "\n"


def _inline(v):
    if isinstance(v, dict) and not v:
        return "{}"
    if isinstance(v, list) and not v:
        return "[]"
    return _scalar(v)


def _header(plan, what):
    return (f"# {what} for {plan.name}, written by `pyweb deploy {plan.target}`.\n"
            "# Re-run it after changing the app; see the plan it prints for why things are set up this way.\n")


# ------------------------------------------------------------------ image

def pyweb_requirement(plan):
    from pyweb import __version__
    spec = "pyweb-stack"
    if re.fullmatch(r"\d+\.\d+\.\d+", __version__):
        spec += f"=={__version__}"
    return spec


# The worker container's health check (pyweb worker touches a file after each healthy round).
WORKER_ALIVE = ["pyweb", "worker", "--alive"]

DRIVERS = {"postgres": "psycopg[binary]>=3.1", "mysql": "PyMySQL>=1.1", "redis": "redis>=5", "auth": "argon2-cffi>=23.1"}


def extra_packages(plan):
    """What the plan needs besides PyWeb: database and Redis drivers, argon2 for sign-in."""
    extras = set(plan.extras) | ({"auth"} if plan.facts.auth else set())
    return " ".join(DRIVERS[e] for e in sorted(extras) if e in DRIVERS)


def dockerfile(plan, python="3.12-slim"):
    return f"""# syntax=docker/dockerfile:1
{_header(plan, "Container image")}
# PYTHON_IMAGE can point at a mirror or private registry (--build-arg PYTHON_IMAGE=...).
ARG PYTHON_IMAGE=python:{python}

# ---- build: dependencies and the production build (hashed, minified assets)
FROM ${{PYTHON_IMAGE}} AS build
ARG PYWEB_SPEC="{pyweb_requirement(plan)}"
ARG EXTRA_PACKAGES="{extra_packages(plan)}"
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/root/.cache/pip \\
    python -m venv /venv \\
 && /venv/bin/pip install "$PYWEB_SPEC" $EXTRA_PACKAGES \\
 && if [ -f requirements.txt ]; then /venv/bin/pip install -r requirements.txt; fi
RUN /venv/bin/pyweb build {plan.app_file} --out /dist --production \\
 && if [ -d migrations ]; then cp -r migrations /dist/; fi

# ---- runtime: no compilers, no source tree, not root
FROM ${{PYTHON_IMAGE}}
ENV PATH=/venv/bin:$PATH PYWEB_ENV=production PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \\
    PORT={plan.port} WEB_CONCURRENCY={plan.processes_per_replica}
RUN useradd --system --uid 10001 --home-dir /app pyweb \\
 && mkdir -p /app/data && chown pyweb /app/data
COPY --from=build /venv /venv
COPY --from=build /dist /app/dist
WORKDIR /app
USER 10001
EXPOSE {plan.port}
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s \\
  CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '{plan.port}'), timeout=4).status != 200)"]
CMD {json.dumps(plan.serve_cmd)}
"""


DOCKERIGNORE = """.git
.github
**/__pycache__
**/*.pyc
.venv
venv
env
node_modules
dist
deploy
*.db
*.db-wal
*.db-shm
.env
.env.*
!.env.example
"""


def env_example(plan):
    lines = [f"# Settings for {plan.name}. Copy to .env and fill in; never commit the real one."]
    for k, v in sorted(plan.secrets.items()):
        if k == "DATABASE_URL" and set(plan.provision) & {"postgres", "mysql"}:
            continue
        lines.append(f"# {v}")
        lines.append(f"{k}=")
    if "postgres" in plan.provision or "mysql" in plan.provision:
        lines.append("# password for the database container (any long random string)")
        lines.append("DB_PASSWORD=")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- compose

def compose(plan):
    env = dict(plan.env)
    for k in plan.secrets:
        if k == "PYWEB_AUTH_SECRET":
            env[k] = "${PYWEB_AUTH_SECRET:?set PYWEB_AUTH_SECRET in .env}"
        elif k != "DATABASE_URL" or not set(plan.provision) & {"postgres", "mysql"}:
            env[k] = "${" + k + ":-}"
    if "postgres" in plan.provision:
        env["DATABASE_URL"] = "postgresql://pyweb:${DB_PASSWORD:?set DB_PASSWORD in .env}@postgres:5432/pyweb"
    if "mysql" in plan.provision:
        env["DATABASE_URL"] = "mysql://pyweb:${DB_PASSWORD:?set DB_PASSWORD in .env}@mysql:3306/pyweb"
    if plan.redis:
        env["PYWEB_REDIS_URL"] = "redis://redis:6379/0"
    front = bool(plan.domain) or plan.replicas > 1
    if front:
        env["PYWEB_TRUST_PROXY"] = "1"
        if plan.domain:
            env["PYWEB_COOKIE_SECURE"] = "1"
    else:
        env.pop("PYWEB_TRUST_PROXY", None)
        env.pop("PYWEB_COOKIE_SECURE", None)

    deps = {}
    if "postgres" in plan.provision:
        deps["postgres"] = {"condition": "service_healthy"}
    if "mysql" in plan.provision:
        deps["mysql"] = {"condition": "service_healthy"}
    if plan.redis:
        deps["redis"] = {"condition": "service_healthy"}
    volumes = ["data:/app/data"] if "volume" in plan.provision else []
    services = {}

    def app_service(command=None, **extra):
        svc = {"build": ".", "image": plan.image, "env_file": [{"path": ".env", "required": False}],
               "environment": dict(env), "restart": "unless-stopped"}
        if command:
            svc["command"] = command
        if volumes:
            svc["volumes"] = list(volumes)
        svc.update(extra)
        return svc

    if plan.migrate:
        services["migrate"] = app_service(plan.migrate_cmd, restart="no", healthcheck={"disable": True},
                                          depends_on=dict(deps) if deps else None)
        if not deps:
            services["migrate"].pop("depends_on")
    web_deps = dict(deps)
    if plan.migrate:
        web_deps["migrate"] = {"condition": "service_completed_successfully"}
    web = app_service()
    if web_deps:
        web["depends_on"] = web_deps
    if front:
        web["expose"] = [str(plan.port)]
    else:
        web["ports"] = [f"{plan.port}:{plan.port}"]
    if plan.replicas > 1:
        web["deploy"] = {"replicas": plan.replicas}
    services["web"] = web
    if plan.worker:
        services["worker"] = app_service(plan.worker_cmd, depends_on=web_deps or None)
        if not web_deps:
            services["worker"].pop("depends_on")
        services["worker"]["stop_grace_period"] = "30s"
        # No HTTP here: healthy means the worker finished a round recently (it can reach its job store).
        services["worker"]["healthcheck"] = {"test": ["CMD", *WORKER_ALIVE], "interval": "15s", "timeout": "5s",
                                             "retries": 3, "start_period": "30s"}
        services["worker"].pop("image")
    if "postgres" in plan.provision:
        services["postgres"] = {
            "image": "postgres:17", "restart": "unless-stopped",
            "environment": {"POSTGRES_USER": "pyweb", "POSTGRES_DB": "pyweb",
                            "POSTGRES_PASSWORD": "${DB_PASSWORD:?set DB_PASSWORD in .env}"},
            "volumes": ["postgres:/var/lib/postgresql/data"],
            "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U pyweb"], "interval": "5s", "retries": 20}}
    if "mysql" in plan.provision:
        services["mysql"] = {
            "image": "mysql:8.4", "restart": "unless-stopped",
            "environment": {"MYSQL_USER": "pyweb", "MYSQL_DATABASE": "pyweb",
                            "MYSQL_PASSWORD": "${DB_PASSWORD:?set DB_PASSWORD in .env}",
                            "MYSQL_RANDOM_ROOT_PASSWORD": "1"},
            "volumes": ["mysql:/var/lib/mysql"],
            "healthcheck": {"test": ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1"], "interval": "5s",
                            "retries": 30}}
    if plan.redis:
        services["redis"] = {"image": "redis:7", "restart": "unless-stopped",
                             "command": ["redis-server", "--appendonly", "yes"], "volumes": ["redis:/data"],
                             "healthcheck": {"test": ["CMD", "redis-cli", "ping"], "interval": "5s",
                                             "retries": 20}}
    if front:
        services["caddy"] = {"image": "caddy:2", "restart": "unless-stopped",
                             "ports": ["80:80", "443:443"] if plan.domain else ["80:80"],
                             "volumes": ["./Caddyfile:/etc/caddy/Caddyfile:ro", "caddy:/data"],
                             "depends_on": ["web"]}
    named = [v for v, on in (("data", "volume" in plan.provision), ("postgres", "postgres" in plan.provision),
                             ("mysql", "mysql" in plan.provision), ("redis", plan.redis), ("caddy", front)) if on]
    doc = {"name": plan.name, "services": services}
    if named:
        doc["volumes"] = {v: {} for v in named}
    return _header(plan, "docker compose") + yaml(doc)


def caddyfile(plan):
    site = plan.domain or ":80"
    upstream = f"web:{plan.port}"
    lb = (f"    reverse_proxy {{\n        dynamic a web {plan.port}\n        lb_policy round_robin\n"
          "        health_uri /readyz\n    }\n") if plan.replicas > 1 else f"    reverse_proxy {upstream}\n"
    return (f"# Caddy for {plan.name}: HTTPS certificates are automatic"
            f"{' for ' + plan.domain if plan.domain else ''}; WebSockets pass through.\n"
            f"{site} {{\n    encode zstd gzip\n{lb}}}\n")


# ----------------------------------------------------------------- k8s

def k8s(plan):
    name = plan.name
    labels = {"app.kubernetes.io/name": name}
    secret_names = sorted(k for k in plan.secrets)
    env_from = [{"configMapRef": {"name": f"{name}-config"}}, {"secretRef": {"name": f"{name}-secrets"}}]
    security = {"runAsNonRoot": True, "runAsUser": 10001, "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]}}

    def container(cname, command=None, probes=True):
        c = {"name": cname, "image": plan.image, "envFrom": env_from, "securityContext": security,
             "resources": {"requests": {"cpu": "100m", "memory": "192Mi"}, "limits": {"memory": "512Mi"}}}
        if command:
            c["command"] = command
        if probes:
            c["ports"] = [{"containerPort": plan.port, "name": "http"}]
            c["livenessProbe"] = {"httpGet": {"path": "/healthz", "port": "http"}, "periodSeconds": 20}
            c["readinessProbe"] = {"httpGet": {"path": "/readyz", "port": "http"}, "periodSeconds": 5}
            c["startupProbe"] = {"httpGet": {"path": "/healthz", "port": "http"}, "periodSeconds": 2,
                                 "failureThreshold": 30}
            # Let the load balancer stop sending traffic before the server starts draining.
            c["lifecycle"] = {"preStop": {"exec": {"command": ["sleep", "5"]}}}
        return c

    docs = []
    env = dict(plan.env)
    if plan.redis and "PYWEB_REDIS_URL" not in plan.secrets:
        secret_names.append("PYWEB_REDIS_URL")
    docs.append({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": f"{name}-config", "labels": labels},
                 "data": {k: str(v) for k, v in sorted(env.items())}})
    web_spec = {"terminationGracePeriodSeconds": 35, "containers": [container("web")]}
    if plan.migrate:
        # Every new pod migrates first; a database lock makes all but one wait, then see nothing to do.
        web_spec["initContainers"] = [container("migrate", plan.migrate_cmd, probes=False)]
    docs.append({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "labels": labels},
                 "spec": {"replicas": plan.replicas, "selector": {"matchLabels": labels},
                          "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0,
                                                                                   "maxSurge": 1}},
                          "template": {"metadata": {"labels": labels}, "spec": web_spec}}})
    if plan.worker:
        worker = container("worker", plan.worker_cmd, probes=False)
        worker["livenessProbe"] = {"exec": {"command": WORKER_ALIVE}, "periodSeconds": 30, "failureThreshold": 3}
        wl = {"app.kubernetes.io/name": f"{name}-worker"}
        docs.append({"apiVersion": "apps/v1", "kind": "Deployment",
                     "metadata": {"name": f"{name}-worker", "labels": wl},
                     "spec": {"replicas": 1, "selector": {"matchLabels": wl},
                              "template": {"metadata": {"labels": wl}, "spec": {
                                  "terminationGracePeriodSeconds": 35,
                                  "containers": [worker]}}}})
    docs.append({"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "labels": labels},
                 "spec": {"selector": labels, "ports": [{"name": "http", "port": 80, "targetPort": "http"}]}})
    if plan.replicas > 1:
        docs.append({"apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": {"name": name},
                     "spec": {"minAvailable": 1, "selector": {"matchLabels": labels}}})
        docs.append({"apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler", "metadata": {"name": name},
                     "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": name},
                              "minReplicas": plan.replicas, "maxReplicas": plan.replicas * 4,
                              "metrics": [{"type": "Resource", "resource": {
                                  "name": "cpu", "target": {"type": "Utilization", "averageUtilization": 70}}}]}})
    if plan.domain:
        docs.append({"apiVersion": "networking.k8s.io/v1", "kind": "Ingress", "metadata": {
            "name": name, "annotations": {
                "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",      # live-update sockets stay open
                "nginx.ingress.kubernetes.io/proxy-send-timeout": "3600",
                "nginx.ingress.kubernetes.io/proxy-body-size": "16m"}},
            "spec": {"rules": [{"host": plan.domain, "http": {"paths": [{
                "path": "/", "pathType": "Prefix",
                "backend": {"service": {"name": name, "port": {"name": "http"}}}}]}}]}})
    secret_cmd = (f"# Secrets (not in this file):\n#   kubectl create secret generic {name}-secrets \\\n" +
                  " \\\n".join(f"#     --from-literal={k}=..." for k in sorted(set(secret_names))) + "\n")
    return _header(plan, "Kubernetes manifests") + secret_cmd + "\n---\n".join(yaml(d) for d in docs)


# ------------------------------------------------------------------ fly

def fly(plan):
    env = {k: v for k, v in plan.env.items() if k != "DATABASE_URL" or "volume" in plan.provision}
    region = plan.region or "iad"
    lines = [_header(plan, "Fly.io config").rstrip(), "", f'app = "{plan.name}"', f'primary_region = "{region}"',
             "kill_signal = \"SIGTERM\"", "kill_timeout = 30", "", "[build]", '  dockerfile = "Dockerfile"', ""]
    if plan.migrate:
        lines += ["[deploy]", f'  release_command = "{" ".join(plan.migrate_cmd)}"', '  strategy = "rolling"', ""]
    lines += ["[env]"] + [f'  {k} = "{v}"' for k, v in sorted(env.items())] + [""]
    lines += ["[processes]", f'  web = "{" ".join(plan.serve_cmd)}"']
    if plan.worker:
        lines.append(f'  worker = "{" ".join(plan.worker_cmd)}"')
    lines += ["", "[http_service]", f"  internal_port = {plan.port}", "  force_https = true",
              '  auto_stop_machines = "stop"', "  auto_start_machines = true",
              f"  min_machines_running = {plan.replicas}", '  processes = ["web"]', "",
              "  [http_service.concurrency]", '    type = "connections"', "    soft_limit = 2000",
              "    hard_limit = 5000", "", "  [[http_service.checks]]", '    method = "GET"', '    path = "/readyz"',
              '    interval = "15s"', '    timeout = "5s"', '    grace_period = "20s"', ""]
    if "volume" in plan.provision:
        lines += ["[mounts]", '  source = "data"', '  destination = "/app/data"', '  processes = ["web"]', ""]
    lines += ["[[vm]]", '  memory = "512mb"', '  cpu_kind = "shared"', "  cpus = 1", ""]
    return "\n".join(lines)


# --------------------------------------------------------------- render

def render_yaml(plan):
    shared = [{"key": k, "value": str(v)} for k, v in sorted(plan.env.items()) if k != "DATABASE_URL"]
    if "volume" in plan.provision:
        shared.append({"key": "DATABASE_URL", "value": plan.env["DATABASE_URL"]})
    shared.append({"key": "PYWEB_AUTH_SECRET", "generateValue": True})
    if "postgres" in plan.provision:
        shared.append({"key": "DATABASE_URL", "fromDatabase": {"name": f"{plan.name}-db",
                                                              "property": "connectionString"}})
    if plan.redis:
        shared.append({"key": "PYWEB_REDIS_URL", "fromService": {"type": "keyvalue", "name": f"{plan.name}-redis",
                                                                 "property": "connectionString"}})
    for k in sorted(plan.secrets):
        if k not in ("PYWEB_AUTH_SECRET", "DATABASE_URL") or (k == "DATABASE_URL" and "postgres" not in plan.provision
                                                              and "volume" not in plan.provision):
            shared.append({"key": k, "sync": False})
    services = [{"type": "web", "name": plan.name, "runtime": "docker", "plan": "starter",
                 "healthCheckPath": "/readyz", "numInstances": plan.replicas, "envVars": shared}]
    if plan.migrate:
        services[0]["preDeployCommand"] = " ".join(plan.migrate_cmd)
    if "volume" in plan.provision:
        services[0]["disk"] = {"name": "data", "mountPath": "/app/data", "sizeGB": 1}
    if plan.worker:
        services.append({"type": "worker", "name": f"{plan.name}-worker", "runtime": "docker", "plan": "starter",
                         "dockerCommand": " ".join(plan.worker_cmd), "envVars": shared})
    if plan.redis:
        services.append({"type": "keyvalue", "name": f"{plan.name}-redis", "plan": "starter",
                         "maxmemoryPolicy": "noeviction", "ipAllowList": []})
    doc = {"services": services}
    if "postgres" in plan.provision:
        doc["databases"] = [{"name": f"{plan.name}-db", "plan": "basic-256mb", "ipAllowList": []}]
    return _header(plan, "Render blueprint") + yaml(doc)


# -------------------------------------------------------------- railway

def railway(plan, role="web"):
    deploy = {"restartPolicyType": "ON_FAILURE", "restartPolicyMaxRetries": 10}
    if role == "web":
        deploy.update({"startCommand": "pyweb serve dist --host 0.0.0.0",   # the port comes from $PORT
                       "healthcheckPath": "/readyz", "healthcheckTimeout": 60, "numReplicas": plan.replicas})
        if plan.migrate:
            deploy["preDeployCommand"] = [" ".join(plan.migrate_cmd)]
    else:
        deploy["startCommand"] = " ".join(plan.worker_cmd)
    return json.dumps({"$schema": "https://railway.com/railway.schema.json",
                       "build": {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"},
                       "deploy": deploy}, indent=2) + "\n"


# --------------------------------------------------------------- output

def files_for(plan):
    """``{filename: text}`` for the plan's target (the Dockerfile is part of every target)."""
    files = {"Dockerfile": dockerfile(plan), ".dockerignore": DOCKERIGNORE, ".env.example": env_example(plan)}
    t = plan.target
    if t == "compose":
        files["compose.yaml"] = compose(plan)
        if plan.domain or plan.replicas > 1:
            files["Caddyfile"] = caddyfile(plan)
    elif t == "k8s":
        files["k8s.yaml"] = k8s(plan)
    elif t == "fly":
        files["fly.toml"] = fly(plan)
    elif t == "render":
        files["render.yaml"] = render_yaml(plan)
    elif t == "railway":
        files["railway.json"] = railway(plan)
        if plan.worker:
            files["railway.worker.json"] = railway(plan, "worker")
    return files


def next_steps(plan):
    """What to run after writing the files."""
    t, n = plan.target, plan.name
    secret = 'python -c "import secrets; print(secrets.token_hex(32))"'
    if t == "docker":
        return [f"docker build -t {plan.image} .",
                f"docker run -p {plan.port}:{plan.port} -e PYWEB_AUTH_SECRET=$({secret}) {plan.image}"]
    if t == "compose":
        steps = ["cp .env.example .env   # then fill it in (PYWEB_AUTH_SECRET: " + secret + ")"]
        if plan.domain:
            steps.append(f"point {plan.domain}'s DNS at this server (Caddy fetches the HTTPS certificate)")
        return steps + ["docker compose up -d --build"]
    if t == "k8s":
        return [f"docker build -t {plan.image} . && docker push {plan.image}",
                f"kubectl create secret generic {n}-secrets --from-literal=PYWEB_AUTH_SECRET=$({secret}) ...",
                "kubectl apply -f k8s.yaml"]
    if t == "fly":
        steps = [f"fly launch --name {n} --copy-config --no-deploy"]
        if "postgres" in plan.provision:
            steps.append(f"fly postgres create --name {n}-db && fly postgres attach {n}-db   # sets DATABASE_URL")
        if "volume" in plan.provision:
            steps.append(f"fly volumes create data --size 1 --region {plan.region or 'iad'}")
        if plan.redis:
            steps.append(f"fly redis create --name {n}-redis   # then: fly secrets set PYWEB_REDIS_URL=...")
        steps.append(f"fly secrets set PYWEB_AUTH_SECRET=$({secret})")
        if plan.worker:
            steps.append("fly deploy && fly scale count worker=1")
        else:
            steps.append("fly deploy")
        return steps
    if t == "render":
        return ["commit render.yaml and push", "Render dashboard: New > Blueprint > pick the repository",
                "fill in the variables marked sync: false"]
    if t == "railway":
        steps = ["railway init && railway up   # the web service uses railway.json"]
        if "postgres" in plan.provision:
            steps.append("railway add --database postgres   # then reference ${{Postgres.DATABASE_URL}}")
        if plan.redis:
            steps.append("railway add --database redis      # PYWEB_REDIS_URL=${{Redis.REDIS_URL}}")
        if plan.worker:
            steps.append("add a second service from this repo with config path railway.worker.json")
        return steps + [f"railway variables --set PYWEB_AUTH_SECRET=$({secret})"]
    return []
