"""PyWeb IR (PIR): typed app graph. Textual form is debug-only.

Textual example:
    page Home("/") signals [count@3] rpc [get_count()->Int@12]
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IRNode:
    kind: str
    name: str
    type: str = "Any"
    line: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class AppGraph:
    pages: list = field(default_factory=list)
    rpc: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    computeds: dict = field(default_factory=dict)
    placement: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    sourcemap: list = field(default_factory=list)  # (generated_desc, source_line)


def build_graph(pages, rpc_specs, signals, computeds, placement, edges):
    g = AppGraph(pages=pages, rpc=rpc_specs, signals=signals,
                 computeds=computeds, placement=placement, edges=edges)
    for s in signals:
        g.sourcemap.append((f"sig:{s}", 0))
    for r in rpc_specs:
        g.sourcemap.append((f"rpc:{r['name']}", r["line"]))
    return g


def to_text(g: AppGraph):
    lines = []
    for p in g.pages:
        lines.append(f"page {p['name']}(\"{p['route']}\") signals {p.get('signals', [])} rpc {[r['name'] for r in g.rpc]}")
    for s, c in g.computeds.items():
        lines.append(f"  computed {s} = {c['code']} deps {c['deps']}")
    for sym, (loc, why) in g.placement.items():
        lines.append(f"  place {sym}: {loc}  # {why}")
    return "\n".join(lines)
