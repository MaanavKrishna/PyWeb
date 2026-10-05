"""Forms: ``<Form action={save_post}>`` with fields checked in the browser and on the server.

::

    from pyweb import App, Form, Input, Textarea, Select, Checkbox, Submit, server

    @server
    def save_post(post: Post):          # a Model parameter: the form edits its fields
        post.save()
        return post

    @app.page("/posts/new")
    def NewPost():
        <Form action={save_post} redirect="/posts/{id}">
            <Input name="title" />
            <Textarea name="body" rows="8" />
            <Select name="tags" />
            <Submit>Publish</Submit>
        </Form>

The fields come from the action: a Model parameter (its fields, rules and
relations), or plain typed parameters (``def subscribe(email: Email, daily: bool = False)``).

* **In the browser**, the Model's rules (required, length, range, pattern,
  choices, email/url/slug) are checked as the user types, with the same
  messages as the server. The form submits without a page load and shows
  the server's errors next to each input.
* **Without JavaScript** it is a normal ``POST``: the server re-renders the
  page with the values and errors (422), or redirects (303) on success.
* Every submit carries a CSRF token and a one-time key, so a double click
  or a retried request runs the action once.
* ``<Form action={save_post} values={post}>`` edits an existing row: the
  form carries the row's id (signed), and the action gets the stored row
  with the submitted fields applied.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import html as _html
import inspect
import json
import re
import secrets
import threading
import time
import typing

FORM_PREFIX = "/__pyweb/form/"
CSRF_COOKIE = "__pw_csrf"
MAX_OPTIONS = 1000
IDEMPOTENCY_TTL = 600

#: While re-rendering a page after a failed no-JS submit: ``{form_id: {"values", "errors", "error"}}``.
FORM_STATE: contextvars.ContextVar = contextvars.ContextVar("pyweb_form_state", default=None)

FIELD_TAGS = ("Input", "Textarea", "Select", "Checkbox", "FileInput")
BUILTINS = ("Form", "Submit", "FormError") + FIELD_TAGS

_INPUT_TYPES = {"int": "number", "bigint": "number", "float": "number", "decimal": "number",
                "datetime": "datetime-local", "date": "date", "time": "time"}


def humanize(name):
    words = name.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else name


# ------------------------------------------------------------- the fields

class FieldDef:
    """One field a form can show, from a Model field or an action parameter."""

    def __init__(self, name, *, kind="str", rules=None, label=None, help=None, choices=None, multiple=False,
                 nullable=True, relation=None, file=None, model_field=None, default=None, bool_field=False):
        self.name = name
        self.kind = kind
        self.rules = rules
        self.label = label or humanize(name)
        self.help = help
        self.choices = choices            # [(value, label)] or None
        self.multiple = multiple
        self.nullable = nullable
        self.relation = relation          # target Model for foreign keys / many-to-many
        self.file = file                  # {"max_size": n, "types": [...]}
        self.model_field = model_field
        self.default = default
        self.bool_field = bool_field or kind == "bool"

    def input_type(self):
        if self.file is not None:
            return "file"
        if self.bool_field:
            return "checkbox"
        fmt = getattr(self.rules, "format", None)
        if fmt in ("email", "url"):
            return fmt
        return _INPUT_TYPES.get(self.kind, "text")

    def schema(self):
        out = self.rules.schema() if self.rules is not None else {"kind": self.kind}
        out["label"] = self.label
        if self.multiple:
            out["multiple"] = True
        if self.file is not None:
            out["file"] = self.file
        if self.choices is not None and "choices" not in out:
            out["options"] = [str(v) for v, _ in self.choices]
        return out


def _relation_choices(target):
    rows = target.query().limit(MAX_OPTIONS).all()
    return [(str(r.pk), _row_label(r)) for r in rows]


def _row_label(row):
    if type(row).__str__ is not object.__str__:
        return str(row)
    meta = type(row)._meta
    for f in meta.fields.values():
        if f.kind in ("str", "text") and not f.private and not f.primary_key:
            value = row.__dict__.get(f.name)
            if value:
                return str(value)
    return f"#{row.pk}"


def _model_param(fn):
    from pyweb.models import Model
    from pyweb.runtime.server.argcheck import hints
    found = hints(fn)
    params = [p for p in inspect.signature(fn).parameters.values()
              if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
    for p in params:
        ann = found.get(p.name)
        if isinstance(ann, type) and issubclass(ann, Model):
            return p.name, ann
    return None, None


def _model_fields(model, with_choices):
    out = {}
    meta = model._meta
    rel_fields = {rel.field.name: rel for rel in meta.relations.values()}
    for f in meta.fields.values():
        if f.primary_key or f.private or f.readonly:
            continue
        rel = rel_fields.get(f.name)
        if rel is not None:
            out[rel.name] = FieldDef(rel.name, kind="bigint", rules=f.rules, label=rel.label or humanize(rel.name),
                                     choices=_relation_choices(rel.target) if with_choices(rel.name) else None,
                                     nullable=f.nullable, relation=rel.target, model_field=f)
            continue
        file = None
        if getattr(f, "is_file", False):
            file = {"max_size": f.max_size, "types": f.types}
        choices = [(str(c), str(c)) for c in f.choices] if f.choices else None
        out[f.name] = FieldDef(f.name, kind=f.kind, rules=f.rules, label=f.label, help=f.help, choices=choices,
                               nullable=f.nullable, file=file, model_field=f, default=f.initial())
    for name, m2m in meta.m2m.items():
        out[name] = FieldDef(name, kind="bigint", label=humanize(name), multiple=True,
                             choices=_relation_choices(m2m.target) if with_choices(name) else None,
                             relation=m2m.target)
    return out


def _param_fields(fn):
    from pyweb.models.fields import Field, kind_for, unwrap_optional
    from pyweb.rules import Rules
    from pyweb.runtime.server.argcheck import hints
    found = hints(fn)
    out = {}
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        ann = found.get(p.name, str)
        field = None
        if typing.get_origin(ann) is typing.Annotated:
            args = typing.get_args(ann)
            ann = args[0]
            field = next((a for a in args[1:] if isinstance(a, Field)), None)
        inner, optional = unwrap_optional(ann)
        multiple = False
        choices = None
        if typing.get_origin(inner) in (list, set, tuple):
            multiple = True
            inner = (typing.get_args(inner) or (str,))[0]
        if typing.get_origin(inner) is typing.Literal:
            choices = [(str(v), str(v)) for v in typing.get_args(inner)]
            inner = type(typing.get_args(inner)[0])
        if _is_upload_type(inner):
            out[p.name] = FieldDef(p.name, kind="str", label=humanize(p.name), file={"max_size": None, "types": []},
                                   nullable=optional or p.default is not p.empty)
            continue
        kind = kind_for(inner) or "str"
        has_default = p.default is not p.empty
        if field is not None:
            field.bind(None, p.name, ann)
            rules = field.rules
            if not has_default:
                rules.required = rules.required or not optional
        else:
            fmt = {"Email": "email", "URL": "url", "Slug": "slug"}.get(getattr(inner, "__name__", ""))
            rules = Rules(kind=kind, required=not optional and not has_default and kind != "bool", format=fmt,
                          choices=[v for v, _ in choices] if choices and not multiple else None)
        out[p.name] = FieldDef(p.name, kind=kind, rules=rules, label=(field.label if field else None), choices=choices,
                               multiple=multiple, nullable=optional or has_default,
                               default=None if p.default is p.empty else p.default,
                               file={"max_size": field.max_size, "types": field.types}
                               if field is not None and getattr(field, "is_file", False) else None)
    return out


def _is_upload_type(t):
    return isinstance(t, type) and t.__name__ == "UploadedFile"


def form_fields(action, *, choices_for=None):
    """``(shape, param, model, {name: FieldDef})`` for an action function.

    ``shape`` is ``"model"`` (one Model parameter) or ``"kwargs"`` (plain parameters).
    """
    want = (lambda name: True) if choices_for is None else (lambda name: name in choices_for)
    param, model = _model_param(action)
    if model is not None:
        return "model", param, model, _model_fields(model, want)
    return "kwargs", None, None, _param_fields(action)


# ------------------------------------------------------- tokens and keys

def _secret():
    from pyweb import context as _ctx
    try:
        return _ctx.sign_key("csrf")
    except RuntimeError:
        return _ctx.sign_key("csrf", _ctx.RequestContext(None))


def _verify_keys():
    from pyweb import context as _ctx
    try:
        return _ctx.verify_keys("csrf")
    except RuntimeError:
        return _ctx.verify_keys("csrf", _ctx.RequestContext(None))


def _mac(key, text):
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:40]


def csrf_cookie():
    """This visitor's CSRF cookie value (set now if missing)."""
    from pyweb import context as _ctx
    from pyweb.context import request
    ctx = _ctx.current()
    value = getattr(ctx, "_pw_csrf", None) or (ctx.request.cookies or {}).get(CSRF_COOKIE)
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", value):
        value = secrets.token_urlsafe(24)
        request.set_cookie(CSRF_COOKIE, value, max_age=60 * 60 * 24 * 365, same_site="Lax")
    ctx._pw_csrf = value
    return value


def csrf_token():
    return _mac(_secret(), "csrf:" + csrf_cookie())


def check_csrf(cookie, token):
    if not cookie or not token:
        return False
    return any(hmac.compare_digest(_mac(k, "csrf:" + cookie), token) for k in _verify_keys())


def sign_pk(form_id, model, pk):
    raw = json.dumps([form_id, model.__name__, pk], default=str)
    payload = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return payload + "." + _mac(_secret(), "pk:" + payload)


def read_pk(token, form_id, model):
    if not token or "." not in token:
        return None
    payload, mac = token.rsplit(".", 1)
    if not any(hmac.compare_digest(_mac(k, "pk:" + payload), mac) for k in _verify_keys()):
        return None
    try:
        fid, name, pk = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, TypeError):
        return None
    return pk if fid == form_id and name == model.__name__ else None


def carry(data, purpose="steps"):
    """A signed token holding ``data`` (JSON) for multi-step forms: put it in a hidden
    field on the next step and read it back with :func:`restore`."""
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":"), default=str).encode()).decode().rstrip("=")
    stamp = str(int(time.time()))
    return f"{payload}.{stamp}.{_mac(_secret(), f'{purpose}:{payload}:{stamp}')}"


def restore(token, purpose="steps", max_age=3600):
    """The data from :func:`carry`, or None if it was changed or is older than ``max_age``."""
    try:
        payload, stamp, mac = (token or "").split(".")
    except ValueError:
        return None
    if not any(hmac.compare_digest(_mac(k, f"{purpose}:{payload}:{stamp}"), mac) for k in _verify_keys()):
        return None
    if time.time() - int(stamp) > max_age:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None


# --------------------------------------------------------------- the specs

def _display(value):
    import datetime as _dt
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else ""
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%dT%H:%M")
    if isinstance(value, (_dt.date, _dt.time)):
        return value.isoformat()
    return str(value)


def _initial(values, fdef):
    from pyweb.models import Model
    if values is None:
        return fdef.default
    if isinstance(values, Model):
        if fdef.relation is not None and fdef.multiple:
            lst = values.__dict__.get("__rel_" + fdef.name)
            if lst is not None and lst._loaded:
                return [t.pk for t in list.__iter__(lst)]
            if values.pk is not None:
                return getattr(values, fdef.name).query().pluck("id")
            return []
        if fdef.relation is not None:
            return values.__dict__.get(fdef.name + "_id")
        return values.__dict__.get(fdef.name)
    if isinstance(values, dict):
        return values.get(fdef.name, fdef.default)
    return getattr(values, fdef.name, fdef.default)


def _field_spec(fid, fdef, raw, error, used_kind):
    ident = f"pw-{fid}-{fdef.name}"
    rules = fdef.rules
    multiple = fdef.multiple
    out = {"id": ident, "name": fdef.name, "label": fdef.label, "type": fdef.input_type(),
           "required": bool(rules.required) if rules is not None and not fdef.bool_field else False,
           "error": error or "", "invalid": "true" if error else None,
           "describedby": ident + "-error", "help": fdef.help or "", "multiple": multiple or None,
           "minlength": None, "maxlength": None, "min": None, "max": None, "step": None, "accept": None}
    if error:
        out["error"] = error if not error[:1].islower() else f"{fdef.label} {error}"
    kind = fdef.kind
    if rules is not None and fdef.file is None:
        if kind in ("str", "text"):
            out["minlength"] = rules.min
            out["maxlength"] = rules.max
        elif kind in ("int", "bigint", "float", "decimal") and fdef.choices is None:
            out["min"] = rules.min
            out["max"] = rules.max
            out["step"] = "any" if kind in ("float", "decimal") else None
    if fdef.file is not None:
        out["accept"] = ",".join(fdef.file.get("types") or []) or None
    if multiple:
        selected = {str(v) for v in (raw or [])}
    else:
        selected = {_display(raw)} if raw not in (None, "") else set()
    out["value"] = "" if multiple else _display(raw)
    out["checked"] = bool(raw) and raw not in ("", "false", "0", "off")
    if fdef.choices is not None:
        opts = []
        if not multiple and (fdef.nullable or used_kind == "Select"):
            opts.append({"value": "", "label": "", "selected": not selected})
        for value, label in fdef.choices:
            opts.append({"value": value, "label": label, "selected": value in selected})
        out["options"] = opts
    else:
        out["options"] = []
    return out


def specs(entries):
    """Everything the markup of each ``<Form>`` on a page needs (called while the page renders).

    ``entries``: ``{form_id: {"action": fn, "values": row-or-dict, "fields": [(name, tag), ...],
    "redirect": str or None}}``.
    """
    from pyweb.context import current
    state = FORM_STATE.get() or {}
    req = current().request
    page = req.path if req is not None else "/"
    token = csrf_token()
    out = {}
    for fid, entry in entries.items():
        action = entry["action"]
        if not callable(action):
            raise TypeError(f"<Form action=...> must be a @server function, got {action!r}")
        used = list(entry.get("fields") or [])
        selects = {name for name, tag in used if tag == "Select"}
        shape, param, model, defs = form_fields(action, choices_for=selects)
        missing = [name for name, _ in used if name not in defs]
        if missing:
            raise ValueError(f"<Form action={{{action.__name__}}}> has no field {missing[0]!r} "
                             f"(fields: {', '.join(defs) or 'none'})")
        st = state.get(fid) or {}
        values = entry.get("values")
        submitted = st.get("values")
        errors = st.get("errors") or {}
        fields = {}
        for name, tag in used:
            fdef = defs[name]
            raw = submitted.get(name) if submitted is not None else _initial(values, fdef)
            if submitted is not None and fdef.multiple and not isinstance(raw, list):
                raw = [raw] if raw else []
            fields[name] = _field_spec(fid, fdef, raw, errors.get(name), tag)
        hidden = {"__pw_form": fid, "__pw_csrf": token, "__pw_key": secrets.token_urlsafe(16), "__pw_page": page}
        if entry.get("redirect"):
            hidden["__pw_redirect"] = entry["redirect"]
        from pyweb.models import Model
        if shape == "model" and isinstance(values, Model) and values.pk is not None:
            hidden["__pw_pk"] = sign_pk(fid, model, values.pk)
        out[fid] = {
            "id": fid,
            "url": FORM_PREFIX + action.__name__,
            "enctype": "multipart/form-data" if any(defs[n].file is not None for n, _ in used) else None,
            "rules": json.dumps({name: defs[name].schema() for name, _ in used}, separators=(",", ":"), default=str),
            "hidden": [{"name": k, "value": v} for k, v in hidden.items()],
            "fields": fields,
            "error": st.get("error") or (errors.get("__all__") or ""),
        }
    return out


# ------------------------------------------------------- reading a submit

class UploadedFile:
    """A file from a ``<FileInput>``: ``.filename``, ``.content_type`` (from its bytes),
    ``.size``, ``.data``; ``.save()`` stores it and returns its key."""

    def __init__(self, filename, data, content_type):
        self.filename = filename
        self.data = data
        self.content_type = content_type

    @property
    def size(self):
        return len(self.data)

    def save(self):
        from pyweb import storage
        return storage.storage().save(self.data, self.filename, self.content_type)

    def __repr__(self):
        return f"<UploadedFile {self.filename!r} {self.content_type} {self.size} bytes>"


def parse_body(req):
    """``(fields, files)`` from an urlencoded or multipart body: ``{name: [values]}``."""
    import urllib.parse
    headers = {k.lower(): v for k, v in (req.headers or {}).items()}
    ctype = headers.get("content-type", "")
    body = req.body or b""
    fields: dict = {}
    files: dict = {}
    if ctype.startswith("multipart/form-data"):
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not m:
            raise ValueError("multipart body without a boundary")
        boundary = ("--" + m.group(1)).encode()
        for part in body.split(boundary)[1:]:
            if part.startswith(b"--"):
                break
            part = part[2:] if part.startswith(b"\r\n") else part
            head, sep, data = part.partition(b"\r\n\r\n")
            if not sep:
                continue
            if data.endswith(b"\r\n"):
                data = data[:-2]
            disposition = ""
            for line in head.decode("utf-8", "replace").split("\r\n"):
                if line.lower().startswith("content-disposition:"):
                    disposition = line
            name_m = re.search(r'\bname="([^"]*)"', disposition)
            if not name_m:
                continue
            name = name_m.group(1)
            file_m = re.search(r'\bfilename="([^"]*)"', disposition)
            if file_m is not None:
                if file_m.group(1) or data:
                    from pyweb import storage
                    files.setdefault(name, []).append(UploadedFile(file_m.group(1) or "upload", data, storage.sniff(data)))
            else:
                fields.setdefault(name, []).append(data.decode("utf-8", "replace"))
    else:
        for key, value in urllib.parse.parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True):
            fields.setdefault(key, []).append(value)
    return fields, files


def _one(values):
    return values[-1] if values else None


def _coerce_into(fdef, raw_list, files, errors):
    """The Python value for one field, or adds an error."""
    from pyweb import storage
    name = fdef.name
    if fdef.file is not None:
        uploads = files.get(name) or []
        if not uploads:
            return None
        up = uploads[-1]
        limit = fdef.file.get("max_size")
        if limit and up.size > limit:
            errors[name] = f"must be at most {_size_words(limit)}"
            return None
        if not storage.type_allowed(up.content_type, fdef.file.get("types")):
            errors[name] = "isn't an allowed kind of file"
            return None
        return up
    if fdef.multiple:
        values = [v for v in (raw_list or []) if v != ""]
        try:
            if fdef.relation is not None:
                return [int(v) for v in values]
            if fdef.model_field is not None:
                return [fdef.model_field.coerce(v) for v in values]
            return values
        except (TypeError, ValueError):
            errors[name] = "has an invalid choice"
            return None
    raw = _one(raw_list)
    if fdef.bool_field:
        return raw not in (None, "", "false", "0", "off")
    if raw is None or raw == "":
        if fdef.kind in ("str", "text") and fdef.relation is None:
            return "" if not fdef.nullable else (None if raw is None else "")
        return None
    if fdef.choices is not None and fdef.relation is None and raw not in {v for v, _ in fdef.choices}:
        errors[name] = "must be one of: " + ", ".join(label for _, label in fdef.choices)
        return None
    try:
        if fdef.relation is not None:
            return int(raw)
        if fdef.model_field is not None:
            return fdef.model_field.coerce(raw)
        from pyweb.models.fields import Field
        probe = Field(kind=fdef.kind)
        return probe.coerce(raw)
    except (TypeError, ValueError) as exc:
        errors[name] = str(exc) or "has the wrong type"
        return None


def _size_words(n):
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:g} {unit}"
    return f"{n} bytes"


def build_args(action, fid, fields, files):
    """The keyword arguments to call ``action`` with, from a submitted form.

    Only the action's own fields are read (never ids, roles or private fields);
    problems raise :class:`pyweb.ValidationError` with a message per field.
    """
    from pyweb.rules import ValidationError
    shape, param, model, defs = form_fields(action, choices_for=set())
    errors: dict = {}
    if shape == "model":
        # Fields the form showed: checkboxes and multi-selects send nothing when empty,
        # so the markup adds a hidden __pw_has_<name> for them.
        submitted = {name for name in defs if name in fields or name in files or f"__pw_has_{name}" in fields}
        row = None
        pk = read_pk(_one(fields.get("__pw_pk")), fid, model) if fields.get("__pw_pk") else None
        if fields.get("__pw_pk") and pk is None:
            raise ValidationError({"__all__": "this form was changed or is too old; reload the page"})
        if pk is not None:
            row = model.get(pk)
            if row is None:
                raise ValidationError({"__all__": "this row no longer exists"})
        values = {}
        for name in submitted:
            fdef = defs[name]
            value = _coerce_into(fdef, fields.get(name), files, errors)
            if name in errors:
                continue
            if isinstance(value, UploadedFile):
                value = value.save()
            elif fdef.file is not None and value is None:
                continue          # no new file: keep the old one
            values[name] = value
        if row is None:
            row = model(**values)
        else:
            m2m = {k: v for k, v in values.items() if k in model._meta.m2m}
            for key, value in values.items():
                if key in model._meta.m2m:
                    continue
                if key in model._meta.relations:
                    setattr(row, key, value)
                else:
                    row.__dict__[key] = value
            if m2m:
                row.__dict__.setdefault("_pending_m2m", {}).update(m2m)
        try:
            row.validate()
        except ValidationError as exc:
            errors = {**exc.errors, **errors}            # a value that couldn't be read wins
        if errors:
            raise ValidationError(errors)
        return {param: row}
    out = {}
    for name, fdef in defs.items():
        if name not in fields and name not in files and f"__pw_has_{name}" not in fields:
            if fdef.default is not None or fdef.nullable or fdef.bool_field or fdef.multiple:
                continue
            errors[name] = "is required"
            continue
        value = _coerce_into(fdef, fields.get(name), files, errors)
        if name in errors:
            continue
        if fdef.rules is not None and not isinstance(value, UploadedFile):
            problem = fdef.rules.check(value)
            if problem:
                errors[name] = problem
                continue
        out[name] = value
    if errors:
        raise ValidationError(errors)
    return out


# --------------------------------------------------------- one-time keys

class _Once:
    """Remembers each submit's outcome for a while, so a repeat gets the same answer."""

    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict = {}

    def claim(self, key):
        """``("new", None)``, ``("done", outcome)`` or ``("busy", None)``."""
        now = time.monotonic()
        with self.lock:
            for k in [k for k, (t, _) in self.items.items() if now - t > IDEMPOTENCY_TTL]:
                del self.items[k]
            found = self.items.get(key)
            if found is None:
                self.items[key] = (now, None)
                return "new", None
            return ("busy", None) if found[1] is None else ("done", found[1])

    def finish(self, key, outcome):
        with self.lock:
            self.items[key] = (time.monotonic(), outcome)

    def forget(self, key):
        with self.lock:
            self.items.pop(key, None)


ONCE = _Once()


def fill_redirect(template, result):
    """``/posts/{id}`` with values from the action's result; only same-site paths."""
    from pyweb.models import Model
    if not template:
        return None
    data = result.to_dict() if isinstance(result, Model) else (result if isinstance(result, dict) else {"result": result})

    def sub(m):
        value = data.get(m.group(1))
        return "" if value is None else re.sub(r"[^A-Za-z0-9._~-]", lambda c: "%%%02X" % ord(c.group()), str(value))
    url = re.sub(r"\{(\w+)\}", sub, template)
    if not url.startswith("/") or url.startswith("//") or "\\" in url:
        return None
    return url


# ------------------------------------------------------------ old API (0.4)

MAX_FIELD_LENGTH = 500

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _check_rule(value, rule):
    name, _, arg = rule.partition(":")
    if name == "required":
        if value is None or (isinstance(value, str) and not value.strip()):
            return "this field is required"
    elif name == "email":
        if value is not None and not _EMAIL_RE.match(str(value)):
            return "enter a valid email address"
    elif name == "max_length":
        if value is not None and len(str(value)) > int(arg):
            return f"must be at most {arg} characters"
    elif name == "min_length":
        if value is not None and len(str(value)) < int(arg):
            return f"must be at least {arg} characters"
    return None


def validate_request(data, rules):
    """Validate ``data`` against ``{field: [rules]}``; return error dict."""
    errors = {}
    for field, field_rules in rules.items():
        value = data.get(field)
        if isinstance(value, str) and len(value) > MAX_FIELD_LENGTH * 10:
            errors[field] = ["value too long"]
            continue
        for rule in field_rules:
            err = _check_rule(value, rule)
            if err:
                errors.setdefault(field, []).append(err)
    return errors


def _kind(ann):
    name = getattr(ann, "__name__", str(ann))
    if name == "Email":
        return "email"
    return {"str": "text", "int": "number", "float": "number",
            "bool": "checkbox"}.get(name, "text")


def fields_for(model):
    fields = []
    for fname, ann in getattr(model, "_fields", {}).items():
        name = getattr(ann, "__name__", str(ann))
        fields.append({"name": fname, "kind": _kind(ann), "type": name,
                       "required": True})
    return fields


def render_form(model, *, action="", method="post", submit="Submit",
                csrf_token=None):
    parts = [f'<form action="{_html.escape(action)}" method="{method}">']
    if csrf_token:
        parts.append(f'<input type="hidden" name="csrf_token" '
                     f'value="{_html.escape(csrf_token)}" />')
    for f in fields_for(model):
        if f["kind"] == "checkbox":
            parts.append(f'<label><input type="checkbox" name="{f["name"]}" /> {f["name"]}</label>')
        else:
            parts.append(f'<label>{f["name"]}<input type="{f["kind"]}" name="{f["name"]}" required /></label>')
    parts.append(f"<button>{_html.escape(submit)}</button></form>")
    return "".join(parts)


def validate(model, data):
    """Validate a dict against model annotations. Returns (clean, errors)."""
    clean, errors = {}, {}
    import datetime
    from decimal import Decimal
    for fname, ann in getattr(model, "_fields", {}).items():
        name = getattr(ann, "__name__", str(ann))
        raw = (data or {}).get(fname)
        if raw in (None, ""):
            errors[fname] = "required"
            continue
        try:
            if name == "Email":
                if "@" not in str(raw):
                    raise ValueError("must be a valid email")
                clean[fname] = str(raw)
            elif name == "int":
                clean[fname] = int(raw)
            elif name == "float":
                clean[fname] = float(raw)
            elif name == "Decimal":
                clean[fname] = Decimal(str(raw))
            elif name == "bool":
                clean[fname] = str(raw).lower() in ("1", "true", "yes", "on")
            elif name == "datetime":
                clean[fname] = raw if isinstance(raw, datetime.datetime) else datetime.datetime.fromisoformat(str(raw))
            else:
                clean[fname] = str(raw)
        except (ValueError, TypeError, ArithmeticError) as exc:
            errors[fname] = str(exc)
    return clean, errors


# ---------------------------------------------------------- markup names

class _Tag:
    """``<Form>`` and its fields are markup: imported from ``pyweb`` and used in pages."""

    def __init__(self, name):
        self.__name__ = name

    def __call__(self, *args, **kwargs):
        raise TypeError(f"<{self.__name__}> is markup: use it inside a page, e.g. <{self.__name__} ... />")

    def __repr__(self):
        return f"<pyweb markup tag {self.__name__}>"


Form = _Tag("Form")
Input = _Tag("Input")
Textarea = _Tag("Textarea")
Select = _Tag("Select")
Checkbox = _Tag("Checkbox")
FileInput = _Tag("FileInput")
Submit = _Tag("Submit")
FormError = _Tag("FormError")
