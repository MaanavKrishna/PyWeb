"""pyweb deploy: reading the app, the decisions the plan makes (and why), and valid files
for every target (parsed back as YAML, TOML and JSON)."""

import json
import subprocess
import sys

import pytest

from pyweb import deploy as D

yaml = pytest.importorskip("yaml")
try:
    import tomllib
except ImportError:          # Python 3.10: TOML files are checked on 3.11+
    tomllib = None

SHOP = '''
import os
from pyweb import App, server, live
from pyweb.models import Model, Field, File

app = App(database="sqlite:///shop.db")
auth = app.use_auth(providers=["github", "google"])

class Order(Model):
    item: str
    receipt: str | None = File(types=["application/pdf"])

@app.job(retries=3)
def ship(order_id: int): ...

@app.page("/")
def Home():
    orders = Order.query().live()
    <p>shop</p>
'''

STATIC = '''
from pyweb import App
app = App()

@app.page("/")
def Home():
    <p>hello</p>
'''


def make_app(tmp_path, source, migrations=True, name="shop"):
    root = tmp_path / name
    root.mkdir()
    (root / "app.pyweb").write_text(source)
    if migrations:
        (root / "migrations").mkdir()
        (root / "migrations" / "0001_initial.py").write_text("def up(op): pass\n")
    return root / "app.pyweb"


def test_reads_what_the_app_uses(tmp_path):
    f = D.read_app(make_app(tmp_path, SHOP))
    assert (f.name, f.database, f.jobs, f.realtime, f.auth, f.uploads, f.mail, f.migrations, f.models) == \
        ("shop", "sqlite", True, True, True, True, True, True, True)
    assert f.providers == ("github", "google")
    s = D.read_app(make_app(tmp_path, STATIC, migrations=False, name="site"))
    assert (s.database, s.jobs, s.realtime, s.auth, s.models) == (None, False, False, False, False)


def test_one_box_keeps_sqlite_on_a_volume(tmp_path):
    plan = D.make_plan("compose", D.read_app(make_app(tmp_path, SHOP)))
    assert plan.database == "sqlite" and "volume" in plan.provision and plan.replicas == 1
    assert plan.env["DATABASE_URL"] == "sqlite:////app/data/app.db"
    assert not plan.redis and not plan.worker          # jobs run inside the one server
    assert plan.migrate


@pytest.mark.parametrize("target", ["fly", "render", "railway", "k8s"])
def test_ephemeral_platforms_get_postgres(tmp_path, target):
    plan = D.make_plan(target, D.read_app(make_app(tmp_path, SHOP)))
    assert plan.database == "postgres"
    assert any("Postgres instead of SQLite" in r for r in plan.reasons)
    assert plan.worker and plan.env["PYWEB_WORKER"] == "0" and plan.env["PYWEB_MAIL_OUTBOX"] == "1"


def test_more_than_one_process_needs_redis_and_postgres(tmp_path):
    plan = D.make_plan("compose", D.read_app(make_app(tmp_path, SHOP)), replicas=3)
    assert plan.database == "postgres" and "postgres" in plan.provision and plan.redis
    assert any("3 replicas need one shared database" in r for r in plan.reasons)
    assert any("live updates" in r for r in plan.reasons)


def test_keeping_sqlite_on_purpose(tmp_path):
    plan = D.make_plan("fly", D.read_app(make_app(tmp_path, SHOP)), db="sqlite", replicas=2)
    assert plan.database == "sqlite" and plan.replicas == 1 and "volume" in plan.provision
    assert any("can't be shared" in w for w in plan.warnings)


def test_secrets_follow_the_app(tmp_path):
    plan = D.make_plan("render", D.read_app(make_app(tmp_path, SHOP)))
    assert {"PYWEB_AUTH_SECRET", "PYWEB_MAIL_URL", "PYWEB_ORIGIN", "PYWEB_STORAGE",
            "PYWEB_OAUTH_GITHUB_ID", "PYWEB_OAUTH_GOOGLE_SECRET"} <= set(plan.secrets)
    with_domain = D.make_plan("render", D.read_app(make_app(tmp_path, SHOP, name="b")), domain="shop.dev")
    assert with_domain.env["PYWEB_ORIGIN"] == "https://shop.dev" and "PYWEB_ORIGIN" not in with_domain.secrets


def test_missing_migrations_is_a_warning(tmp_path):
    plan = D.make_plan("fly", D.read_app(make_app(tmp_path, SHOP, migrations=False)))
    assert any("pyweb db diff" in w for w in plan.warnings) and not plan.migrate
    static = D.make_plan("fly", D.read_app(make_app(tmp_path, STATIC, migrations=False, name="s")))
    assert static.warnings == [] and static.database is None and not static.worker


def test_describe_explains_itself(tmp_path):
    text = D.describe(D.make_plan("compose", D.read_app(make_app(tmp_path, SHOP)), replicas=2))
    assert "2 × web + worker" in text and "provisions: postgres, redis" in text and "Why:" in text


# ------------------------------------------------------------------ files

@pytest.mark.parametrize("target", D.TARGETS)
@pytest.mark.parametrize("replicas", [1, 3])
def test_every_target_writes_valid_files(tmp_path, target, replicas):
    plan = D.make_plan(target, D.read_app(make_app(tmp_path, SHOP)), replicas=replicas, domain="shop.dev")
    files = D.files_for(plan)
    assert {"Dockerfile", ".dockerignore", ".env.example"} <= set(files)
    for name, text in files.items():
        if name.endswith(".yaml"):
            assert all(d is not None for d in yaml.safe_load_all(text))
        elif name.endswith(".toml") and tomllib is not None:
            tomllib.loads(text)
        elif name.endswith(".json"):
            json.loads(text)
    assert D.next_steps(plan)


def test_dockerfile_is_multi_stage_non_root_and_drains(tmp_path):
    plan = D.make_plan("compose", D.read_app(make_app(tmp_path, SHOP)), replicas=2)
    df = D.files_for(plan)["Dockerfile"]
    assert df.count("FROM ${PYTHON_IMAGE}") == 2 and "AS build" in df
    assert "USER 10001" in df and "STOPSIGNAL SIGTERM" in df and "HEALTHCHECK" in df
    assert 'EXTRA_PACKAGES="argon2-cffi>=23.1 psycopg[binary]>=3.1 redis>=5"' in df
    assert '"pyweb", "serve", "dist"' in df and "--production" in df and "cp -r migrations /dist/" in df


def test_compose_wires_everything(tmp_path):
    plan = D.make_plan("compose", D.read_app(make_app(tmp_path, SHOP)), replicas=2, domain="shop.dev")
    doc = yaml.safe_load(D.files_for(plan)["compose.yaml"])
    s = doc["services"]
    assert set(s) == {"migrate", "web", "worker", "postgres", "redis", "caddy"}
    assert s["web"]["deploy"]["replicas"] == 2 and "ports" not in s["web"]
    assert s["web"]["depends_on"]["migrate"] == {"condition": "service_completed_successfully"}
    assert s["worker"]["command"] == ["pyweb", "worker", "dist"] and s["worker"]["healthcheck"] == {"disable": True}
    assert s["web"]["environment"]["PYWEB_REDIS_URL"] == "redis://redis:6379/0"
    assert s["web"]["environment"]["DATABASE_URL"].startswith("postgresql://pyweb:${DB_PASSWORD")
    assert s["caddy"]["ports"] == ["80:80", "443:443"]
    caddy = D.files_for(plan)["Caddyfile"]
    assert "shop.dev {" in caddy and "dynamic a web 8000" in caddy


def test_k8s_rolls_out_safely(tmp_path):
    plan = D.make_plan("k8s", D.read_app(make_app(tmp_path, SHOP)), replicas=3, domain="shop.dev")
    docs = {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(D.files_for(plan)["k8s.yaml"])}
    web = docs[("Deployment", "shop")]["spec"]
    assert web["replicas"] == 3 and web["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    pod = web["template"]["spec"]
    assert pod["initContainers"][0]["command"] == ["pyweb", "db", "upgrade", "--app", "dist/app.pyweb"]
    c = pod["containers"][0]
    assert c["securityContext"]["runAsNonRoot"] and c["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert c["lifecycle"]["preStop"]["exec"]["command"] == ["sleep", "5"]
    assert ("Deployment", "shop-worker") in docs and ("PodDisruptionBudget", "shop") in docs
    assert ("HorizontalPodAutoscaler", "shop") in docs
    ingress = docs[("Ingress", "shop")]
    assert ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/proxy-read-timeout"] == "3600"


@pytest.mark.skipif(tomllib is None, reason="tomllib needs Python 3.11")
def test_fly_config(tmp_path):
    plan = D.make_plan("fly", D.read_app(make_app(tmp_path, SHOP)), region="ams")
    conf = tomllib.loads(D.files_for(plan)["fly.toml"])
    assert conf["primary_region"] == "ams" and conf["deploy"]["release_command"].startswith("pyweb db upgrade")
    assert set(conf["processes"]) == {"web", "worker"} and conf["http_service"]["processes"] == ["web"]
    assert conf["http_service"]["checks"][0]["path"] == "/readyz" and conf["env"]["PYWEB_WORKER"] == "0"
    steps = "\n".join(D.next_steps(plan))
    assert "fly postgres create" in steps and "fly scale count worker=1" in steps


def test_render_blueprint(tmp_path):
    plan = D.make_plan("render", D.read_app(make_app(tmp_path, SHOP)), replicas=2)
    doc = yaml.safe_load(D.files_for(plan)["render.yaml"])
    web = doc["services"][0]
    assert web["preDeployCommand"].startswith("pyweb db upgrade") and web["numInstances"] == 2
    keys = {e["key"]: e for e in web["envVars"]}
    assert keys["PYWEB_AUTH_SECRET"] == {"key": "PYWEB_AUTH_SECRET", "generateValue": True}
    assert "fromDatabase" in keys["DATABASE_URL"] and "fromService" in keys["PYWEB_REDIS_URL"]
    assert {s["type"] for s in doc["services"]} == {"web", "worker", "keyvalue"} and doc["databases"]


def test_railway_config(tmp_path):
    plan = D.make_plan("railway", D.read_app(make_app(tmp_path, SHOP)))
    files = D.files_for(plan)
    web, worker = json.loads(files["railway.json"]), json.loads(files["railway.worker.json"])
    assert web["deploy"]["preDeployCommand"] == ["pyweb db upgrade --app dist/app.pyweb"]
    assert "--port" not in web["deploy"]["startCommand"]                 # Railway's $PORT is used
    assert worker["deploy"]["startCommand"] == "pyweb worker dist"


def test_yaml_writer_quotes_what_needs_it():
    text = D.targets.yaml({"a": "yes", "b": "1.0", "c": "x: y", "d": "", "e": [1, {"f": True}], "g": {}})
    assert yaml.safe_load(text) == {"a": "yes", "b": "1.0", "c": "x: y", "d": "", "e": [1, {"f": True}], "g": {}}


# -------------------------------------------------------------------- CLI

def test_cli_prints_the_plan_and_writes_files(tmp_path):
    app = make_app(tmp_path, SHOP)
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "pyweb.cli", "deploy", "fly", "--file", str(app), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "Deploy plan for shop (fly)" in r.stdout and "Why:" in r.stdout and "Next:" in r.stdout
    assert (out / "fly.toml").exists() and (out / ".dockerignore").exists()
    plan_only = subprocess.run([sys.executable, "-m", "pyweb.cli", "deploy", "fly", "--file", str(app), "--plan",
                                "--out", str(tmp_path / "none")], capture_output=True, text=True)
    assert plan_only.returncode == 0 and not (tmp_path / "none").exists()


def test_cli_check_fails_on_warnings(tmp_path):
    app = make_app(tmp_path, SHOP, migrations=False)
    r = subprocess.run([sys.executable, "-m", "pyweb.cli", "deploy", "fly", "--file", str(app), "--check",
                        "--out", str(tmp_path / "o")], capture_output=True, text=True)
    assert r.returncode == 1 and "pyweb db diff" in r.stdout


def test_database_url_wins_over_the_apps_own(tmp_path, monkeypatch):
    from pyweb import App
    from pyweb import models as M
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'prod.db'}")
    App(database=f"sqlite:///{tmp_path / 'dev.db'}")
    assert M.database().path.endswith("prod.db")
    M._state.db = None


def test_check_production_knows_what_the_app_needs(tmp_path, monkeypatch):
    from pyweb.cli import _topology_problems
    for k in ("PYWEB_MAIL_URL", "PYWEB_STORAGE", "PYWEB_WORKER", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    app = make_app(tmp_path, SHOP, migrations=False)
    problems = "\n".join(_topology_problems(str(app.parent)))
    assert "no migrations/" in problems and "PYWEB_MAIL_URL" in problems and "PYWEB_STORAGE" in problems
    monkeypatch.setenv("PYWEB_MAIL_URL", "smtp://x")
    monkeypatch.setenv("PYWEB_STORAGE", "s3://bucket")
    monkeypatch.setenv("PYWEB_WORKER", "0")
    problems = "\n".join(_topology_problems(str(app.parent)))
    assert "PYWEB_MAIL_URL" not in problems and "pyweb worker" in problems
    assert _topology_problems(str(tmp_path / "nothing-here")) == []


def test_check_production_reports_pending_migrations(tmp_path, monkeypatch):
    from pyweb.cli import _topology_problems
    root = tmp_path / "m"
    root.mkdir()
    (root / "app.pyweb").write_text(SHOP)
    (root / "migrations").mkdir()
    (root / "migrations" / "0001_create_x.py").write_text(
        "def up(op):\n    op.run_sql('CREATE TABLE x (id INTEGER)')\n\n\ndef down(op):\n    op.run_sql('DROP TABLE x')\n")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'p.db'}")
    assert any("1 migration(s) not applied yet" in p for p in _topology_problems(str(root)))
