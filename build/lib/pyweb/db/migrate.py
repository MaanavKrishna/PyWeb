"""Migrations: versioned schema changes, safe to run from many servers at once.

``migrations/`` holds numbered files applied in order:

* ``0003_add_tags.py`` (written by ``pyweb db diff``) with ``up(op)`` and
  ``down(op)`` functions; ``op`` is :class:`pyweb.db.schema.Operations`.
* ``001_name.up.sql`` / ``001_name.down.sql`` pairs (plain SQL, as in 0.4).

Applied migrations are recorded in the ``pyweb_migrations`` table, so
``upgrade`` is idempotent. Every run holds a lock (a Postgres advisory lock,
MySQL ``GET_LOCK``, or a row in ``pyweb_locks``), so when ten servers start
together one migrates and the rest wait, then find nothing to do.

Migrations marked ``contract = True`` remove things (columns, old copies of
renamed columns). They wait for ``upgrade --contract``, run once every
server is on the new code, so a rolling deploy never breaks the old code
still serving requests.
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import time
import uuid
from contextlib import contextmanager

from . import schema as S
from .dialect import dialect_of

JOURNAL_DDL = (
    "CREATE TABLE IF NOT EXISTS pyweb_migrations ("
    "version VARCHAR(255) PRIMARY KEY, applied_at VARCHAR(64) NOT NULL)"
)

_FILE_RE = re.compile(r"^(\d+)_([A-Za-z0-9_]+)\.(up|down)\.sql$")
_PY_RE = re.compile(r"^(\d+)_([A-Za-z0-9_]+)\.py$")
LOCK_NAME = "pyweb_migrate"


class Migration:
    """One migration file."""

    def __init__(self, version, name, kind, path=None, *, up=None, down=None, contract=False,
                 transactional=True, replaces=(), schema=None, doc=""):
        self.version, self.name, self.kind, self.path = version, name, kind, path
        self._up, self._down = up, down
        self.contract = contract
        self.transactional = transactional
        self.replaces = list(replaces)
        self.schema = schema
        self.doc = doc

    @property
    def label(self):
        return f"{self.version}_{self.name}"

    def up(self, op):
        if self._up is None:
            raise RuntimeError(f"{self.label} has no up()")
        self._up(op)

    def down(self, op):
        if self._down is None:
            raise RuntimeError(f"{self.label} can't be undone (it has no down())")
        self._down(op)

    def __repr__(self):
        return f"<Migration {self.label}{' contract' if self.contract else ''}>"


def _sql_runner(outdir, version, name, direction):
    def run(op):
        for stmt in _read(outdir, version, name, direction):
            op.execute(stmt)
    return run


def _read(outdir, version, name, direction):
    path = os.path.join(outdir, f"{version}_{name}.{direction}.sql")
    with open(path) as fh:
        return [s.strip() for s in _split(fh.read()) if s.strip()]


def _split(script):
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";")]


def _load_py(path):
    ns = {"__file__": path, "__name__": f"pyweb_migration_{os.path.basename(path)[:-3]}"}
    with open(path, encoding="utf-8") as fh:
        code = compile(fh.read(), path, "exec")
    exec(code, ns)  # noqa: S102 - the app's own migration file
    return ns


def discover(outdir):
    """Every migration in ``outdir``, in the order they apply."""
    found = []
    if not os.path.isdir(outdir):
        return found
    pairs: dict = {}
    for fn in sorted(os.listdir(outdir)):
        m = _FILE_RE.match(fn)
        if m:
            pairs.setdefault((m.group(1), m.group(2)), set()).add(m.group(3))
            continue
        m = _PY_RE.match(fn)
        if m:
            path = os.path.join(outdir, fn)
            ns = _load_py(path)
            found.append(Migration(m.group(1), m.group(2), "py", path, up=ns.get("up"), down=ns.get("down"),
                                   contract=bool(ns.get("contract")), transactional=ns.get("transactional", True),
                                   replaces=ns.get("replaces", ()), schema=ns.get("schema"),
                                   doc=(ns.get("__doc__") or "").strip()))
    for (version, name), sides in pairs.items():
        if sides != {"up", "down"}:
            raise ValueError(f"migration {version}_{name} is missing "
                             f"{'down' if 'up' in sides else 'up'} file")
        found.append(Migration(version, name, "sql", up=_sql_runner(outdir, version, name, "up"),
                               down=_sql_runner(outdir, version, name, "down")))
    labels = [m.label for m in found]
    dupes = {v for v in labels if labels.count(v) > 1}
    if dupes:
        raise ValueError(f"two migrations share a name: {', '.join(sorted(dupes))}")
    return sorted(found, key=lambda m: (int(m.version), m.name))


def _discover(outdir):
    """0.4 helper: ``[(version, name)]`` sorted."""
    return [(m.version, m.name) for m in discover(outdir)]


def _db(db_or_url):
    from pyweb.db import connect
    return connect(db_or_url) if isinstance(db_or_url, str) else db_or_url


def _applied(db) -> dict:
    db.execute(JOURNAL_DDL)
    res = db.execute("SELECT version, applied_at FROM pyweb_migrations")
    rows = res.fetchall() if hasattr(res, "fetchall") else res
    out = {}
    for row in rows or []:
        if isinstance(row, dict):
            out[row.get("version")] = row.get("applied_at")
        else:
            out[row[0]] = row[1] if len(row) > 1 else None
    return out


def _record(db, label):
    db.execute("INSERT INTO pyweb_migrations (version, applied_at) VALUES (?, ?)",
               (label, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))


def _unrecord(db, label):
    db.execute("DELETE FROM pyweb_migrations WHERE version = ?", (label,))


# ----------------------------------------------------------------------- lock

def _lock_key(name):
    return int(hashlib.sha256(name.encode()).hexdigest()[:15], 16)


def _scalar(conn, sql, params):
    if hasattr(conn, "cursor"):
        cur = conn.cursor()
        cur.execute(sql, params)
    else:
        cur = conn.execute(sql, params)
    row = cur.fetchone()
    try:
        cur.close()
    except Exception:
        pass
    return row[0] if row else None


@contextmanager
def lock(db, name=LOCK_NAME, *, timeout=600.0, poll=0.25):
    """Hold a lock shared by every process using ``db`` (one migrator at a time)."""
    d = dialect_of(db)
    deadline = time.monotonic() + timeout
    pool = getattr(db, "_pool_size", 1)
    if d.name in ("postgres", "mysql") and pool > 1 and hasattr(db, "_acquire"):
        conn = db._acquire()
        try:
            if d.name == "postgres":
                key = _lock_key(name)
                while not _scalar(conn, "SELECT pg_try_advisory_lock(%s)", (key,)):
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"another process is still migrating (waited {timeout:.0f}s)")
                    time.sleep(poll)
            else:
                while _scalar(conn, "SELECT GET_LOCK(%s, 0)", (name,)) != 1:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"another process is still migrating (waited {timeout:.0f}s)")
                    time.sleep(poll)
            try:
                if not getattr(conn, "autocommit", True):
                    conn.commit()
            except Exception:
                pass
            try:
                yield
            finally:
                try:
                    if d.name == "postgres":
                        _scalar(conn, "SELECT pg_advisory_unlock(%s)", (_lock_key(name),))
                    else:
                        _scalar(conn, "SELECT RELEASE_LOCK(%s)", (name,))
                except Exception:
                    pass
        finally:
            db._pool.put(conn)
        return
    # A row in pyweb_locks: works everywhere, including SQLite files shared by processes.
    real = {"sqlite": "REAL", "postgres": "DOUBLE PRECISION", "mysql": "DOUBLE"}[d.name]
    db.execute(f"CREATE TABLE IF NOT EXISTS pyweb_locks (name VARCHAR(64) PRIMARY KEY, "
               f"owner VARCHAR(80) NOT NULL, expires_at {real} NOT NULL)")
    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"[:80]
    ttl = max(timeout, 60.0) * 2
    while True:
        db.execute("DELETE FROM pyweb_locks WHERE name = ? AND expires_at < ?", (name, time.time()))
        try:
            db.execute("INSERT INTO pyweb_locks (name, owner, expires_at) VALUES (?, ?, ?)",
                       (name, owner, time.time() + ttl))
            break
        except Exception:  # noqa: BLE001 - held by someone else
            if time.monotonic() > deadline:
                holder = db.execute("SELECT owner FROM pyweb_locks WHERE name = ?", (name,)).fetchone()
                raise TimeoutError(f"another process is still migrating ({holder[0] if holder else '?'}; "
                                   f"waited {timeout:.0f}s)") from None
            time.sleep(poll)
    try:
        yield
    finally:
        db.execute("DELETE FROM pyweb_locks WHERE name = ? AND owner = ?", (name, owner))


# ------------------------------------------------------------------ running

def _run(db, mig, direction):
    op = S.Operations(db)
    step = mig.up if direction == "up" else mig.down
    if mig.transactional:
        with db.migration_transaction():
            step(op)
            (_record if direction == "up" else _unrecord)(db, mig.label)
    else:
        step(op)
        (_record if direction == "up" else _unrecord)(db, mig.label)


def _plan(migrations, done):
    """Which migrations to run and which squashed ones only to record."""
    skip, record_only = set(), set()
    for m in migrations:
        if not m.replaces or m.label in done:
            if m.replaces:
                skip.update(m.replaces)
            continue
        have = [r for r in m.replaces if r in done]
        if len(have) == len(m.replaces):
            record_only.add(m.label)
            skip.update(m.replaces)
        elif not have:
            skip.update(m.replaces)
        else:
            record_only.add(m.label)            # finish the old ones individually, then mark it
    return skip, record_only


def pending(db_or_url, outdir="migrations"):
    db = _db(db_or_url)
    done = _applied(db)
    migrations = discover(outdir)
    skip, _ = _plan(migrations, done)
    return [m for m in migrations if m.label not in done and m.label not in skip]


def upgrade(db_or_url, outdir="migrations", *, target=None, contract=False, use_lock=True):
    """Apply pending migrations in order (up to ``target`` if given). Returns their labels.

    Contract migrations (and everything after one) wait for ``contract=True``.
    """
    db = _db(db_or_url)
    migrations = discover(outdir)
    if target is not None and target not in {m.label for m in migrations}:
        raise ValueError(f"no migration named {target!r}")
    ctx = lock(db) if use_lock else _null()
    applied = []
    with ctx:
        done = _applied(db)
        skip, record_only = _plan(migrations, done)
        for m in migrations:
            if m.label in done or m.label in skip:
                if target == m.label:
                    break
                continue
            if m.label in record_only:
                _record(db, m.label)
                applied.append(m.label)
            else:
                if m.contract and not contract:
                    break
                _run(db, m, "up")
                applied.append(m.label)
            if target == m.label:
                break
    return applied


def migrate(db_or_url, outdir="migrations"):
    """Apply every pending migration (including contract ones). Returns their labels."""
    return upgrade(db_or_url, outdir, contract=True)


@contextmanager
def _null():
    yield


def downgrade(db_or_url, outdir="migrations", *, steps=1, to=None, use_lock=True):
    """Undo the latest ``steps`` migrations, or everything applied after ``to``."""
    db = _db(db_or_url)
    migrations = {m.label: m for m in discover(outdir)}
    with (lock(db) if use_lock else _null()):
        done = _applied(db)
        ordered = [label for label in migrations if label in done]
        ordered.sort(key=lambda label: (int(label.split("_", 1)[0]), label))
        if to is not None:
            if to not in ordered:
                raise ValueError(
                    f"rollback --to {to!r}: not an applied migration; "
                    f"applied: {', '.join(ordered) or 'none'}")
            targets = ordered[ordered.index(to) + 1:]
        else:
            targets = ordered[max(len(ordered) - max(steps, 0), 0):]
        rolled = []
        for label in reversed(targets):
            _run(db, migrations[label], "down")
            rolled.append(label)
    return rolled


def rollback(db_or_url, outdir="migrations", *, steps=1, to=None):
    """0.4 name for :func:`downgrade`."""
    return downgrade(db_or_url, outdir, steps=steps, to=to)


def status(db_or_url, outdir="migrations"):
    """``[(label, applied)]`` for every migration."""
    db = _db(db_or_url)
    done = _applied(db)
    migrations = discover(outdir)
    skip, _ = _plan(migrations, done)
    return [(m.label, m.label in done or (m.label in skip and any(
        s.label in done and m.label in s.replaces for s in migrations))) for m in migrations]


def report(db_or_url, outdir="migrations", models=None):
    """A readable status: applied, pending (and which are contract steps), drift."""
    db = _db(db_or_url)
    done = _applied(db)
    migrations = discover(outdir)
    lines = []
    for m in migrations:
        mark = "x" if m.label in done else " "
        extra = "  (contract: run with --contract after deploying)" if m.contract and m.label not in done else ""
        lines.append(f"[{mark}] {m.label}{extra}")
    if not migrations:
        lines.append("no migrations yet (pyweb db diff writes the first one)")
    if models is not None:
        live = S.introspect(db)
        plan = S.diff(live, S.from_models(models), dialect_of(db))
        if plan and not pending(db, outdir):
            lines.append("models differ from the database: run `pyweb db diff` to write a migration")
            lines += [f"  - {op.describe()}" for op in plan.ops]
    return "\n".join(lines)


# ----------------------------------------------------------------- writing

TEMPLATE = '''"""{doc}"""

revision = {label!r}
schema = {schema!r}
contract = {contract!r}{extra}


def up(op):
    {up}


def down(op):
    {down}
'''


def _next_number(outdir):
    numbers = [int(m.version) for m in discover(outdir)]
    return (max(numbers) + 1) if numbers else 1


def _slug(name):
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_").lower() or "migration"


def _body(ops):
    if not ops:
        return "pass"
    return "\n    ".join(op.to_code().replace("\n", "\n    ") for op in ops)


def _down_body(ops):
    lines = []
    for op in reversed(ops):
        rev = op.reverse()
        if rev is None:
            lines.append(f"pass  # can't undo automatically: {op.describe()}")
        else:
            lines.append(rev.to_code().replace("\n", "\n    "))
    return "\n    ".join(lines) or "pass"


def write_migration(outdir, name, ops, *, doc=None, schema=None, contract=False, extra=""):
    os.makedirs(outdir, exist_ok=True)
    number = _next_number(outdir)
    label = f"{number:04d}_{_slug(name)}"
    path = os.path.join(outdir, label + ".py")
    text = TEMPLATE.format(doc=(doc or name).replace('"""', "'''"), label=label, schema=schema, contract=contract,
                           extra=extra, up=_body(ops), down=_down_body(ops))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def make_migration(db_or_url, models, outdir="migrations", name=None, *, renames=None, table_renames=None,
                   allow_destructive=False):
    """Compare ``models`` with the database and write the migration(s) between them.

    Returns ``(paths, plan)``; ``paths`` is empty when nothing changed. Safe
    steps go in one file; destructive ones go in a separate *contract* file
    unless ``allow_destructive`` (single-server apps that deploy with downtime).
    """
    db = _db(db_or_url)
    waiting = pending(db, outdir)
    if waiting:
        raise RuntimeError("apply the pending migrations first (pyweb db upgrade): "
                           + ", ".join(m.label for m in waiting))
    target = S.from_models(models)
    plan = S.diff(S.introspect(db), target, dialect_of(db), renames=renames, table_renames=table_renames)
    if not plan:
        return [], plan
    digest = S.schema_hash(target)
    described = [op.describe() for op in plan.ops]
    auto_name = name or _auto_name(plan)
    paths = []
    if allow_destructive or not plan.contract:
        doc = auto_name + "\n\n" + "\n".join(f"- {d}" for d in described)
        paths.append(write_migration(outdir, auto_name, plan.ops, doc=doc, schema=digest))
    else:
        if plan.expand:
            doc = auto_name + "\n\n" + "\n".join(f"- {op.describe()}" for op in plan.expand)
            paths.append(write_migration(outdir, auto_name, plan.expand, doc=doc, schema=digest))
        doc = (f"{auto_name} (contract)\n\nRun with `pyweb db upgrade --contract` once every server runs the "
               f"new code.\n\n" + "\n".join(f"- {op.describe()}" for op in plan.contract))
        paths.append(write_migration(outdir, auto_name + "_contract", plan.contract, doc=doc, schema=digest,
                                     contract=True))
    return paths, plan


def _auto_name(plan):
    ops = plan.ops
    if ops and all(isinstance(op, S.CreateTable) for op in ops):
        return "create_" + "_".join(op.table.name for op in ops)[:44]
    if len(ops) == 1:
        return re.sub(r"[^a-z0-9]+", "_", ops[0].describe().split(" (")[0].lower()).strip("_")[:50]
    created = [op.table.name for op in ops if isinstance(op, S.CreateTable)]
    if created and len(created) == len([o for o in ops if not isinstance(o, S.CreateIndex)]):
        return "create_" + "_".join(created)[:44]
    return "update_schema"


def adopt(db_or_url, outdir="migrations"):
    """Record an existing database as migration 0001 without changing it."""
    db = _db(db_or_url)
    if discover(outdir):
        raise RuntimeError(f"{outdir} already has migrations; adopt is for databases that have none")
    tables = S.introspect(db)
    ops = [S.CreateTable(t) for t in S.ordered(tables)]
    path = write_migration(outdir, "initial", ops, doc="initial schema (adopted from the existing database)",
                           schema=S.schema_hash(tables))
    with lock(db):
        _applied(db)
        _record(db, os.path.basename(path)[:-3])
    return path


def squash(db_or_url, outdir="migrations", name="squashed"):
    """Replace every applied migration with one that creates today's schema.

    Databases that applied the old files record the new one without running
    it; new databases run only the new one. Delete the old files once every
    database has upgraded.
    """
    db = _db(db_or_url)
    migrations = discover(outdir)
    done = _applied(db)
    if not migrations:
        raise RuntimeError("nothing to squash")
    missing = [m.label for m in migrations if m.label not in done]
    if missing:
        raise RuntimeError("apply every migration before squashing: " + ", ".join(missing))
    tables = S.introspect(db)
    ops = [S.CreateTable(t) for t in S.ordered(tables)]
    replaces = [m.label for m in migrations]
    path = write_migration(outdir, name, ops, doc=f"{name}: replaces {len(replaces)} migrations",
                           schema=S.schema_hash(tables), extra=f"\nreplaces = {replaces!r}")
    _record(db, os.path.basename(path)[:-3])
    return path


def new_migration(outdir, name):
    """Scaffold the next ``NNN_name.{up,down}.sql`` pair. Returns up path."""
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "migration"
    os.makedirs(outdir, exist_ok=True)
    existing = [int(m.version) for m in discover(outdir)]
    nxt = f"{(max(existing) + 1) if existing else 1:03d}"
    up = os.path.join(outdir, f"{nxt}_{safe}.up.sql")
    down = os.path.join(outdir, f"{nxt}_{safe}.down.sql")
    with open(up, "w") as fh:
        fh.write(f"-- Migration {nxt}_{safe} (up).\n")
    with open(down, "w") as fh:
        fh.write(f"-- Migration {nxt}_{safe} (down).\n")
    return up


def new_python_migration(outdir, name):
    """An empty Python migration to fill in by hand. Returns its path."""
    return write_migration(outdir, name, [], doc=name)
