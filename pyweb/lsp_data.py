"""What the language server knows about an app's data, read from the source (nothing is run).

* Models: their fields, relations and many-to-many lists, from ``class X(Model)``
  declarations (and the auth kit's ``User``), for completion after ``Post.``,
  inside ``Post.where(``, ``.include("``, ``.order("`` and ``Field(``.
* Forms: the fields a ``<Form action={fn}>`` can show, for completion inside
  ``name="`` and an error for a field the action doesn't have.
* N+1: a loop over a query that reads a relation the query didn't
  ``include()`` (one query per row) is flagged where the relation is read.

Everything here is pure: source text in, plain data out.
"""

from __future__ import annotations

import ast
import re

QUERY_METHODS = {
    "where": "rows matching conditions: where(Post.views > 10) or where(title='x')",
    "query": "every row, as a query to refine", "all": "every row (a list)",
    "get": "one row by id or conditions (None if missing)", "get_or_404": "one row, or the 404 page",
    "get_or_create": "(row, created)", "create": "insert a row", "upsert": "insert or update by unique fields",
    "bulk_create": "insert many rows", "include": "load relations in one query each (no N+1)",
    "order": 'sort: order("-created_at", "id")', "limit": "at most n rows", "offset": "skip n rows",
    "first": "the first row or None", "last": "the last row or None", "count": "how many rows",
    "exists": "whether any row matches", "values": "dicts of some columns", "pluck": "one column as a list",
    "update": "update every matching row", "delete": "delete every matching row",
    "aggregate": "Count/Sum/Avg/Min/Max over the rows", "group_by": "aggregate per group",
    "page": "cursor pagination: page(after=..., size=20)", "live": "rows that update in the page by themselves",
    "exclude": "rows not matching", "distinct": "drop duplicate rows", "policy": "who may read and write rows",
}
LOOKUPS = ("in", "not_in", "gt", "gte", "lt", "lte", "ne", "contains", "icontains", "startswith",
           "istartswith", "endswith", "iendswith", "isnull", "between")
FIELD_OPTIONS = {
    "default": "value for new rows", "min": "smallest number / shortest text / fewest items",
    "max": "largest number / longest text / most items", "pattern": "a regular expression the text must match",
    "choices": "the allowed values", "format": '"email", "url" or "slug"', "required": "must be filled in",
    "unique": "no two rows share it", "index": "add a database index", "label": "the form label",
    "help": "help text under the form field", "message": "the error message for any rule",
    "private": "never sent to browsers or accepted from them (password hashes)",
    "readonly": "never accepted from browsers", "nullable": "may be NULL", "auto_now_add": "set when created",
    "auto_now": "set on every save", "precision": "digits (Decimal)", "scale": "decimal places (Decimal)",
    "column": "the column name in the database",
}
# The auth kit's tables, so `User.` completes without running anything.
KNOWN_MODELS = {
    "User": {"fields": {"id": "int", "email": "Email", "name": "str", "roles": "list", "email_verified": "bool",
                        "is_active": "bool", "created_at": "datetime", "last_login_at": "datetime"},
             "relations": {}, "m2m": {}, "line": 0, "locked": {"id", "roles", "email_verified", "is_active",
                                                                  "created_at", "last_login_at"}},
}


def _ann_text(node):
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return ""


def _base_names(cls):
    return {b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in cls.bases}


def models(tree):
    """``{name: {"fields": {name: type}, "relations": {name: Model}, "m2m": {name: Model}, "line": n}}``."""
    found = {}
    imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                and (n.module or "").startswith("pyweb.authkit") for a in n.names}
    for name in KNOWN_MODELS:
        if name in imported:
            found[name] = KNOWN_MODELS[name]
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    names = set(found)
    changed = True
    while changed:                                 # Models can derive from other Models
        changed = False
        for cls in classes:
            if cls.name not in names and (_base_names(cls) & ({"Model"} | names)):
                names.add(cls.name)
                changed = True
    for cls in classes:
        if cls.name not in names or cls.name in found:
            continue
        fields, rels, m2m, locked = {"id": "int"}, {}, {}, {"id"}
        for item in cls.body:
            if not (isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)):
                continue
            key, text = item.target.id, _ann_text(item.annotation)
            if isinstance(item.value, ast.Call) and any(
                    k.arg in ("readonly", "private", "auto_now", "auto_now_add")
                    and isinstance(k.value, ast.Constant) and k.value.value is True for k in item.value.keywords):
                locked.update({key, f"{key}_id"})      # never set from a browser: not a form field
            if text.startswith("ClassVar"):
                continue
            inner = re.sub(r"\s*\|\s*None|Optional\[(.*)\]", r"\1", text).strip()
            listed = re.fullmatch(r"(?:list|List)\[\s*['\"]?(\w+)['\"]?\s*\]", inner)
            target = inner.strip("'\"")
            if listed and listed.group(1) in names:
                m2m[key] = listed.group(1)
            elif target in names:
                rels[key] = target
                fields[f"{key}_id"] = "int"
            else:
                fields[key] = text
        found[cls.name] = {"fields": fields, "relations": rels, "m2m": m2m, "line": cls.lineno, "locked": locked}
    return found


# ------------------------------------------------------------- completion

def _chain_model(prefix, known):
    """The Model at the root of a call chain ending at the cursor: ``Post.where(...).include("``."""
    m = re.search(r"\b([A-Z]\w*)((?:\.\w+\((?:[^()]|\([^()]*\))*\))*)\.\w*\(?[^()]*$", prefix)
    if m and m.group(1) in known:
        return m.group(1)
    return None


def complete(prefix, tree, known=None):
    """Completion items for the text before the cursor, or None when this isn't about data."""
    known = models(tree) if known is None else known
    if not known:
        return None
    # .include("au   /  .order("-ti
    m = re.search(r"\.(include|order|order_by|values|pluck|group_by)\(\s*(?:[\"'][^\"']*[\"']\s*,\s*)*[\"'](-?)(\w*)$",
                  prefix)
    if m:
        model = _chain_model(prefix[:m.start()] + ".x(", known) or _chain_model(prefix, known)
        if model is None:
            return None
        info = known[model]
        if m.group(1) == "include":
            names = {**info["relations"], **info["m2m"]}
            return [{"label": n, "kind": 18, "detail": f"{model}.{n} → {t}"} for n, t in sorted(names.items())]
        sign = m.group(2)
        return [{"label": sign + f, "kind": 5, "detail": t} for f, t in sorted(info["fields"].items())]
    # Post.where(ti   /  create(   get_or_create(
    m = re.search(r"\.(where|exclude|filter|get|get_or_404|get_or_create|create|update|upsert|first|count|exists)"
                  r"\((?:[^()]|\([^()]*\))*$", prefix)
    if m and re.search(r"(?:\(|,)\s*\w*$", prefix):
        model = _chain_model(prefix, known)
        if model is not None:
            info = known[model]
            items = [{"label": f"{f}=", "kind": 5, "detail": t, "insertText": f"{f}="}
                     for f, t in sorted(info["fields"].items())]
            items += [{"label": f"{r}=", "kind": 5, "detail": f"a {t} row", "insertText": f"{r}="}
                      for r, t in sorted(info["relations"].items())]
            if m.group(1) in ("where", "exclude", "filter", "count", "exists", "first"):
                items += [{"label": f"{f}__{lk}=", "kind": 5, "detail": f"lookup on {f}", "insertText": f"{f}__{lk}="}
                          for f in sorted(info["fields"]) for lk in ("in", "contains", "icontains", "gt", "lt")]
            return items
    # Post.   /   Post.ti
    m = re.search(r"\b([A-Z]\w*)\.(\w*)$", prefix)
    if m and m.group(1) in known:
        info = known[m.group(1)]
        items = [{"label": f, "kind": 5, "detail": f"column ({t}): Post.{f} == value".replace("Post", m.group(1))}
                 for f, t in sorted(info["fields"].items())]
        items += [{"label": r, "kind": 18, "detail": f"relation → {t}: .has(...) / include"}
                  for r, t in sorted({**info["relations"], **info["m2m"]}.items())]
        items += [{"label": q, "kind": 2, "detail": d} for q, d in sorted(QUERY_METHODS.items())]
        return items
    # Field(ma
    if re.search(r"\bField\((?:[^()]|\([^()]*\))*$", prefix) and re.search(r"(?:\(|,)\s*\w*$", prefix):
        return [{"label": f"{k}=", "kind": 5, "detail": d, "insertText": f"{k}="} for k, d in FIELD_OPTIONS.items()]
    return None


# ------------------------------------------------------------------ forms

_FORM_FIELD_TAGS = {"Input", "Textarea", "Select", "Checkbox", "FileInput"}


def action_fields(tree, action, known=None):
    """The fields ``<Form action={action}>`` can have, or None if the action isn't defined here."""
    known = models(tree) if known is None else known
    fn = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == action), None)
    if fn is None:
        return None
    params = [a for a in fn.args.args + fn.args.kwonlyargs]
    for a in params:
        ann = _ann_text(a.annotation) if a.annotation is not None else ""
        if ann in known:
            info = known[ann]
            locked = info.get("locked") or {"id"}
            out = {f: t for f, t in info["fields"].items() if f not in locked}
            out.update({r: f"a {t}" for r, t in {**info["relations"], **info["m2m"]}.items() if r not in locked})
            return out
    return {a.arg: (_ann_text(a.annotation) if a.annotation is not None else "") for a in params}


def _walk_ui(nodes, form=None):
    """``(node, enclosing <Form> action or None)`` for every markup node."""
    for n in nodes or ():
        action = form
        tag = getattr(n, "tag", None)
        if tag == "Form":
            val = n.attrs.get("action")
            action = val[1].strip() if isinstance(val, tuple) and val[0] == "expr" else None
        yield n, form
        for kids in (getattr(n, "children", None), getattr(n, "body", None), getattr(n, "orelse", None)):
            if kids:
                yield from _walk_ui(kids, action if tag == "Form" else form)


def form_problems(tree, ui, known=None):
    """``[(line, message)]`` for form fields the action doesn't have."""
    known = models(tree) if known is None else known
    out = []
    for node, action in _walk_ui(ui):
        if action is None or getattr(node, "tag", None) not in _FORM_FIELD_TAGS:
            continue
        name = node.attrs.get("name")
        if not (isinstance(name, tuple) and name[0] == "lit"):
            continue
        fields = action_fields(tree, action, known)
        if fields is not None and name[1] not in fields:
            out.append((node.line, f"<Form action={{{action}}}> has no field {name[1]!r} "
                                   f"(fields: {', '.join(sorted(fields)) or 'none'})"))
    return out


def form_completion(prefix, tree, ui, line, known=None):
    """Field names inside ``name="`` of a field tag in a ``<Form>`` (``line`` is 1-based)."""
    if not re.search(r"<(Input|Textarea|Select|Checkbox|FileInput)\b[^<>]*\bname=[\"']\w*$", prefix):
        return None
    best = None
    for node, _ in _walk_ui(ui):
        if getattr(node, "tag", None) == "Form" and node.line <= line:
            val = node.attrs.get("action")
            if isinstance(val, tuple) and val[0] == "expr":
                best = val[1].strip()
    if best is None:
        return None
    fields = action_fields(tree, best, known)
    return [{"label": f, "kind": 5, "detail": t or "field"} for f, t in sorted((fields or {}).items())]


# ------------------------------------------------------------------- N+1

_CHAIN = re.compile(r"^\s*([A-Z]\w*)\s*\.(?:query|where|all|filter|exclude|order|include)\b")


def _query_of(code, known):
    """``(model, includes)`` if ``code`` is a query over a known Model (not ``.live()``: rows are dicts)."""
    m = _CHAIN.match(code or "")
    if not m or m.group(1) not in known or ".live(" in code:
        return None
    includes = set()
    for args in re.findall(r"\.include\(([^()]*)\)", code):
        for name in re.findall(r"[\"']([\w.]+)[\"']", args):
            includes.add(name.split(".")[0])
    return m.group(1), includes


def n_plus_one(tree, ui, known=None):
    """``[(line, message)]`` where a loop reads a relation its query didn't include."""
    known = models(tree) if known is None else known
    if not known:
        return []
    queries = {}                                   # variable -> (model, includes), per function
    for fn in ast.walk(tree):
        if isinstance(fn, ast.Assign) and len(fn.targets) == 1 and isinstance(fn.targets[0], ast.Name):
            q = _query_of(_ann_text(fn.value), known)
            if q:
                queries[fn.targets[0].id] = q
    out = []

    def check(var, iterable, reads, line_of):
        q = queries.get(iterable.strip()) or _query_of(iterable, known)
        if not q:
            return
        model, includes = q
        rels = {**known[model]["relations"], **known[model]["m2m"]}
        for attr, line in reads(var):
            if attr in rels and attr not in includes:
                out.append((line, f"{model}.{attr} is read for every row but the query doesn't include it "
                                  f"(one query per row): add .include({attr!r}) to the query"))

    # Python loops
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            var = node.target.id

            def reads(v, body=node.body):
                for sub in body:
                    for x in ast.walk(sub):
                        if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == v:
                            yield x.attr, x.lineno
            check(var, _ann_text(node.iter), reads, None)
    # markup loops
    for node, _ in _walk_ui(ui):
        if type(node).__name__ != "ControlFor":
            continue
        var = node.target.strip()
        if not re.fullmatch(r"\w+", var):
            continue

        def reads(v, body=node.body):
            pattern = re.compile(rf"\b{re.escape(v)}\.(\w+)")
            for sub, _ in _walk_ui(body):
                codes = [getattr(sub, "code", None), getattr(sub, "test", None), getattr(sub, "iterable", None)]
                codes += [val[1] for val in (getattr(sub, "attrs", None) or {}).values()
                          if isinstance(val, tuple) and val[0] == "expr"]
                for code in codes:
                    for attr in pattern.findall(code or ""):
                        yield attr, sub.line
        check(var, node.iterable, reads, None)
    seen, unique = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def parse(text, path=None):
    """``(tree, ui)`` for a .pyweb source, or ``(None, None)`` if it doesn't parse yet."""
    from .compiler.parser import parse_source
    try:
        tree, ui, _ = parse_source(text, filename=path or "<editor>")
    except (SyntaxError, ValueError):
        return None, None
    return tree, ui
