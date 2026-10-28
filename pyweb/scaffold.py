"""Making a new PyWeb project: ``pyweb new``, the ``pyweb_new_app`` MCP tool and the benchmarks.

Writes the template's app, its tests, AGENTS.md (instructions for AI assistants), a README and, for
templates with data, the first migration.
"""

from __future__ import annotations

import json
import os
import re
import sys

from ._templates import TEMPLATES

HERE = os.path.dirname(os.path.abspath(__file__))


def _guide_text(section=None):
    with open(os.path.join(HERE, "ai", "guide.md"), encoding="utf-8") as fh:
        text = fh.read()
    if not section:
        return text
    parts = re.split(r"(?m)^## ", text)
    want = str(section).strip().lower()
    for part in parts[1:]:
        title = part.split("\n", 1)[0].lower()
        if title.startswith(want) or want in title:
            return "## " + part.strip()
    titles = [p.split("\n", 1)[0] for p in parts[1:]]
    return f"No section matching {section!r}. Sections: " + "; ".join(titles)


def _template_dir(name):
    """The folder of a multi-file template (``templates/<name>/``), or None."""
    path = os.path.join(HERE, "templates", name)
    return path if os.path.isdir(path) else None


def _template_source(name, title=None):
    if name == "blank":
        source = BLANK_APP
    else:
        folder = _template_dir(name)
        path = os.path.join(folder, "app.pyweb") if folder else os.path.join(HERE, "templates", f"{name}.pyweb")
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    if title:
        source = re.sub(r'App\(title="[^"]*"', f'App(title={json.dumps(title)}', source, count=1)
    return source


BLANK_APP = '''from pyweb import App

app = App(title="My app")


@app.page("/")
def Home():
    <main>
        <h1>Hello from PyWeb</h1>
    </main>
'''


def agent_instructions():
    """Contents of AGENTS.md written into new projects."""
    return (
        "# Instructions for AI coding agents\n\n"
        "This project is a PyWeb app (`pip install pyweb-stack`, `import pyweb`). The whole app is\n"
        "`app.pyweb`: Python plus HTML-like markup. Pages render on the server, event handlers\n"
        "compile to JavaScript, and `@server` functions run on the server.\n\n"
        "## Workflow\n\n"
        "- Run `pyweb check app.pyweb` after every change and fix errors by line number.\n"
        "- `pyweb inspect app.pyweb` shows what runs in the browser vs the server.\n"
        "- `pyweb dev app.pyweb` serves at http://localhost:8000 with live reload.\n"
        "- `pytest` runs `test_app.py`; add a test for each server function and page you change.\n"
        "- If the `pyweb` MCP server is available, use its tools: pyweb_guide and pyweb_docs to learn,\n"
        "  pyweb_check after each edit, pyweb_ui for components, pyweb_render/pyweb_call/pyweb_review to\n"
        "  verify, pyweb_test, and pyweb_dev/pyweb_logs for errors while `pyweb dev` runs.\n\n"
        + _guide_text().split("\n", 2)[2]
    )


STARTER_TEST = '''"""Tests for this app. Run them with `pytest` (or the pyweb_test MCP tool)."""

from pathlib import Path

import pytest

from pyweb import RPCError  # noqa: F401  (for testing server-function errors)
from pyweb.testing import TestClient

APP = str(Path(__file__).parent / "app.pyweb")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # keep files the app writes (e.g. SQLite) out of the project
    return TestClient(APP)


def test_home_page_renders(client):
    page = client.get("/")
    assert page.status in (200, 303), page.text[:500]


# Server functions are called exactly like the browser calls them:
#
# def test_save(client):
#     assert client.rpc("save", title="Hello") == ...
#     with pytest.raises(RPCError) as err:
#         client.rpc("save", title="")
#     assert err.value.code == "validation_error"
'''


def scaffold(directory, template="blank", title=None, overwrite=False):
    """Create a new app directory. Returns the list of files written."""
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; choose one of {', '.join(TEMPLATES)}")
    app_path = os.path.join(directory, "app.pyweb")
    if os.path.exists(app_path) and not overwrite:
        raise FileExistsError(f"{app_path} already exists (pass overwrite=true to replace it)")
    if title is None:
        title = os.path.basename(os.path.abspath(directory)).replace("-", " ").replace("_", " ").title()
    os.makedirs(os.path.join(directory, "static"), exist_ok=True)
    source = _template_source(template, title)
    files = {"app.pyweb": source,
             "test_app.py": STARTER_TEST,
             "AGENTS.md": agent_instructions(),
             "CLAUDE.md": "@AGENTS.md\n",
             ".gitignore": "/dist/\n__pycache__/\n*.db\n.env\n"}
    folder = _template_dir(template)
    if folder:                    # multi-file templates bring their own tests, seeds, ...
        for fn in sorted(os.listdir(folder)):
            if fn != "app.pyweb" and os.path.isfile(os.path.join(folder, fn)) and not fn.endswith(".pyc"):
                with open(os.path.join(folder, fn), encoding="utf-8") as fh:
                    files[fn] = fh.read()
    if "database=" in source:
        files["README.md"] = _readme(title, template, "seeds.py" in files)
    if "/static/app.css" in source:
        with open(os.path.join(HERE, "templates", "app.css"), encoding="utf-8") as fh:
            files["static/app.css"] = fh.read()
    written = []
    for rel, text in files.items():
        path = os.path.join(directory, rel)
        if rel != "app.pyweb" and os.path.exists(path) and not overwrite:
            continue
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(path)
    if "database=" in source and not os.path.isdir(os.path.join(directory, "migrations")):
        written += _initial_migration(directory)
    return written


def _initial_migration(directory):
    """``migrations/0001_initial.py`` for the template's Models (in a separate process, on a
    throwaway database, so nothing is created in the project and no Models leak into this one)."""
    import subprocess
    import tempfile
    app = os.path.abspath(os.path.join(directory, "app.pyweb"))
    out = os.path.abspath(os.path.join(directory, "migrations"))
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "PYWEB_WORKER": "0", "PYWEB_ENV": "development"}
        env.pop("DATABASE_URL", None)
        done = subprocess.run([sys.executable, "-m", "pyweb.cli", "db", "diff", "--app", app, "--database",
                               "sqlite:///:memory:", "--migrations", out, "--name", "initial"],
                              cwd=tmp, env=env, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"couldn't write the first migration: {(done.stderr or done.stdout).strip()[-500:]}")
    return sorted(os.path.join(out, f) for f in os.listdir(out) if f.endswith(".py"))


def _readme(title, template, has_seeds):
    seed = "pyweb db seed                # example data (see seeds.py)\n" if has_seeds else ""
    if template == "desktop":
        run = ('pip install "pyweb-stack[desktop]"\n'
               "pyweb desktop app.pyweb      # its own window; --dev reloads on save\n"
               "pyweb dev app.pyweb          # or in a browser, at http://localhost:8000\n")
    else:
        run = ('pip install "pyweb-stack[auth]"\n'
               "pyweb dev app.pyweb          # http://localhost:8000, reloads on save, with the dev toolbar\n")
    if template == "desktop":
        ship = ("pyweb build --desktop app.pyweb --package   # an installer for this OS\n```\n\n"
                "The data lives in the user's data folder; see the desktop guide for signing installers.\n")
    else:
        ship = ("pyweb deploy compose         # or: docker, k8s, fly, render, railway\n```\n\n"
                "`pyweb deploy` explains what it will run (database, Redis, workers) and why,\n"
                "then writes the files. `pyweb check --production` checks the settings.\n")
    return f"""# {title}

A [PyWeb](https://maanavkrishna.github.io/PyWeb/) app, from the `{template}` template.

```bash
{run}{seed}pytest                       # the tests in test_app.py
```

## Changing the data

Edit the Models in `app.pyweb`, then:

```bash
pyweb db diff                # writes migrations/NNNN_*.py from your changes (commit it)
pyweb db upgrade             # applies it (`pyweb dev` does this for you)
```

## Shipping it

```bash
{ship}"""
