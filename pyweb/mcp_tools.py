"""More MCP tools for ``pyweb mcp``: docs search, the UI kit, accessibility and visual review,
upgrades, deploy plans, webhooks, profiling, editor-style explanations, teams, and the link to a
running ``pyweb dev``. Registered in :mod:`pyweb.mcp` (``TOOLS``)."""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time

from . import mcp as M

PATH = {"type": "string", "description": "Path to the .pyweb file (default: app.pyweb)."}
USER_PROPS = {
    "as_user": {"type": "string", "description": "Sign in as this email first (created if needed)."},
    "roles": {"type": "array", "items": {"type": "string"}, "description": "Roles for as_user."},
    "org": {"type": "string", "description": "With organizations: work in this organization (by name; created "
                                             "if missing) as as_user."},
    "org_role": {"type": "string", "enum": ["owner", "admin", "member", "viewer"]},
}
_S, _I, _B, _N = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}, {"type": "number"}
_A, _O, _ANY = {"type": "array"}, {"type": "object"}, {}
_obj = M._obj


# ------------------------------------------------------------------ docs

def _sections(text):
    """``[(heading, body)]`` split at ``#``/``##``/``###`` headings (code fences respected)."""
    out, heading, buf, fence = [], "", [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fence = not fence
        if not fence and re.match(r"#{1,3} ", line):
            if buf or heading:
                out.append((heading, "\n".join(buf).strip()))
            heading, buf = line.lstrip("#").strip(), []
        else:
            buf.append(line)
    out.append((heading, "\n".join(buf).strip()))
    return out


def _corpus():
    """Everything searchable: each docs page's sections, the AI guide's and the threat model's."""
    items = []
    for slug, (path, title) in M.doc_pages().items():
        with open(path, encoding="utf-8") as fh:
            for heading, body in _sections(fh.read()):
                items.append({"page": slug, "title": title, "section": heading or title,
                              "uri": f"pyweb://docs/{slug}", "body": body})
    for heading, body in _sections(M._guide_text()):
        items.append({"page": "guide", "title": "PyWeb guide for AI assistants", "section": heading,
                      "uri": "pyweb://guide", "body": body})
    tm = M.threat_model_path()
    if tm:
        with open(tm, encoding="utf-8") as fh:
            for heading, body in _sections(fh.read()):
                items.append({"page": "threat-model", "title": "Threat model", "section": heading,
                              "uri": "pyweb://threat-model", "body": body})
    return items


def _words(text):
    return re.findall(r"[a-z0-9_]+", text.lower())


def tool_docs(args):
    """Search PyWeb's documentation, or read one page."""
    page = args.get("page")
    limit = int(args.get("max_chars") or 30000)
    if page:
        try:
            text, _mime = M.read_resource(page if page.startswith("pyweb://") else f"pyweb://docs/{page}")
        except KeyError:
            raise ValueError(f"no docs page {page!r}; pages: {', '.join(M.doc_pages())}") from None
        return {"page": page, "text": text[:limit], "truncated": len(text) > limit}
    query = (args.get("query") or "").strip()
    if not query:
        return {"pages": [{"page": s, "title": t, "uri": f"pyweb://docs/{s}"} for s, (_p, t) in M.doc_pages().items()],
                "hint": "pass query to search, or page to read one"}
    terms = [w for w in _words(query) if len(w) > 1]
    scored = []
    for item in _corpus():
        head, body = _words(item["section"] + " " + item["title"]), _words(item["body"])
        score = sum(3 * head.count(t) + min(body.count(t), 8) for t in terms)
        if all(t in head or t in body for t in terms):
            score *= 2                                     # every word appears: much more likely to be it
        if query.lower() in item["body"].lower():
            score += 10
        if score:
            scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    hits = []
    for score, item in scored[: int(args.get("max_results") or 6)]:
        body = item["body"]
        at = max(0, min((body.lower().find(t) for t in terms if t in body.lower()), default=0) - 200)
        hits.append({"page": item["page"], "title": item["title"], "section": item["section"], "uri": item["uri"],
                     "score": score, "text": body[at:at + 1500]})
    return {"query": query, "results": hits,
            **({} if hits else {"hint": "nothing matched; try other words, or read pyweb://docs"})}


# ------------------------------------------------------------------ UI kit

def _kit_files():
    from .ui.cli import files
    return files()


def _kit_source(name):
    """``(file path, the component's def block, the file's docstring)``."""
    from .compiler.pipeline import kit_index
    path = kit_index().get(name)
    if path is None:
        return None, None, None
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    doc = re.match(r'\s*"""(.*?)"""', src, re.S)
    block = re.search(rf"^def {re.escape(name)}\(.*?(?=^def |\Z)", src, re.M | re.S)
    return path, block.group(0).rstrip() if block else None, doc.group(1).strip() if doc else ""


def _kit_params(block):
    m = re.match(r"def \w+\((.*?)\):", block or "", re.S)
    if not m:
        return []
    params = []
    for part in re.split(r",\s*(?![^()\[\]{}]*[)\]}])", " ".join(m.group(1).split())):
        part = part.strip()
        if not part:
            continue
        name, _, default = part.partition("=")
        params.append({"name": name.strip(), **({"default": default.strip()} if default else {})})
    return params


def _kit_values(name):
    from .ui.extending import values
    return {k: v for k, v in values(name).items() if v}


def kit_index_markdown():
    lines = ["# UI kit (`from pyweb.ui import ...`)", ""]
    for stem, names in _kit_files().items():
        comps = [n for n in names if n[:1].isupper()]
        if not comps:
            continue
        parts = []
        for n in comps:
            vals = _kit_values(n)
            extra = "; ".join(f"{d}: {', '.join(v)}" for d, v in vals.items())
            parts.append(f"[`{n}`](pyweb://kit/{n})" + (f" ({extra})" if extra else ""))
        lines.append(f"- **{stem}**: " + ", ".join(parts))
    lines += ["", "Read one with pyweb://kit/<Component> (its source, parameters and allowed values)."]
    return "\n".join(lines) + "\n"


def kit_component_markdown(name):
    path, block, doc = _kit_source(name)
    if block is None:
        return None
    vals = _kit_values(name)
    out = [f"# {name}", "", f"`from pyweb.ui import {name}` ({os.path.basename(path)})", ""]
    if doc:
        out += [doc, ""]
    if vals:
        out += [f"- **{d}**: " + ", ".join(f"`{v}`" for v in v_) for d, v_ in vals.items()] + [""]
    out += ["```pyweb", block, "```"]
    return "\n".join(out) + "\n"


def tool_ui(args):
    """The UI kit: list components, show one, copy one into the app, or make a theme."""
    action = args.get("action") or "list"
    if action == "list":
        families = []
        for stem, names in _kit_files().items():
            comps = [{"name": n, **({"values": _kit_values(n)} if _kit_values(n) else {})}
                     for n in names if n[:1].isupper()]
            helpers = [n for n in names if not n[:1].isupper()]
            families.append({"file": stem, "components": comps, **({"helpers": helpers} if helpers else {})})
        return {"families": families,
                "hint": "action=show with component=Name for its parameters and source; every component also "
                        "takes class_=, id= and other attributes (**attrs)"}
    name = args.get("component")
    if action == "show":
        if not name:
            raise ValueError("show needs component, e.g. Dialog")
        path, block, doc = _kit_source(name)
        if block is None:
            raise ValueError(f"no kit component {name!r}; action=list shows them all")
        return {"component": name, "file": os.path.basename(path), "import": f"from pyweb.ui import {name}",
                "about": doc, "params": _kit_params(block), "values": _kit_values(name), "source": block,
                "uri": f"pyweb://kit/{name}"}
    if action == "add":
        if not name:
            raise ValueError("add needs component (or a kit file name such as dialog)")
        from .ui.cli import add
        app_dir = os.path.dirname(os.path.abspath(args.get("path") or "app.pyweb"))
        try:
            written = add([name], app_dir, force=bool(args.get("overwrite")))
        except SystemExit as exc:
            raise ValueError(str(exc)) from None
        out = []
        for path in written:
            with open(path, encoding="utf-8") as fh:
                names = re.findall(r"^def ([A-Z]\w*)\(", fh.read(), re.M)
            stem = os.path.splitext(os.path.basename(path))[0]
            out.append({"file": path, "import": f"from components.{stem} import {', '.join(names)}"})
        return {"written": out, "next": "change the copy freely; replace the pyweb.ui import with the line above"}
    if action == "theme":
        from .ui import theme as T
        from .ui.source_edit import FIELDS, make_theme, save_theme, theme_code
        fields = {k: args[k] for k in FIELDS if args.get(k) not in (None, "")}
        if args.get("preset"):
            if args["preset"] not in T.PRESETS:
                raise ValueError(f"presets: {', '.join(T.PRESETS)}")
            fields = {**T.PRESETS[args["preset"]], **fields}
        theme = make_theme(fields)
        accent_text = T.on_solid(theme.accent)
        res = {"code": theme_code(fields), "theme": {k: getattr(theme, k) for k in FIELDS},
               "accent_text_contrast": round(T.contrast(T.hex_to_rgb(theme.accent), T.hex_to_rgb(accent_text)), 2),
               "presets": list(T.PRESETS), "variables": theme.variables()}
        if args.get("save"):
            res["saved"] = save_theme(args.get("path") or "app.pyweb", fields)
        return res
    raise ValueError("action is list, show, add or theme")


# ------------------------------------------------------------------ accessibility and visual review

AXE_VERSION = "4.14.0"


def _axe_source():
    """axe-core from the npm registry (checksum verified), cached in ~/.cache/pyweb."""
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "pyweb",
                         f"axe-core-{AXE_VERSION}.min.js")
    if not os.path.exists(cache):
        from .packages import Registry
        reg = Registry()
        _version, meta = reg.resolve("axe-core", AXE_VERSION)
        files, _integrity = reg.tarball(meta)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "wb") as fh:
            fh.write(files["axe.min.js"])
    with open(cache, encoding="utf-8") as fh:
        return fh.read()


def tool_review(args):
    """The page in a real browser at each size and colour scheme, with axe-core's accessibility audit."""
    path = args.get("path") or "app.pyweb"
    with open(path, encoding="utf-8") as fh:
        from .a11y import check
        static = check(fh.read(), path)
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return {"static": static, "views": [],
                "hint": "only the source was checked: the browser review needs Playwright "
                        "(pip install playwright && python -m playwright install chromium)"}
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        res = pool.submit(contextvars.copy_context().run, _review, args).result()
    res["static"] = static
    return res


def _review(args):
    from playwright.sync_api import sync_playwright

    path = args.get("path") or "app.pyweb"
    url = args.get("url") or "/"
    schemes = args.get("schemes") or ["light", "dark"]
    widths = args.get("widths") or [1280, 390]
    try:
        axe = _axe_source() if args.get("axe", True) else None
        axe_note = None
    except Exception as exc:  # noqa: BLE001 - offline: still screenshot
        axe, axe_note = None, f"axe-core unavailable ({exc}); screenshots only"
    views, images = [], []
    total = len(schemes) * len(widths)
    with _target(path, args) as (base, cookies), sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for scheme in schemes:
                for width in widths:
                    M.progress(len(views), total, f"{url} at {width}px, {scheme}")
                    ctx = browser.new_context(viewport={"width": int(width), "height": 900 if width > 600 else 800},
                                              color_scheme=scheme)
                    if cookies:
                        ctx.add_cookies([{"name": k, "value": v, "url": base} for k, v in cookies.items()])
                    if axe:
                        ctx.add_init_script(script=axe)
                    page = ctx.new_page()
                    console = []
                    page.on("console", lambda m, c=console: c.append(f"{m.type}: {m.text}")
                            if m.type in ("error", "warning") else None)
                    page.on("pageerror", lambda e, c=console: c.append(f"error: {e}"))
                    resp = page.goto(base + url)
                    if page.query_selector("#pw-state"):
                        page.wait_for_selector("[data-pw-ready]", state="attached", timeout=10000)
                    page.wait_for_timeout(400)                # entrance animations settle
                    view = {"scheme": scheme, "width": width, "status": resp.status if resp else None,
                            "console": console}
                    if axe:
                        view["violations"] = page.evaluate("""async () => {
                          const r = await axe.run(document, {resultTypes: ["violations"]});
                          return r.violations.map((v) => ({id: v.id, impact: v.impact, help: v.help,
                            nodes: v.nodes.slice(0, 5).map((n) => n.target.join(" "))}));
                        }""")
                    images.append(page.screenshot(full_page=bool(args.get("full_page"))))
                    views.append(view)
                    ctx.close()
        finally:
            browser.close()
    serious = sorted({v["id"] for view in views for v in view.get("violations", [])
                      if v["impact"] in ("serious", "critical")})
    out = {"url": url, "views": views, "serious": serious, "ok": not serious and not any(v["console"] for v in views),
           "__images__": images if args.get("screenshots", True) else []}
    if axe_note:
        out["hint"] = axe_note
    return out


@contextlib.contextmanager
def _target(path, args):
    """``(base url, cookies)``: the running ``pyweb dev`` for this app when there is one, else a fresh server.
    With ``as_user``, the browser starts signed in (cookies from the in-process client)."""
    cookies = {}
    if args.get("as_user"):
        client = M.APPS.client(path)
        with contextlib.redirect_stdout(sys.stderr):
            M._sign_in(client, args)
        cookies = dict(client.cookies)
    from . import devlink
    dev = devlink.find(path) if args.get("target", "auto") != "fresh" else None
    if dev is not None and not cookies:
        yield dev["url"].rstrip("/"), {}
        return
    from .testing import serve
    with serve(path) as base:
        yield base.rstrip("/"), cookies


# ------------------------------------------------------------------ upgrade, deploy

def tool_upgrade(args):
    """What to change for this version of PyWeb (pyweb upgrade), optionally applying the safe rewrites."""
    from . import upgrade
    path = args.get("path") or "app.pyweb"
    with contextlib.redirect_stdout(sys.stderr):
        changed = upgrade.fix(path) if args.get("fix") else None
        findings = [f.as_dict() for f in upgrade.check(path)]
    out = {"findings": findings, "to_do": sum(1 for f in findings if f.get("action", True)),
           "docs": "pyweb://docs/upgrading"}
    if changed is not None:
        out["fixed"] = changed
    return out


def tool_deploy_plan(args):
    """What `pyweb deploy TARGET` would run and why, without writing anything."""
    from .cli import production_problems
    from .deploy.plan import describe, make_plan, read_app
    path = args.get("path") or "app.pyweb"
    facts = read_app(path)
    plan = make_plan(args.get("target") or "compose", facts, replicas=args.get("replicas"),
                     domain=args.get("domain") or "")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    return {"target": plan.target, "plan": describe(plan),
            "processes": {"web_replicas": plan.replicas, "processes_per_replica": plan.processes_per_replica,
                          "worker": plan.worker, "migrate_on_deploy": plan.migrate},
            "provision": list(plan.provision), "secrets": plan.secrets, "warnings": plan.warnings,
            "facts": dataclasses.asdict(facts),
            "production_problems": production_problems(source, os.path.dirname(os.path.abspath(path))),
            "next": f"pyweb deploy {plan.target} writes the files (see pyweb://docs/scaling)"}


# ------------------------------------------------------------------ webhooks

def _signed(scheme, secret, body, event_type, event_id):
    ts = str(int(time.time()))
    if scheme == "github":
        return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
                "X-GitHub-Event": event_type, "X-GitHub-Delivery": event_id}
    if scheme == "stripe":
        sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        return {"Stripe-Signature": f"t={ts},v1={sig}"}
    from .webhooks import sign_standard
    return {"webhook-id": event_id, "webhook-timestamp": ts,
            "webhook-signature": sign_standard(secret, event_id, ts, body)}


def tool_webhook_send(args):
    """Send a correctly signed test delivery to one of the app's @app.webhook receivers, then run its job."""
    from . import jobs
    path = args.get("path") or "app.pyweb"
    client = M.APPS.client(path)
    hooks = getattr(client.site.app.app, "webhooks", {}) or {}
    if not hooks:
        raise ValueError("this app has no @app.webhook receivers")
    hook = args.get("hook") or (next(iter(hooks)) if len(hooks) == 1 else None)
    if hook not in hooks:
        raise ValueError(f"pass hook: one of {', '.join(hooks)}")
    rcv = hooks[hook]
    secret = rcv.secret_value()
    if not secret:
        raise ValueError(f"{hook} has no secret (set the environment variable it reads)")
    event_type = args.get("event") or "test.event"
    event_id = args.get("id") or ("evt_" + secrets.token_hex(8))
    data = args.get("data") or {}
    if rcv.scheme == "stripe":
        payload = {"id": event_id, "type": event_type, "data": {"object": data}}
    elif rcv.scheme == "standard":
        payload = {"type": event_type, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "data": data}
    else:
        payload = data
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", **_signed(rcv.scheme, secret, body, event_type, event_id)}
    if args.get("bad_signature"):
        headers = {k: (v[:-4] + "0000" if "ignature" in k else v) for k, v in headers.items()}
    with contextlib.redirect_stdout(sys.stderr):
        resp = client.request("POST", hook, body, headers)
    out = {"hook": hook, "scheme": rcv.scheme, "status": resp.status, "response": resp.text[:500],
           "sent": {"headers": headers, "body": payload}}
    if resp.status == 202 and args.get("run_job", True):
        from .jobs import core
        with contextlib.redirect_stdout(sys.stderr):
            out["jobs_ran"] = jobs.Worker(schedule=False).drain(timeout=30)
        rows = core.backend().list(limit=5, name=f"webhook:{hook}")
        if rows:
            last = rows[0]
            out["job"] = {"state": last.get("state"), "attempts": last.get("attempts"),
                          **({"error": (last.get("last_error") or "")[-1500:]} if last.get("last_error") else {})}
    return out


# ------------------------------------------------------------------ profiling

def tool_profile(args):
    """Where a page or server function spends its time, in-process (cProfile), plus its SQL."""
    import cProfile
    import pstats
    from . import telemetry
    path = os.path.abspath(args.get("path") or "app.pyweb")
    client = M.APPS.client(path)
    with contextlib.redirect_stdout(sys.stderr):
        M._sign_in(client, args)
    fn, url = args.get("function"), args.get("url") or "/"
    repeat = max(1, min(int(args.get("repeat") or 20), 500))

    def once():
        if fn:
            client.rpc(fn, **(args.get("args") or {}))
        else:
            resp = client.get(url)
            if resp.status >= 500:
                raise RuntimeError(f"{url} answered {resp.status}: profile a page that works")
    with contextlib.redirect_stdout(sys.stderr):
        once()                                           # warm up: compile caches, first queries
        times = []
        prof = cProfile.Profile()
        for i in range(repeat):
            t0 = time.perf_counter()
            prof.enable()
            once()
            prof.disable()
            times.append((time.perf_counter() - t0) * 1000)
            if i % 5 == 0:
                M.progress(i, repeat, "profiling")
    last = list(telemetry.RECENT)[-1] if telemetry.RECENT else {}
    stats = pstats.Stats(prof)
    rows = []
    for (file, line, name), (_cc, calls, tottime, cumtime, _callers) in stats.stats.items():
        if "cProfile" in file or name.startswith("<built-in method _lsprof") or \
                file.endswith(("mcp_tools.py", os.sep + "testing.py")):
            continue                                     # the profiler's own frames and the test client's
        rows.append({"function": name, "where": f"{_short(file)}:{line}", "calls": calls // repeat or calls,
                     "self_ms": round(tottime * 1000 / repeat, 3), "total_ms": round(cumtime * 1000 / repeat, 3),
                     "in_app": not file.startswith(("<", "~")) and os.path.dirname(os.path.abspath(file)) == os.path.dirname(path)})
    key = "self_ms" if args.get("sort") == "self" else "total_ms"
    rows.sort(key=lambda r: -r[key])
    times.sort()
    sql = last.get("sql") or []
    return {"target": fn or url, "repeat": repeat, "mean_ms": round(sum(times) / len(times), 3),
            "p50_ms": round(times[len(times) // 2], 3), "p95_ms": round(times[min(len(times) - 1, int(len(times) * .95))], 3),
            "sql_queries": len(sql), "sql": sql[:30], "warnings": last.get("warnings") or [],
            "top": rows[: int(args.get("limit") or 25)],
            "app_code": [r for r in rows if r["in_app"]][:15]}


def _short(file):
    for marker in (os.sep + "pyweb" + os.sep, os.sep + "site-packages" + os.sep, os.sep + "lib" + os.sep):
        if marker in file:
            return file[file.rindex(marker) + 1:]
    return os.path.basename(file)


# ------------------------------------------------------------------ editor intelligence

def tool_explain(args):
    """What the editor would show at a line: where a name runs and why, problems on the line, completions."""
    from . import lsp
    path = args.get("path") or "app.pyweb"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    lines = text.splitlines()
    line = int(args.get("line") or 1)
    if not 1 <= line <= len(lines):
        raise ValueError(f"line is 1 to {len(lines)}")
    src = lines[line - 1]
    name = args.get("name")
    col = args.get("column")
    if name and col is None:
        m = re.search(rf"\b{re.escape(name)}\b", src)
        if not m:
            raise ValueError(f"{name!r} isn't on line {line}: {src.strip()}")
        col = m.start()
    analysis = lsp.Analysis(text, os.path.abspath(path))
    out = {"line": line, "text": src}
    names = [name] if name else list(dict.fromkeys(re.findall(r"\b[A-Za-z_]\w*\b", src)))[:12]
    hovers = {}
    for n in names:
        m = re.search(rf"\b{re.escape(n)}\b", src)
        info = lsp.hover(text, os.path.abspath(path), line - 1, (col if name else m.start()) + 1, analysis)
        if info:
            hovers[n] = info
    out["names"] = hovers
    out["problems"] = [{"line": d["range"]["start"]["line"] + 1, "severity": d.get("severity"),
                        "message": d["message"], **({"code": d["code"]} if d.get("code") else {})}
                       for d in lsp.diagnostics(text, os.path.abspath(path), analysis)
                       if d["range"]["start"]["line"] <= line - 1 <= d["range"]["end"]["line"]]
    if args.get("complete"):
        at = int(col if col is not None else len(src))
        items = lsp.completions(text, os.path.abspath(path), line - 1, at, analysis) or []
        out["completions"] = [{"label": i["label"], **({"detail": i["detail"]} if i.get("detail") else {})}
                              for i in items[:60]]
    return out


# ------------------------------------------------------------------ teams

def tool_teams(args):
    """Organizations in the app's database: plans, members and roles, invitations, tenant rows per org."""
    client, _db, models = M._app_db(args)
    kit = getattr(client.site.app.app, "auth", None)
    if not getattr(kit, "orgs_enabled", False):
        return {"orgs": [], "hint": "this app has no organizations: app.use_auth(orgs=True) (see pyweb://docs/teams)"}
    from .authkit.orgs import Invitation, Membership, Organization
    from .models import system
    tenant = [m for m in models if getattr(m._meta, "tenant", False)]
    out = []
    with system():
        orgs = Organization.query().order("id")
        if args.get("org"):
            orgs = orgs.where(name=args["org"])
        for org in list(orgs)[: int(args.get("limit") or 20)]:
            members = [{"email": getattr(m.user, "email", None), "role": m.role}
                       for m in Membership.where(org_id=org.id).include("user").order("id")]
            invites = [{"email": i.email, "role": i.role, "accepted": i.accepted_at is not None}
                       for i in Invitation.where(org_id=org.id).order("-id")][:20]
            rows = {m.__name__: m.where(org_id=org.id).count() for m in tenant}
            out.append({"id": org.id, "name": org.name, "slug": org.slug, "plan": org.plan, "members": members,
                        "invitations": invites, "rows": rows})
    plans = getattr(kit, "_plans", None) or getattr(kit, "plans_table", None)
    return {"orgs": out, "tenant_models": [m.__name__ for m in tenant],
            **({"plans": plans} if isinstance(plans, dict) else {})}


# ------------------------------------------------------------------ registry

def _devlink_tools():
    from .devlink import TOOLS as DEV
    return DEV


TOOLS = [
    {"name": "pyweb_docs", "fn": tool_docs, "readOnly": True, "title": "Search the docs",
     "description": "Search PyWeb's documentation (every page, the AI guide and the threat model) and get the "
                    "matching sections, or read one page. Use it for any 'how do I...' about PyWeb: teams, forms, "
                    "jobs, live data, deploys, installable and desktop apps, the UI kit, security.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Words to look for, e.g. 'invite members' or 'rate limit'."},
         "page": {"type": "string", "description": "Read this page instead (e.g. teams, ui-and-motion)."},
         "max_results": {"type": "integer"}, "max_chars": {"type": "integer"}}},
     "output": _obj(query=_S, results=_A, page=_S, text=_S, truncated=_B, pages=_A, hint=_S)},
    {"name": "pyweb_ui", "fn": tool_ui, "readOnly": False, "title": "UI kit and theme",
     "description": "The UI kit (from pyweb.ui import ...): action=list for every component with its variants and "
                    "sizes; action=show for one component's parameters, allowed values and source; action=add to "
                    "copy a component's file into the app's components/ folder to change it; action=theme to make "
                    "a Theme from accent/gray/radius/font/density/dark or a preset (save=true writes it into "
                    "App(theme=...)).",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["list", "show", "add", "theme"]},
         "component": {"type": "string", "description": "show/add: e.g. Dialog, DataTable, Sidebar."},
         "path": PATH, "overwrite": {"type": "boolean"},
         "preset": {"type": "string", "description": "theme: indigo, zinc, slate, rose, ocean, violet, emerald, amber."},
         "accent": {"type": "string", "description": "theme: #rrggbb"}, "gray": {"type": "string"},
         "radius": {"type": "number"}, "font": {"type": "string"},
         "density": {"type": "string", "enum": ["compact", "comfortable", "spacious"]},
         "dark": {"type": "string", "enum": ["auto", "light", "dark"]},
         "save": {"type": "boolean", "description": "theme: write it into the app's App(theme=...)."}}},
     "output": _obj(families=_A, hint=_S, component=_S, file=_S, about=_S, params=_A, values=_O, source=_S,
                    uri=_S, written=_A, next=_S, code=_S, theme=_O, accent_text_contrast=_N, presets=_A,
                    variables=_O, saved=_S)},
    {"name": "pyweb_review", "fn": tool_review, "readOnly": True, "title": "Accessibility and visual review",
     "description": "Review a page like a designer and an accessibility tester: screenshots in light and dark "
                    "mode at desktop and phone widths, axe-core's audit of each (serious/critical violations with "
                    "the elements), console errors, and the source's accessibility lint. Uses the running "
                    "`pyweb dev` when there is one. Needs Playwright for the browser part.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH, "url": {"type": "string", "description": "URL path (default /)."},
         "schemes": {"type": "array", "items": {"type": "string", "enum": ["light", "dark"]}},
         "widths": {"type": "array", "items": {"type": "integer"}, "description": "Viewport widths (default 1280, 390)."},
         "axe": {"type": "boolean", "description": "Run axe-core (default true; downloaded once)."},
         "screenshots": {"type": "boolean", "description": "Return the images (default true)."},
         "full_page": {"type": "boolean"}, **USER_PROPS,
         "target": {"type": "string", "enum": ["auto", "fresh"],
                    "description": "auto uses a running pyweb dev; fresh starts its own server."}}},
     "output": _obj(["static"], url=_S, views=_A, serious=_A, ok=_B, static=_A, hint=_S)},
    {"name": "pyweb_upgrade", "fn": tool_upgrade, "readOnly": False, "destructive": False,
     "title": "Upgrade checks",
     "description": "What to change in the app for this version of PyWeb (file, line, what and how), as "
                    "`pyweb upgrade --check` lists it; fix=true applies the safe rewrites first.",
     "inputSchema": {"type": "object", "properties": {"path": PATH, "fix": {"type": "boolean"}}},
     "output": _obj(["findings"], findings=_A, to_do=_I, docs=_S, fixed=_O)},
    {"name": "pyweb_deploy_plan", "fn": tool_deploy_plan, "readOnly": True, "title": "Deploy plan",
     "description": "What `pyweb deploy TARGET` would run for this app and why (processes, database, Redis, "
                    "worker, migrations, secrets to set) and what the production settings are missing, without "
                    "writing files.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH, "target": {"type": "string", "enum": ["docker", "compose", "k8s", "fly", "render", "railway"]},
         "domain": {"type": "string"}, "replicas": {"type": "integer"}}},
     "output": _obj(["plan"], target=_S, plan=_S, processes=_O, provision=_A, secrets=_O, warnings=_A, facts=_O,
                    production_problems=_A, next=_S)},
    {"name": "pyweb_webhook_send", "fn": tool_webhook_send, "readOnly": False, "title": "Test a webhook",
     "description": "Send a correctly signed test delivery (GitHub, Stripe or Standard Webhooks, whatever the "
                    "receiver expects) to one of the app's @app.webhook paths, then run the queued handler job and "
                    "report its result or error. bad_signature=true checks that forged deliveries are refused.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH, "hook": {"type": "string", "description": "The receiver's path, e.g. /hooks/stripe."},
         "event": {"type": "string", "description": "Event type, e.g. invoice.paid or push."},
         "data": {"type": "object"}, "id": {"type": "string"},
         "bad_signature": {"type": "boolean"}, "run_job": {"type": "boolean"}}},
     "output": _obj(["status"], hook=_S, scheme=_S, status=_I, response=_S, sent=_O, jobs_ran=_ANY, job=_O)},
    {"name": "pyweb_profile", "fn": tool_profile, "readOnly": False, "title": "Profile a page or function",
     "description": "Run a page (url) or server function (function) repeatedly in-process under cProfile: "
                    "mean/p50/p95 time, the SQL it runs, N+1 warnings, the slowest functions and the app's own "
                    "code among them.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH, "url": {"type": "string"}, "function": {"type": "string"}, "args": {"type": "object"},
         "repeat": {"type": "integer", "description": "Runs (default 20)."},
         "sort": {"type": "string", "enum": ["total", "self"]}, "limit": {"type": "integer"}, **USER_PROPS}},
     "output": _obj(["mean_ms"], target=_S, repeat=_I, mean_ms=_N, p50_ms=_N, p95_ms=_N, sql_queries=_I, sql=_A,
                    warnings=_A, top=_A, app_code=_A)},
    {"name": "pyweb_explain", "fn": tool_explain, "readOnly": True, "title": "Explain a line",
     "description": "What the editor shows for a line of a .pyweb file: for each name, where it runs (browser or "
                    "server) and why, its signature or npm type; the problems reported on that line; with "
                    "complete=true, the completions at column (or the end of the line).",
     "inputSchema": {"type": "object", "required": ["line"], "properties": {
         "path": PATH, "line": {"type": "integer", "description": "1-based line number."},
         "name": {"type": "string", "description": "Only this name on the line."},
         "column": {"type": "integer", "description": "0-based column for completions."},
         "complete": {"type": "boolean"}}},
     "output": _obj(["line"], line=_I, text=_S, names=_O, problems=_A, completions=_A)},
    {"name": "pyweb_teams", "fn": tool_teams, "readOnly": True, "title": "Organizations",
     "description": "For apps with app.use_auth(orgs=True): every organization with its plan, members and roles, "
                    "invitations, and how many rows of each tenant Model it has.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH, "org": {"type": "string", "description": "Only this organization (by name)."},
         "limit": {"type": "integer"}}},
     "output": _obj(["orgs"], orgs=_A, tenant_models=_A, plans=_O, hint=_S)},
]
TOOLS += _devlink_tools()


def tool_doctor(args):
    """Every `pyweb doctor` check for the app and this machine."""
    from . import doctor
    path = args.get("path") or "app.pyweb"
    checks = doctor.run(path, production=bool(args.get("production")))
    return {"ok": not any(c.status == doctor.FAIL for c in checks),
            "problems": [c.as_dict() for c in checks if c.status != doctor.OK],
            "checks": [c.as_dict() for c in checks]}


TOOLS.append(
    {"name": "pyweb_doctor", "fn": tool_doctor, "readOnly": True, "title": "Project health",
     "description": "Is the project ready to run? Python and PyWeb versions, whether the app compiles, missing "
                    "optional packages, database reachable and migrations applied, Redis, secrets in the source, "
                    "pending upgrade changes, a stale dev server link, tests, the port and editor; with "
                    "production=true also the production environment. Each problem comes with a fix. Run it "
                    "first when something doesn't work and the error isn't obvious.",
     "inputSchema": {"type": "object", "properties": {
         "path": PATH, "production": {"type": "boolean", "description": "Also check production settings."}}},
     "output": _obj(["ok", "checks"], ok=_B, problems=_A, checks=_A)})
