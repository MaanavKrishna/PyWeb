"""Multi-target platform branches: web / mobile / desktop / edge.

```python
from pyweb import platform

if platform.web:
    <Article>...</Article>

if platform.mobile:
    <NativeList>...</NativeList>
```

On the server all flags except ``server`` are False; the compiler
evaluates the branch for its target and prunes dead branches per
output (web bundle drops mobile code and vice versa). Shared business
logic lives outside the branch. This is intentionally *not* a
write-once-render-everywhere widget set — each target keeps its
native controls; PyWeb shares state, RPC, models and routing.
"""

from __future__ import annotations

import os

_TARGET = os.environ.get("PYWEB_TARGET", "web")

web = _TARGET in ("web", "server")
mobile = _TARGET in ("ios", "android")
desktop = _TARGET in ("desktop",)
server = True
edge = _TARGET == "edge"

#: All known targets (used by the compiler to prune branches).
TARGETS = ("web", "ios", "android", "desktop", "edge", "server")


def target() -> str:
    return _TARGET


def prune(source: str, keep: str) -> str:
    """Prune platform branches for ``keep`` from ``source`` text.

    Keeps ``if platform.<keep>:`` blocks, drops the other platform
    branches, unwraps shared code. Operates on source text with ``ast``
    so pruning is exact, not regex-based.
    """
    import ast as _ast
    tree = _ast.parse(source)
    keep_attr = {"ios": "mobile", "android": "mobile"}.get(keep, keep)

    class Pruner(_ast.NodeTransformer):
        def visit_If(self, node):  # noqa: N802
            self.generic_visit(node)
            test = node.test
            if (isinstance(test, _ast.Attribute)
                    and isinstance(test.value, _ast.Name)
                    and test.value.id == "platform"):
                if test.attr == keep_attr or (
                        keep_attr == "mobile" and test.attr == "mobile"):
                    return node.body
                if test.attr in ("web", "mobile", "desktop", "edge",
                                 "server"):
                    return node.orelse or None
            return node

    pruned = Pruner().visit(tree)
    _ast.fix_missing_locations(pruned)
    return _ast.unparse(pruned)
