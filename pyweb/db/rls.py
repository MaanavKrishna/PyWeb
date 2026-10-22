"""Postgres row-level security for tenant Models: the database itself keeps organizations apart.

PyWeb already limits every ORM query on a ``tenant=True`` Model to the current
organization. On Postgres the table also gets a policy, so raw SQL that forgets
the ``org_id`` condition (or SQL injected into a query) can't cross organizations:

* ``pyweb db diff`` adds ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` and a policy
  to the migration for each tenant table that doesn't have them (``FORCE``
  applies it to the table's owner too, which is usually the app's role);
* before a statement that mentions a tenant table, PyWeb sets the
  transaction-local setting ``pyweb.org_id``: the organization's id while code
  runs for one, ``0`` (no rows) while it runs for none, and empty for code
  outside any organization (migrations, scripts), which the policy lets through.

Superusers and roles with ``BYPASSRLS`` skip every policy: connect the app as an
ordinary role for this to apply. SQLite and MySQL have no row-level security;
there the ORM's scoping is the protection.
"""

from __future__ import annotations

import re

POLICY = "pyweb_tenant"
SETTING = "pyweb.org_id"
_CONDITION = (f"coalesce(current_setting('{SETTING}', true), '') = '' "
              f"OR org_id = nullif(current_setting('{SETTING}', true), '')::bigint")


def enable_sql(table, quote):
    """The statements that turn the policy on for ``table``."""
    t = quote(table)
    return [f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY {POLICY} ON {t} USING ({_CONDITION}) WITH CHECK ({_CONDITION})"]


def disable_sql(table, quote):
    t = quote(table)
    return [f"DROP POLICY IF EXISTS {POLICY} ON {t}",
            f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY"]


def tenant_tables(models):
    return sorted({m._meta.table for m in models if getattr(m._meta, "tenant", False) and not m._meta.abstract})


def protected(db, table):
    """Is ``table`` already protected (RLS forced and PyWeb's policy present)?"""
    row = db.execute("SELECT c.relrowsecurity, c.relforcerowsecurity, EXISTS (SELECT 1 FROM pg_policies p "
                     "WHERE p.tablename = c.relname AND p.policyname = ?) FROM pg_class c "
                     "WHERE c.relname = ? AND c.relkind = 'r'", (POLICY, table)).fetchone()
    return bool(row and row[0] and row[1] and row[2])


def missing_ops(db, models):
    """Migration steps (``Execute``) enabling the policy on tenant tables that lack it (Postgres only)."""
    from .dialect import dialect_of
    from .schema import Execute, introspect
    d = dialect_of(db)
    if d.name != "postgres":
        return []
    existing = set(introspect(db))
    ops = []
    for table in tenant_tables(models):
        if table in existing and protected(db, table):
            continue
        ops.append(Execute("; ".join(enable_sql(table, d.quote)),
                           reverse_sql="; ".join(disable_sql(table, d.quote)),
                           note=f"row-level security for {table} (organizations can't read each other's rows)"))
    return ops


def ensure(db, models):
    """Enable the policy now where it's missing (``auto_create`` while developing)."""
    from .dialect import dialect_of
    d = dialect_of(db)
    if d.name != "postgres":
        return []
    done = []
    for table in tenant_tables(models):
        if not protected(db, table):
            for sql in enable_sql(table, d.quote):
                db.execute(sql)
            done.append(table)
    return done


# ------------------------------------------------------------------ the setting, per statement

_names: dict = {}


def statement_setting(db, sql):
    """The ``pyweb.org_id`` value a statement on ``db`` should run with, or None to leave it alone."""
    pattern = _names.get(id(db))
    if pattern is None:
        return None
    if not pattern.search(sql):
        return None
    from pyweb import tenancy
    scoped, org_id, _ = tenancy.current()
    if not scoped:
        return ""
    return str(org_id) if org_id is not None else "0"


def watch(db, models):
    """Make ``db`` set ``pyweb.org_id`` before statements on these Models' tenant tables."""
    tables = tenant_tables(models)
    if not tables:
        return
    from .dialect import dialect_of
    if dialect_of(db).name != "postgres":
        return
    known = set(getattr(db, "_pw_tenant_tables", ()))
    known.update(tables)
    db._pw_tenant_tables = known
    _names[id(db)] = re.compile(r'\b(?:' + "|".join(re.escape(t) for t in sorted(known)) + r')\b')
