"""Plugin architecture: typed, compiler-visible extensions.

A plugin participates in the application graph instead of hiding
behind stringly-typed hooks — it declares routes, RPC functions,
models, browser bindings and build steps with types, so placement,
security checks and codegen keep working across plugin boundaries.

```python
stripe = Plugin("stripe", version="1.0.0")

@stripe.server
def checkout(cart_id: str) -> str:
    ...

app = App(plugins=[stripe])
```
"""

from __future__ import annotations


class Plugin:
    """Namespace for plugin-provided routes, RPC, models and assets."""

    def __init__(self, name, *, version="0.1.0"):
        self.name = name
        self.version = version
        self.routes: list = []
        self.rpc: list = []
        self.models: list = []
        self.browser_bindings: dict = {}
        self.build_steps: list = []
        self.config_schema: dict = {}
        self.config: dict = {}

    def configure(self, **config):
        """Validate ``config`` against the plugin's schema, store it."""
        for key, spec in self.config_schema.items():
            if spec.get("required") and key not in config:
                raise ValueError(
                    f"plugin {self.name!r} requires config key {key!r}")
        self.config.update(config)
        return self

    def server(self, fn=None, **opts):
        from pyweb.decorators import server as _server
        if fn is None:
            return lambda f: self.server(f, **opts)
        fn = _server(fn)
        fn.__pyweb_plugin__ = self.name
        self.rpc.append(fn)
        return fn

    def page(self, route, **opts):
        def deco(fn):
            fn.__pyweb_route__ = route
            fn.__pyweb_plugin__ = self.name
            self.routes.append((route, fn))
            return fn
        return deco

    def model(self, cls):
        cls.__pyweb_plugin__ = self.name
        self.models.append(cls)
        return cls

    def browser_binding(self, name, js):
        """Declare a JS global the plugin's browser code needs."""
        self.browser_bindings[name] = js
        return js

    def on_build(self, fn):
        """Register a ``fn(manifest, out_dir)`` build step."""
        self.build_steps.append(fn)
        return fn

    def describe(self) -> dict:
        return {"name": self.name, "version": self.version,
                "routes": [r for r, _ in self.routes],
                "rpc": [f.__name__ for f in self.rpc],
                "models": [m.__name__ for m in self.models],
                "bindings": dict(self.browser_bindings),
                "config": {k: ("***" if "secret" in k.lower() or
                               "key" in k.lower() else v)
                           for k, v in self.config.items()}}

    def __repr__(self):
        return f"Plugin({self.name!r}, version={self.version!r})"


class Registry:
    """All plugins attached to an app; runs build steps in order."""

    def __init__(self):
        self._plugins: dict[str, Plugin] = {}

    def add(self, plugin: Plugin):
        if plugin.name in self._plugins:
            raise ValueError(f"duplicate plugin {plugin.name!r}")
        self._plugins[plugin.name] = plugin
        return plugin

    def get(self, name) -> Plugin:
        return self._plugins[name]

    def run_build_steps(self, manifest: dict, out_dir: str):
        for plugin in self._plugins.values():
            for step in plugin.build_steps:
                step(manifest, out_dir)

    def describe(self):
        return {name: p.describe() for name, p in self._plugins.items()}
