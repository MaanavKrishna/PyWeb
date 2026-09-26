# 08 - Browser APIs

> Source: `pyweb/browser.py` (every API has a server-safe stub the test
> suite exercises in `tests/test_browser_build.py`).

## Pattern

```python
from pyweb.browser import location, storage  # module import
from pyweb import platform

if platform.web:
    position = await location.current()
```

(`from pyweb.browser import ...` and `pyweb.browser_api` both reach
the module. Note: `import pyweb.browser as x` binds the `@browser`
decorator instead — same name, Python package-attr shadowing — so
prefer the `from`-import above. `from pyweb import browser` is always
the decorator.)

## Rules

1. Sensor/action APIs (`location`, `camera`, `clipboard`,
   `notifications`, `share`, `filesystem`, `bluetooth`) **raise**
   `BrowserUnavailable` on the server — fake sensor data is worse than
   an error. Guard with `if platform.web:` or call from event handlers.
2. Query-style APIs (`fetch`, `permissions`, `vibrate`, `idb`,
   `storage`) return safe defaults on the server so SSR paths work.
3. `idb` (IndexedDB KV) falls back to memory on the server — offline
   persistence in the browser, plain dicts in tests.

## Binding map for codegen

```python
pyweb.browser.bindings()
# {'storage': 'localStorage', 'clipboard': 'navigator.clipboard', ...}
```

The compiler maps each stub to its JS global; server bundles never
include the binding. `pyweb inspect --security` flags secret-like
values crossing into browser-placed code.
