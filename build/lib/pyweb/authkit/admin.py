"""``/admin``: browse, search, edit and delete rows of every Model.

Only for signed-in users with the ``admin`` role, who also must have signed
in within the last hour to change anything. Private fields never appear;
read-only fields are shown but not editable; row policies don't apply (an
admin sees everything). Every change is in the audit log.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse

from pyweb.rules import ValidationError

from . import html as H
from .models import User

PAGE_SIZE = 50
FRESH = 60 * 60


def _models(server):
    app = getattr(server, "app", None)
    found = app.models() if app is not None and hasattr(app, "models") else []
    out = {m._meta.table: m for m in found}
    from pyweb import models as M
    db = M.database()
    if db is not None:
        from pyweb.db import schema as S
        from pyweb.jobs.db import JobRecord
        if JobRecord._meta.table in S.table_names(db):
            out[JobRecord._meta.table] = JobRecord          # background jobs: see, retry, delete
    return out


def _label(model):
    return re.sub(r"(?<!^)(?=[A-Z])", " ", model.__name__)


def _shown(model):
    return [f for f in model._meta.fields.values() if not f.private]


def _cell(value):
    if value is None:
        return '<span class="pw-muted">—</span>'
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
    else:
        text = str(value)
    return H.esc(text if len(text) <= 80 else text[:77] + "…")


def _input(f, value, error):
    from pyweb.forms import _display
    name = f.name
    label = f.label or name.replace("_", " ").capitalize()
    rel = getattr(f, "relation", None)
    if f.readonly or f.primary_key:
        return (f'<div class="pw-field"><label>{H.esc(label)}</label><p>{_cell(value)}</p></div>')
    if f.kind == "bool":
        return (f'<div class="pw-field"><input type="hidden" name="__has_{name}" value="1"><label class="pw-check">'
                f'<input type="checkbox" name="{name}" value="true"{" checked" if value else ""}> {H.esc(label)}</label>'
                f'<p class="pw-error"{"" if error else " hidden"}>{H.esc(error)}</p></div>')
    if rel is not None:
        target = rel.target
        if target.query().unscoped().count() <= 1000:
            from pyweb.forms import _row_label as row_label
            opts = ['<option value=""></option>'] if f.nullable else []
            for row in target.query().unscoped().order("id"):
                sel = " selected" if row.pk == value else ""
                opts.append(f'<option value="{row.pk}"{sel}>{H.esc(row_label(row))}</option>')
            return (f'<div class="pw-field"><label for="a-{name}">{H.esc(label)}</label>'
                    f'<select id="a-{name}" name="{name}">{"".join(opts)}</select>'
                    f'<p class="pw-error"{"" if error else " hidden"}>{H.esc(error)}</p></div>')
    if f.kind in ("json", "text"):
        text = json.dumps(value, indent=2, default=str) if f.kind == "json" and value is not None else (value or "")
        return (f'<div class="pw-field"><label for="a-{name}">{H.esc(label)}</label>'
                f'<textarea id="a-{name}" name="{name}" rows="5">{H.esc(text)}</textarea>'
                f'<p class="pw-error"{"" if error else " hidden"}>{H.esc(error)}</p></div>')
    kind = {"int": "number", "bigint": "number", "float": "number", "decimal": "number",
            "datetime": "datetime-local", "date": "date", "time": "time"}.get(f.kind, "text")
    step = ' step="any"' if f.kind in ("float", "decimal") else ""
    return H.field(name, label, type=kind, value=_display(value), error=error, required=False, form="admin") \
        .replace(f'type="{kind}"', f'type="{kind}"{step}', 1)


def _read(model, f, raw, has):
    if f.kind == "bool":
        return raw not in (None, "", "false", "0", "off") if has else None
    if raw is None:
        return None
    if raw == "" and (f.nullable or f.kind not in ("str", "text")):
        return None
    if f.kind == "json":
        try:
            return json.loads(raw)
        except ValueError:
            raise ValueError("must be valid JSON") from None
    return f.coerce(raw)


def dispatch(kit, server, req):
    from .pages import Ctx, forbidden, page, redirect
    user = kit.user()
    path = req.path.split("?")[0]
    if user is None:
        return redirect("/login?" + urllib.parse.urlencode({"next": path}))
    c = Ctx(kit, server, req)
    if not user.has_role("admin"):
        return page(c, "Not allowed", H.notice("This page is for administrators.", "error"), status=403)
    models = _models(server)
    parts = [p for p in path.split("/") if p][1:]
    from pyweb.models import system
    with system():
        if not parts:
            rows = "".join(f'<tr><td><a href="/admin/{t}">{H.esc(_label(m))}</a></td><td>{m.query().count()}</td></tr>'
                           for t, m in sorted(models.items()))
            body = f'<table class="pw-table"><tr><th>Table</th><th>Rows</th></tr>{rows}</table>'
            return page(c, "Admin", body, wide=True)
        model = models.get(parts[0])
        if model is None:
            return page(c, "Not found", H.notice("No such table.", "error"), status=404)
        if len(parts) == 1:
            return _list(c, model)
        if c.method == "POST":
            if not c.csrf_ok():
                return forbidden(c)
            if not kit.fresh(FRESH):
                return redirect("/login?" + urllib.parse.urlencode({"next": path, "fresh": "1"}))
        if parts[1] == "new":
            return _edit(c, model, None)
        row = model.query().get(model._meta.pk.coerce(parts[1])) if len(parts) == 2 else None
        if row is None:
            return page(c, "Not found", H.notice("No such row.", "error"), status=404)
        return _edit(c, model, row)


def _list(c, model):
    from pyweb.models.query import Or
    from .pages import page
    table = model._meta.table
    q = c.query.get("q", "").strip()
    query = model.query()
    if q:
        text_fields = [f for f in _shown(model) if f.kind in ("str", "text")]
        conds = [f.icontains(q) for f in text_fields]
        if q.isdigit():
            conds.append(model._meta.pk == int(q))
        if conds:
            query = query.where(Or(conds) if len(conds) > 1 else conds[0])
    try:
        rows = query.order("-" + model._meta.pk.name).page(size=PAGE_SIZE, after=c.query.get("after") or None)
    except ValueError:
        rows = query.order("-" + model._meta.pk.name).page(size=PAGE_SIZE)
    cols = _shown(model)[:6]
    head = "".join(f"<th>{H.esc(f.name)}</th>" for f in cols)
    body_rows = "".join(
        "<tr>" + "".join(
            (f'<td><a href="/admin/{table}/{r.pk}">{_cell(r.__dict__.get(f.name))}</a></td>' if i == 0
             else f"<td>{_cell(r.__dict__.get(f.name))}</td>") for i, f in enumerate(cols)) + "</tr>"
        for r in rows)
    search = (f'<form method="get" action="/admin/{table}" class="pw-row">'
              f'<input name="q" value="{H.esc(q)}" placeholder="Search" aria-label="Search" class="pw-code">'
              f'<button class="pw-button pw-secondary pw-inline">Search</button></form>')
    more = ""
    if rows.next:
        params = urllib.parse.urlencode({k: v for k, v in (("q", q), ("after", rows.next)) if v})
        more = f'<p class="pw-links"><a href="/admin/{table}?{params}">Next page</a></p>'
    nav = f'<p class="pw-nav"><a href="/admin">All tables</a><a href="/admin/{table}/new">Add a row</a></p>'
    body = nav + search + f'<table class="pw-table"><tr>{head}</tr>{body_rows}</table>' + more
    return page(c, _label(model), body, wide=True)


def _edit(c, model, row):
    from .pages import page, redirect
    kit = c.kit
    table = model._meta.table
    errors = {}
    if c.method == "POST" and c.get("action") == "retry" and row is not None and model._meta.table == "pyweb_jobs":
        from pyweb import jobs
        if jobs.retry(row.pk):
            kit.event("admin_change", user=kit.user(), detail=f"retried job {row.pk}")
        return redirect(f"/admin/{table}/{row.pk}")
    if c.method == "POST":
        if c.get("action") == "delete":
            if c.get("confirm") != "yes":
                errors["__all__"] = "Tick the box to confirm."
            else:
                try:
                    row.delete()
                except Exception as exc:  # noqa: BLE001 - e.g. other rows still point at it
                    errors["__all__"] = f"Couldn't delete: {exc}"
                else:
                    kit.event("admin_delete", user=kit.user(), detail=f"{table} {row.pk}")
                    return redirect(f"/admin/{table}")
        else:
            target = row if row is not None else model()
            old_roles = list(row.roles or []) if isinstance(row, User) else None
            for f in model._meta.fields.values():
                if f.private or f.readonly or f.primary_key:
                    continue
                has = f.name in c.fields or f"__has_{f.name}" in c.fields
                if not has:
                    continue
                try:
                    value = _read(model, f, c.fields.get(f.name), has)
                except ValueError as exc:
                    errors[f.name] = str(exc)
                    continue
                target.__dict__[f.name] = value
            if not errors:
                try:
                    target.save()
                except ValidationError as exc:
                    errors.update(exc.errors)
            if not errors:
                kit.event("admin_change", user=kit.user(), detail=f"{table} {target.pk}")
                if old_roles is not None and sorted(old_roles) != sorted(target.roles or []):
                    kit.event("roles_changed", user=target, detail=f"{old_roles} -> {target.roles} by admin")
                    kit.revoke(target)
                return redirect(f"/admin/{table}/{target.pk}")
            row = target if row is None else row
    values = row.__dict__ if row is not None else {f.name: f.initial() for f in model._meta.fields.values()}
    fields = "".join(_input(f, values.get(f.name), errors.get(f.name, "")) for f in _shown(model))
    title = f"{_label(model)} {row.pk}" if row is not None and row.pk is not None else f"New {_label(model).lower()}"
    action = f"/admin/{table}/{row.pk}" if row is not None and row.pk is not None else f"/admin/{table}/new"
    body = f'<p class="pw-nav"><a href="/admin/{table}">Back to {H.esc(_label(model))}</a></p>'
    body += H.notice(errors.get("__all__", ""), "error")
    body += H.form(action, fields + H.button("Save", kind="primary pw-inline"), csrf=c.csrf)
    if row is not None and model._meta.table == "pyweb_jobs" and getattr(row, "state", "") in ("dead", "failed"):
        body += "<h2>Run again</h2>" + H.form(
            action, '<input type="hidden" name="action" value="retry">' +
            H.button("Retry this job", kind="primary pw-inline"), csrf=c.csrf)
    if row is not None and row.pk is not None:
        body += "<h2>Delete</h2>" + H.form(
            action, '<input type="hidden" name="action" value="delete"><label class="pw-check">'
            '<input type="checkbox" name="confirm" value="yes"> Delete this row</label>' +
            H.button("Delete", kind="danger pw-inline"), csrf=c.csrf)
    return page(c, title, body, wide=True, status=422 if errors else 200)
