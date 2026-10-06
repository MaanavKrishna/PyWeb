"""Read an app and decide how it should run in production.

``pyweb deploy`` doesn't ask you to describe your architecture: it reads the
app (does it use a database? jobs? live updates? sign-in? uploads? does it
have migrations?), combines that with what you ask for (target, replicas,
domain) and decides the topology: which services to provision, which
processes to run, what must be set, and why. Every target (Docker, compose,
Kubernetes, Fly.io, Render, Railway) is rendered from this one plan, so
they all make the same decisions.
"""

from __future__ import annotations

import dataclasses
import os
import re

TARGETS = ("docker", "compose", "k8s", "fly", "render", "railway")
PLATFORMS = ("fly", "render", "railway")          # managed hosts with ephemeral disks
SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", "build", "deploy", "venv", ".venv", "env",
             "vendor", "site-packages", ".pytest_cache"}

_DB_LITERAL = re.compile(r"""\bApp\s*\((?:[^()]|\([^()]*\))*?\bdatabase\s*=\s*(['"])([a-zA-Z0-9+]+):""", re.S)
_DB_ANY = re.compile(r"""\bApp\s*\((?:[^()]|\([^()]*\))*?\bdatabase\s*=""", re.S)
_JOBS = re.compile(r"@\s*(?:\w+\.)?(?:job|cron|every)\b|\b(?:jobs\.job|\.enqueue)\s*\(")
_REALTIME = re.compile(r"\b(?:live|channel|presence|publish|join)\s*\(|\.live\s*\(\s*\)")
_AUTH = re.compile(r"\.use_auth\s*\(")
_PROVIDERS = re.compile(r"providers\s*=\s*\[([^\]]*)\]")
_UPLOADS = re.compile(r"\bFile\s*\(|\bUploadedFile\b|<FileInput\b")
_MAIL = re.compile(r"\bmail\.send\s*\(")
_NPM = re.compile(r"\bnpm\s*\(")
_MODELS = re.compile(r"class\s+\w+\s*\(\s*(?:\w+\.)?Model\s*\)|\.use_auth\s*\(")


@dataclasses.dataclass
class Facts:
    """What the app uses (read from its source)."""

    name: str = "app"
    app_file: str = "app.pyweb"
    database: str | None = None          # sqlite | postgres | mysql | env (URL from the environment) | None
    jobs: bool = False
    realtime: bool = False
    auth: bool = False
    providers: tuple = ()
    uploads: bool = False
    mail: bool = False
    migrations: bool = False
    models: bool = False                 # defines Models (tables managed by migrations)
    requirements: bool = False


def _scheme(raw):
    raw = raw.lower()
    if raw.startswith("sqlite"):
        return "sqlite"
    if raw.startswith(("postgres", "postgresql")):
        return "postgres"
    if raw.startswith("mysql"):
        return "mysql"
    return "env"


def read_app(app_file="app.pyweb"):
    """Facts about the app at ``app_file`` (its source and the files next to it)."""
    app_file = os.path.abspath(app_file)
    root = os.path.dirname(app_file)
    texts = []
    if os.path.isfile(app_file):
        with open(app_file, encoding="utf-8") as fh:
            texts.append(fh.read())
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                   and not os.path.exists(os.path.join(dirpath, d, "pyvenv.cfg"))]
        for fn in files:
            path = os.path.join(dirpath, fn)
            if path != app_file and fn.endswith((".py", ".pyweb")) and "migrations" not in dirpath.split(os.sep):
                try:
                    with open(path, encoding="utf-8") as fh:
                        texts.append(fh.read())
                except (OSError, UnicodeDecodeError):
                    pass
    src = "\n".join(texts)
    facts = Facts(name=_slug(os.path.basename(root) or "app"), app_file=os.path.basename(app_file))
    m = _DB_LITERAL.search(src)
    if m:
        facts.database = _scheme(m.group(2))
    elif _DB_ANY.search(src) or "DATABASE_URL" in src:
        facts.database = "env"
    facts.jobs = bool(_JOBS.search(src))
    facts.realtime = bool(_REALTIME.search(src))
    facts.auth = bool(_AUTH.search(src))
    found = _PROVIDERS.search(src)
    if facts.auth and found:
        facts.providers = tuple(sorted({p.strip(" '\"").lower() for p in found.group(1).split(",") if p.strip(" '\"")}))
    facts.uploads = bool(_UPLOADS.search(src))
    facts.models = bool(_MODELS.search(src))
    facts.mail = facts.auth or bool(_MAIL.search(src))
    mig = os.path.join(root, "migrations")
    facts.migrations = os.path.isdir(mig) and any(f.endswith((".py", ".sql")) for f in os.listdir(mig))
    facts.requirements = os.path.isfile(os.path.join(root, "requirements.txt"))
    return facts


def _slug(text):
    s = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return (s or "app")[:40]


@dataclasses.dataclass
class Plan:
    target: str
    name: str
    app_file: str = "app.pyweb"
    port: int = 8000
    replicas: int = 1
    processes_per_replica: int = 1        # WEB_CONCURRENCY: pyweb serve --workers
    database: str | None = None           # what production uses: sqlite | postgres | mysql | env | None
    provision: tuple = ()                 # services to create: postgres, mysql, redis, volume
    worker: bool = False                  # a separate `pyweb worker` process
    migrate: bool = False                 # a release step running `pyweb db upgrade`
    domain: str = ""
    region: str = ""
    image: str = ""
    extras: tuple = ()                    # pip extras for the image
    env: dict = dataclasses.field(default_factory=dict)       # plain settings
    secrets: dict = dataclasses.field(default_factory=dict)   # name -> how to get it
    reasons: list = dataclasses.field(default_factory=list)   # what was decided and why
    warnings: list = dataclasses.field(default_factory=list)  # what you should fix
    facts: Facts = dataclasses.field(default_factory=Facts)

    @property
    def redis(self):
        return "redis" in self.provision or "PYWEB_REDIS_URL" in self.secrets

    @property
    def serve_cmd(self):
        return ["pyweb", "serve", "dist", "--host", "0.0.0.0"]          # the port comes from $PORT

    @property
    def worker_cmd(self):
        return ["pyweb", "worker", "dist"]

    @property
    def migrate_cmd(self):
        return ["pyweb", "db", "upgrade", "--app", "dist/app.pyweb"]


def make_plan(target, facts, *, replicas=None, db=None, domain="", region="", with_services=(), port=8000,
              image="", name=None, processes=None, database_url=None):
    """Decide the production topology for ``facts`` on ``target``."""
    if target not in TARGETS:
        raise ValueError(f"unknown deploy target {target!r} ({'|'.join(TARGETS)})")
    with_services = {s.strip().lower() for s in with_services if s.strip()}
    p = Plan(target=target, name=_slug(name or facts.name), app_file=facts.app_file, port=port,
             domain=domain, region=region, facts=facts)
    p.image = image or f"{p.name}:latest"
    p.replicas = replicas or (2 if target == "k8s" else 1)
    p.processes_per_replica = processes or 1      # the asyncio server handles many connections in one process
    why, warn = p.reasons.append, p.warnings.append
    provision = set()

    # ------------------------------------------------------------ database
    wanted = db or facts.database
    if database_url:                                  # an existing database: use it, provision nothing
        p.env["DATABASE_URL"] = database_url
        wanted = None
        p.database = _scheme(database_url.split(":", 1)[0])
        why(f"the database at the DATABASE_URL you gave ({p.database})")
    if db is None and facts.database in ("sqlite", None) and "postgres" in with_services:
        wanted = "postgres"
    if wanted == "sqlite":
        ephemeral = target in PLATFORMS or target == "k8s"
        if db is None and (p.replicas > 1 or ephemeral):
            wanted = "postgres"
            if p.replicas > 1:
                why(f"Postgres instead of SQLite: {p.replicas} replicas need one shared database "
                    "(DATABASE_URL replaces the app's sqlite URL; no code change)")
            else:
                why(f"Postgres instead of SQLite: {target} replaces a container's disk on every deploy, "
                    "so a SQLite file would be lost (pass --db sqlite to keep it on a volume)")
        else:
            provision.add("volume")
            p.env["DATABASE_URL"] = "sqlite:////app/data/app.db"
            why("SQLite on a persistent volume at /app/data (one machine; switch with --db postgres)")
            if p.replicas > 1:
                warn("SQLite can't be shared by several machines: use --db postgres or --replicas 1")
                p.replicas = 1
            if target in ("render", "railway"):
                warn(f"{target}: add a persistent disk mounted at /app/data, or the database resets on deploy")
    if wanted in ("postgres", "mysql"):
        if target in ("docker", "k8s"):
            p.secrets["DATABASE_URL"] = f"your {wanted} connection URL (a managed database)"
            why(f"{'Postgres' if wanted == 'postgres' else 'MySQL'} you run or rent, connected through DATABASE_URL")
        else:
            provision.add(wanted)
            why(f"a {'Postgres' if wanted == 'postgres' else 'MySQL'} database, provisioned with the app "
                "and connected through DATABASE_URL")
    elif wanted == "env":
        if target in ("docker", "k8s"):
            p.secrets["DATABASE_URL"] = "your database URL"
            why("the database comes from DATABASE_URL")
        else:
            wanted = "postgres"
            provision.add("postgres")
            why("a Postgres database, provisioned with the app: it supplies the DATABASE_URL the app reads")
    if not database_url:
        p.database = wanted
    p.extras = tuple(sorted({"postgres": ("postgres",), "mysql": ("mysql",)}.get(p.database or "", ())))

    # --------------------------------------------------------------- redis
    total = p.replicas * p.processes_per_replica
    if "redis" in with_services or total > 1:
        provision.add("redis")
        uses = ["rate limits"] + (["live updates and presence"] if facts.realtime else [])
        why(f"Redis: {total} server processes share {' and '.join(uses)} through it"
            if total > 1 else "Redis, as asked")
        p.extras = tuple(sorted(set(p.extras) | {"redis"}))

    # ---------------------------------------------------------------- jobs
    durable = p.database is not None
    if (facts.jobs or facts.mail) and durable and "volume" not in provision:
        p.worker = True
        p.env["PYWEB_WORKER"] = "0"
        kinds = (["background jobs"] if facts.jobs else []) + (["emails (outbox)"] if facts.mail else [])
        why(f"a separate `pyweb worker` process runs {' and '.join(kinds)}; web processes only serve pages")
        if facts.mail:
            p.env["PYWEB_MAIL_OUTBOX"] = "1"
    elif facts.jobs and durable:
        why("background jobs run inside the server process (SQLite on one machine)")
    elif facts.jobs:
        warn("the app queues jobs but has no database: jobs would live in memory and be lost on restart")

    # ---------------------------------------------------------- migrations
    if p.database and (facts.models or facts.migrations):
        if facts.migrations:
            p.migrate = True
            why("migrations run once per deploy (`pyweb db upgrade`), under a lock, before new servers start")
        else:
            warn("no migrations/ folder: production doesn't create tables by itself. "
                 "Run `pyweb db diff --name initial` and commit migrations/")

    # ------------------------------------------------------------ uploads
    if facts.uploads and "volume" not in provision:
        p.secrets["PYWEB_STORAGE"] = "s3://bucket?region=... (uploaded files must outlive containers)"
        why("uploads go to object storage (PYWEB_STORAGE): container disks don't last")

    # ------------------------------------------------------------- settings
    p.provision = tuple(sorted(provision))
    p.env.update({"PYWEB_ENV": "production", "WEB_CONCURRENCY": str(p.processes_per_replica)})
    behind_proxy = target != "docker" or bool(domain)
    if behind_proxy:
        p.env["PYWEB_TRUST_PROXY"] = "1"
        p.env["PYWEB_COOKIE_SECURE"] = "1"
    p.secrets["PYWEB_AUTH_SECRET"] = 'generate: python -c "import secrets; print(secrets.token_hex(32))"'
    if domain:
        p.env["PYWEB_ORIGIN"] = f"https://{domain}"
    elif facts.auth or facts.mail:
        p.secrets["PYWEB_ORIGIN"] = "the public address, e.g. https://example.com (emailed links use it)"
    if facts.mail:
        p.secrets["PYWEB_MAIL_URL"] = "smtp://user:password@smtp.example.com:587"
    for prov in facts.providers:
        p.secrets[f"PYWEB_OAUTH_{prov.upper()}_ID"] = f"{prov} OAuth client id"
        p.secrets[f"PYWEB_OAUTH_{prov.upper()}_SECRET"] = f"{prov} OAuth client secret"
    return p


def describe(plan):
    """The plan as text: what will run, and why."""
    lines = [f"Deploy plan for {plan.name} ({plan.target})", ""]
    n = plan.processes_per_replica
    procs = f"{plan.replicas} × web" + (f" ({n} processes each)" if n > 1 else "")
    if plan.worker:
        procs += " + worker"
    lines.append(f"  runs:       {procs}")
    if plan.provision:
        lines.append(f"  provisions: {', '.join(plan.provision)}")
    if plan.migrate:
        lines.append("  on deploy:  pyweb db upgrade (once, before the new version starts)")
    lines += ["", "Why:"] + [f"  - {r}" for r in plan.reasons]
    if plan.warnings:
        lines += ["", "Fix before going live:"] + [f"  ! {w}" for w in plan.warnings]
    if plan.secrets:
        lines += ["", "Secrets to set:"] + [f"  {k}: {v}" for k, v in sorted(plan.secrets.items())]
    return "\n".join(lines)
