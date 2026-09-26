# 13 - Plugins and platform targets

> Sources: `pyweb/plugins.py`, `pyweb/platform.py`, `pyweb/bench.py`
> (tested in `tests/test_plugins_platform_bench.py`).

## Plugins participate in the graph

```python
from pyweb.plugins import Plugin

stripe = Plugin("stripe", version="1.0.0")
stripe.config_schema = {"api_key": {"required": True}}

@stripe.server
def checkout(cart_id: str) -> str: ...

app = App(plugins=[stripe.configure(api_key="sk-live")])
```

Plugin RPC keeps `@server` typing (placement + security checks still
apply), routes merge into `app.pages`, build steps run in order.
`describe()` redacts `*key*`/`*secret*` config values — safe to log.

## Platform branches prune per target

```python
from pyweb import platform

if platform.web:
    <Article>...</Article>
if platform.mobile:
    <NativeList>...</NativeList>
```

The compiler keeps the branch for its target and prunes the rest via
AST (`platform.prune(source, "ios")` drops web code exactly, no
regexes). Shared logic lives outside the branch — native controls per
target, shared state/RPC/models everywhere. `PYWEB_TARGET` selects:
`web ios android desktop edge server`.

## Benchmarks, not vibes

```bash
python -m pyweb.bench
# {"counter": {"source_lines": 12, "js_bytes": 612, ...}, ...}
# total JS across 3 apps: 2323B
```

Per-app source lines, JS/HTML bytes, signal counts, compile + SSR
latency. Record baselines in-repo; regressions show up as diffs.
