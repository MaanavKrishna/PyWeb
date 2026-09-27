"""Versioned migrations: journal + ordered apply + CLI.

``migrations/`` holds ``NNN_name.up.sql`` / ``NNN_name.down.sql`` pairs.
Applied versions are recorded in the ``pyweb_migrations`` journal table,
so ``migrate`` is idempotent and ``status`` shows pending work. Works on
any driver exposing ``execute()`` + ``transaction()`` (SQLite, Postgres,
MySQL); the journal DDL is plain standard SQL.
"""

from __future__ import annotations

import os
import re
import time

JOURNAL_DDL = (
    "CREATE TABLE IF NOT EXISTS pyweb_migrations ("
    "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
)

_FILE_RE = re.compile(r"^(\d+)_([A-Za-z0-9_]+)\.(up|down)\.sql$")


def _discover(outdir):
    """Return ``[(version, name)]`` sorted, validating up/down pairs."""
    pairs: dict[tuple[str, str], set[str]] = {}
    if os.path.isdir(outdir):
        for fn in sorted(os.listdir(outdir)):
            m = _FILE_RE.match(fn)
            if m:
                pairs.setdefault((m.group(1), m.group(2)), set()).add(m.group(3))
    ordered = sorted(pairs)
    for key, sides in pairs.items():
        if sides != {"up", "down"}:
            raise ValueError(f"migration {key[0]}_{key[1]} is missing "
                             f"{'down' if 'up' in sides else 'up'} file")
    return ordered


def _read(outdir, version, name, direction):
    path = os.path.join(outdir, f"{version}_{name}.{direction}.sql")
    with open(path) as fh:
        return [s.strip() for s in _split(fh.read()) if s.strip()]


def _split(script):
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";")]


def _applied(db) -> set[str]:
    db.execute(JOURNAL_DDL)
    res = db.execute("SELECT version FROM pyweb_migrations")
    rows = res.fetchall() if hasattr(res, "fetchall") else res
    out = set()
    for row in rows or []:
        if isinstance(row, (list, tuple)):
            out.add(row[0])
        elif isinstance(row, dict):
            out.add(row.get("version"))
        else:
            out.add(row)
    return out


def status(db_or_url, outdir="migrations"):
    """Return ``[(label, applied_bool)]`` for every discovered migration."""
    from pyweb.db import connect
    db = connect(db_or_url) if isinstance(db_or_url, str) else db_or_url
    done = _applied(db)
    return [(f"{v}_{n}", f"{v}_{n}" in done) for v, n in _discover(outdir)]


def migrate(db_or_url, outdir="migrations"):
    """Apply pending ``*.up.sql`` in version order. Returns applied labels."""
    from pyweb.db import connect
    db = connect(db_or_url) if isinstance(db_or_url, str) else db_or_url
    done = _applied(db)
    applied = []
    for version, name in _discover(outdir):
        label = f"{version}_{name}"
        if label in done:
            continue
        statements = _read(outdir, version, name, "up")
        ph = "?" if getattr(db, "paramstyle", "qmark") == "qmark" else "%s"
        with db.transaction():
            for stmt in statements:
                db.execute(stmt)
            db.execute(
                "INSERT INTO pyweb_migrations (version, applied_at) "
                f"VALUES ({ph}, {ph})",
                (label, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        applied.append(label)
    return applied


def rollback(db_or_url, outdir="migrations", *, steps=1, to=None):
    """Apply ``*.down.sql`` for the latest applied migration(s).

    ``steps=N`` rolls back the N most recent applied migrations (default
    1). ``to=<label>`` rolls back everything applied *after* ``label``,
    keeping ``label`` itself; unknown labels raise ``ValueError`` without
    touching the database.
    """
    from pyweb.db import connect
    db = connect(db_or_url) if isinstance(db_or_url, str) else db_or_url
    done = _applied(db)
    ordered = [f"{v}_{n}" for v, n in _discover(outdir) if f"{v}_{n}" in done]
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
        version, name = label.split("_", 1)
        with db.transaction():
            for stmt in _read(outdir, version, name, "down"):
                db.execute(stmt)
            ph = "?" if getattr(db, "paramstyle", "qmark") == "qmark" else "%s"
            db.execute(f"DELETE FROM pyweb_migrations WHERE version = {ph}",
                       (label,))
        rolled.append(label)
    return rolled


def new_migration(outdir, name):
    """Scaffold the next ``NNN_name.{up,down}.sql`` pair. Returns up path."""
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "migration"
    os.makedirs(outdir, exist_ok=True)
    existing = [m.group(1) for m in
                (_FILE_RE.match(f) for f in os.listdir(outdir)) if m]
    nxt = f"{(max(map(int, existing)) + 1) if existing else 1:03d}"
    up = os.path.join(outdir, f"{nxt}_{safe}.up.sql")
    down = os.path.join(outdir, f"{nxt}_{safe}.down.sql")
    with open(up, "w") as fh:
        fh.write(f"-- Migration {nxt}_{safe} (up).\n")
    with open(down, "w") as fh:
        fh.write(f"-- Migration {nxt}_{safe} (down).\n")
    return up
