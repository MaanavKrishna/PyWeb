"""Jobs in a real app: enqueued from server functions (rolled back with them), the mail
outbox, nightly cleanup, /admin retry, the CLI, and a worker killed mid-job."""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from pyweb import auth as pyauth
from pyweb import jobs, mail
from pyweb import models as M
from pyweb.hosting import Site
from pyweb.jobs import core

APP = '''
from pyweb import App, server, ValidationError
from pyweb.models import Model, Field

app = App(database="sqlite:///{db}", title="Shop")
auth = app.use_auth(signup="instant")

class Order(Model):
    item: str = Field(max=40)
    status: str = "new"

@app.job(retries=2, backoff=0.001, max_backoff=0.002)
def ship(order_id: int):
    order = Order.get(order_id)
    if order.item == "explode":
        raise RuntimeError("warehouse on fire")
    order.status = "shipped"
    order.save()
    return order.status

@server
def place(item: str) -> int:
    order = Order.create(item=item)
    ship.enqueue(order.id)
    if item == "invalid":
        raise ValidationError({{"item": "not sold here"}})
    return order.id

@app.page("/")
def Home():
    <p>shop</p>
'''


def rpc(site, name, **args):
    status, _, body = site.respond("POST", f"/__pyweb/rpc/{name}", {"Content-Type": "application/json",
                                                                     "Host": "localhost"},
                                   json.dumps({"args": args}).encode())
    return status, json.loads(body)


@pytest.fixture
def shop(tmp_path, monkeypatch):
    jobs.stop_all(0)                      # e.g. a `pyweb dev` another test ran in this process
    monkeypatch.setattr(M._state, "db", None)
    monkeypatch.setattr(pyauth, "_versions", None)
    monkeypatch.setitem(pyauth.KIT, "kit", None)
    monkeypatch.setenv("PYWEB_BREACHED_PASSWORDS", "0")
    monkeypatch.delenv("PYWEB_ENV", raising=False)
    monkeypatch.delenv("PYWEB_JOBS", raising=False)
    jobs.use_backend(None)
    before = dict(core.REGISTRY)
    (tmp_path / "app.pyweb").write_text(APP.format(db=tmp_path / "shop.db"))
    site = Site(str(tmp_path / "app.pyweb"), debug=True, rate_limit=False)
    yield site
    jobs.use_backend(None)
    core.REGISTRY.clear()
    core.REGISTRY.update(before)
    M._state.db = None


def orders(site):
    Order = next(m for m in site.app.models() if m.__name__ == "Order")
    return Order


def test_server_functions_queue_jobs_that_commit_with_them(shop):
    status, body = rpc(shop, "place", item="book")
    assert status == 200
    status, _ = rpc(shop, "place", item="invalid")         # the order and its job roll back together
    assert status == 422
    store = jobs.backend()
    assert type(store).__name__ == "DatabaseBackend"
    queued = store.list(name="ship")
    assert len(queued) == 1 and queued[0]["args"] == {"args": [body["result"]], "kwargs": {}}
    assert jobs.Worker(schedule=False).drain() == 1
    Order = orders(shop)
    assert Order.get(body["result"]).status == "shipped"
    tables = {m._meta.table for m in shop.app.models()}
    assert "orders" in tables and "pyweb_jobs" not in tables      # PyWeb's own table: not in your migrations


def test_admin_shows_dead_jobs_and_retries_them(shop):
    from tests.test_authkit import PASSWORD, Browser
    from pyweb.authkit import User, passwords
    User(email="boss@example.com", name="Boss", roles=["admin"], email_verified=True,
         password_hash=passwords.hash_password(PASSWORD)).save()
    _, body = rpc(shop, "place", item="explode")
    w = jobs.Worker(schedule=False)
    end = time.time() + 5
    while time.time() < end and jobs.backend().list(state="dead") == []:
        w.tick()
        time.sleep(0.01)
    [dead] = jobs.backend().list(state="dead")
    assert "warehouse on fire" in dead["last_error"]
    b = Browser(shop)
    b.get("/login")
    assert b.post("/login", {"email": "boss@example.com", "password": PASSWORD, "action": "password"})[0] == 303
    status, _, html = b.get(f"/admin/pyweb_jobs/{dead['id']}")
    assert status == 200 and "Retry this job" in html and "warehouse on fire" in html
    assert b.post(f"/admin/pyweb_jobs/{dead['id']}", {"action": "retry"})[0] == 303
    assert jobs.backend().get(dead["id"])["state"] == "queued"


def test_mail_goes_through_the_outbox(shop, monkeypatch):
    sent, failures = [], [1]

    class Flaky:
        def send(self, message):
            if failures:
                failures.pop()
                raise ConnectionError("smtp down")
            sent.append(message)

    mail.use_sender(Flaky())
    monkeypatch.setenv("PYWEB_MAIL_OUTBOX", "1")
    try:
        from pyweb.db import request_scope
        db = M.database()
        M.ensure_tables(db, [orders(shop)])
        with pytest.raises(RuntimeError):
            with request_scope():
                db.execute("insert into orders (item, status) values ('x', 'new')")
                mail.send("a@example.com", "Rolled back", "never sent")
                raise RuntimeError
        with request_scope():
            db.execute("insert into orders (item, status) values ('y', 'new')")
            mail.send("b@example.com", "Hello", "sent after a retry")
        assert sent == []                                    # queued, not sent inline
        w = jobs.Worker(schedule=False)
        end = time.time() + 5
        while time.time() < end and not sent:
            w.tick()
            time.sleep(0.01)
        assert [m.subject for m in sent] == ["Hello"]
        [rec] = jobs.backend().list(name="pyweb.mail.deliver")
        assert rec["state"] == "done" and rec["attempts"] == 2
    finally:
        mail.use_sender(None)


def test_console_mail_stays_immediate(shop):
    mail.OUTBOX.clear()
    jobs.Worker(schedule=False).start().stop(0)
    mail.send("a@example.com", "Hi", "dev")
    assert [m.subject for m in mail.OUTBOX] == ["Hi"]


def test_nightly_cleanup(shop, monkeypatch):
    import datetime as dt

    from pyweb.authkit import AuthEvent, AuthToken
    from pyweb.authkit import models as kit_models
    # issue() clears old links itself on 1 call in 100; keep that out of this test.
    monkeypatch.setattr(kit_models.secrets, "randbelow", lambda n: 1)
    AuthToken.issue("reset", email="x@example.com", ttl=-3 * 86400)
    AuthToken.issue("reset", email="y@example.com", ttl=600)
    old = AuthEvent.create(kind="login")
    AuthEvent.where(id=old.id).update(created_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400))
    AuthEvent.create(kind="login")
    _, body = rpc(shop, "place", item="book")
    jobs.Worker(schedule=False).drain()
    store = jobs.backend()
    store.db.execute("update pyweb_jobs set finished_at = ? where name = 'ship'", (time.time() - 10 * 86400,))
    result = core.REGISTRY["pyweb.cleanup"].run()
    assert result == {"jobs": 1, "auth_tokens": 1, "auth_events": 1}
    assert AuthToken.query().count() == 1 and AuthEvent.query().count() == 1


def test_cli_lists_retries_and_runs(shop, tmp_path, capsys):
    from pyweb.cli import main
    rpc(shop, "place", item="explode")
    app = str(tmp_path / "app.pyweb")
    main(["jobs", "run", "--app", app, "--timeout", "20"])
    main(["jobs", "list", "--app", app, "--state", "dead"])
    out = capsys.readouterr().out
    job_id = next(line.split()[0] for line in out.splitlines() if " dead " in line)
    assert "RuntimeError: warehouse on fire" in out
    main(["jobs", "retry", job_id, "--app", app])
    assert "queued again" in capsys.readouterr().out
    main(["jobs", "purge", "--app", app, "--days", "0"])
    assert "removed 0" in capsys.readouterr().out


KILL_APP = '''
import time
from pyweb import App
from pyweb.jobs import current

app = App(database="sqlite:///{db}")

@app.job(retries=2, timeout=1)
def slow(path: str):
    with open(path, "a") as fh:
        fh.write(f"start {{current().attempt}}\\n")
    if current().attempt == 1:
        time.sleep(60)                    # the worker is killed during this
    with open(path, "a") as fh:
        fh.write("done\\n")
'''


def test_a_killed_worker_doesnt_lose_the_job(tmp_path, monkeypatch):
    jobs.stop_all(0)
    monkeypatch.setattr(M._state, "db", None)
    jobs.use_backend(None)
    app = tmp_path / "app.pyweb"
    app.write_text(KILL_APP.format(db=tmp_path / "k.db"))
    log = tmp_path / "log.txt"
    from pyweb.app_loader import LoadedApp
    LoadedApp(str(app))
    core.REGISTRY["slow"].enqueue(str(log))
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    first = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "worker", str(app), "--no-schedule"], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if log.exists() and "start 1" in log.read_text():
                break
            time.sleep(0.1)
        assert "start 1" in log.read_text()
        first.send_signal(signal.SIGKILL)                # no chance to hand the job back
        first.wait(5)
        store = jobs.backend()
        [rec] = store.list(name="slow")
        assert rec["state"] == "running"
        store.db.execute("update pyweb_jobs set locked_until = ?", (time.time() - 1,))   # the lease runs out
        second = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "worker", str(app), "--no-schedule"],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                if "done" in log.read_text():
                    break
                time.sleep(0.1)
        finally:
            second.send_signal(signal.SIGTERM)
            second.wait(10)
        assert log.read_text().splitlines() == ["start 1", "start 2", "done"]
        assert store.get(rec["id"])["state"] == "done" and store.get(rec["id"])["attempts"] == 2
    finally:
        if first.poll() is None:
            first.kill()
        jobs.use_backend(None)
        M._state.db = None
        core.REGISTRY.pop("slow", None)


def test_sigterm_hands_running_jobs_back(tmp_path, monkeypatch):
    jobs.stop_all(0)
    monkeypatch.setattr(M._state, "db", None)
    jobs.use_backend(None)
    app = tmp_path / "app.pyweb"
    app.write_text(KILL_APP.format(db=tmp_path / "t.db").replace("timeout=1", "timeout=120"))
    log = tmp_path / "log.txt"
    from pyweb.app_loader import LoadedApp
    LoadedApp(str(app))
    core.REGISTRY["slow"].enqueue(str(log))
    proc = subprocess.Popen([sys.executable, "-m", "pyweb.cli", "worker", str(app), "--no-schedule",
                             "--grace", "0.5"], env=dict(os.environ, PYTHONPATH=os.getcwd()),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            if log.exists() and "start 1" in log.read_text():
                break
            time.sleep(0.1)
        proc.send_signal(signal.SIGTERM)
        out = proc.communicate(timeout=15)[0].decode()
        assert "1 job(s) handed back" in out
        [rec] = jobs.backend().list(name="slow")
        assert rec["state"] == "queued" and rec["attempts"] == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        jobs.use_backend(None)
        M._state.db = None
        core.REGISTRY.pop("slow", None)
