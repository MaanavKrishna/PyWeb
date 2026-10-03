# Contributing

Thanks for helping. Bug reports with a minimal `.pyweb` reproduction are
the most valuable contribution of all.

## Set up

```bash
git clone https://github.com/MaanavKrishna/PyWeb && cd PyWeb
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test,crypto]" ruff
python -m playwright install chromium     # for browser tests
```

Node.js (18+) is needed for the runtime and translator tests.

## Run the checks

```bash
ruff check pyweb tests website benchmarks
python -m pytest tests --ignore=tests/integration      # unit + browser tests
```

Integration tests need real services:

```bash
PYWEB_TEST_POSTGRES=postgresql://user@127.0.0.1/db \
PYWEB_TEST_MYSQL=mysql://root:pw@127.0.0.1/db \
PYWEB_TEST_REDIS=redis://127.0.0.1:6379/0 \
python -m pytest tests/integration -rs
```

## Where things live

See [ARCHITECTURE.md](ARCHITECTURE.md). In short: the parser, analysis,
translation and code generation are in `pyweb/compiler/`, the browser
runtime is `pyweb/runtime/browser/runtime.js`, server rendering is
`pyweb/ssr.py`, and the request path is `pyweb/app_loader.py` →
`pyweb/runtime/server` → `pyweb/hosting.py`.

## Guidelines

- **Tests first for bugs.** Add a failing test that shows the bug, then
  fix it.
- **Translator changes** need cases in `tests/test_pyjs_semantics.py`, so
  CPython and the compiled JavaScript stay in agreement.
- **Runtime changes** need a test in `tests/test_runtime_signals.py`
  (Node) or `tests/e2e/test_runtime_dom.py` (browser).
- **Docs:** every ```` ```pyweb ```` block in `docs/` must compile (the test
  suite checks). Rebuild the site with `python website/build.py`.
- **No new required dependencies.** PyWeb's core is standard-library only;
  integrations go behind optional extras.
- Keep the changelog's "Unreleased" section up to date in your PR.

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml` (for example `0.2.0` → `0.3.0`).
2. In `CHANGELOG.md`, move the "Unreleased" entries under a new
   `## [0.3.0]` heading, and add the version to the "Released so far" list
   in `docs/16-limitations-roadmap.md` and the README's version table.
3. Push to `main` and wait for CI to pass.
4. On GitHub: **Releases → Draft a new release**, create tag `v0.3.0` on
   `main`, title `PyWeb 0.3.0`, leave the notes empty, **Publish release**.
5. The Release workflow checks the tag matches `pyproject.toml`, builds,
   publishes to PyPI (trusted publishing) and attaches the files and the
   changelog section to the release. PyPI never accepts the same version
   twice, so a failed upload needs a new version number.
