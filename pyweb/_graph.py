"""App-graph discovery shared by `pyweb inspect` and `pyweb dev` (Track D).

Builds a best-effort graph of routes / components / signals / RPC edges
from a project directory. Consumes placement output when available via a
duck-typed ``placement`` module, but never imports or modifies
``placement.py`` itself:

* ``placement.decide(graph_dict) -> dict`` — mapping of node/component
  name to ``{"decision": str, "reason": str}`` (or a plain string
  decision, in which case the reason defaults to ``""``).
* ``placement.explain(node) -> str`` — fallback reason lookup.

Anything missing degrades gracefully to ``"unknown"`` decisions.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROUTE_DEC_RE = re.compile(r"@(?:app\.)?route\(\s*[\"'](?P<path>[^\"']+)[\"']")
COMPONENT_RE = re.compile(r"class\s+(?P<name>[A-Za-z_]\w*)\s*\(\s*(?P<base>Component|ServerComponent|ClientComponent)")
SIGNAL_RE = re.compile(r"(?P<name>[A-Za-z_]\w*)\s*=\s*signal\s*\(")
RPC_DEC_RE = re.compile(r"@(?:app\.)?rpc\b")


@dataclass
class PlacementInfo:
    decision: str = "unknown"
    reason: str = ""


@dataclass
class RouteInfo:
    path: str
    handler: str
    file: str
    line: int


@dataclass
class ComponentInfo:
    name: str
    kind: str  # server | client | shared
    file: str
    line: int
    props: list[str] = field(default_factory=list)
    placement: PlacementInfo = field(default_factory=PlacementInfo)


@dataclass
class SignalInfo:
    name: str
    file: str
    line: int


@dataclass
class RpcInfo:
    name: str
    file: str
    line: int


@dataclass
class AppGraph:
    root: str
    routes: list[RouteInfo] = field(default_factory=list)
    components: list[ComponentInfo] = field(default_factory=list)
    signals: list[SignalInfo] = field(default_factory=list)
    rpcs: list[RpcInfo] = field(default_factory=list)
    rpc_edges: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "routes": [asdict(r) for r in self.routes],
            "components": [
                {**asdict(c), "placement": asdict(c.placement)} for c in self.components
            ],
            "signals": [asdict(s) for s in self.signals],
            "rpcs": [asdict(r) for r in self.rpcs],
            "rpc_edges": list(self.rpc_edges),
        }


def _load_placement_reasons(graph: AppGraph) -> dict[str, PlacementInfo]:
    """Best-effort placement lookup; never raises."""
    try:
        import importlib

        placement = importlib.import_module("placement")
        decide = getattr(placement, "decide", None)
        if callable(decide):
            raw = decide(graph.to_dict()) or {}
            out: dict[str, PlacementInfo] = {}
            for name, val in raw.items():
                if isinstance(val, dict):
                    out[name] = PlacementInfo(
                        decision=str(val.get("decision", "unknown")),
                        reason=str(val.get("reason", "")),
                    )
                else:
                    out[name] = PlacementInfo(decision=str(val))
            return out
        explain = getattr(placement, "explain", None)
        if callable(explain):
            out = {}
            for c in graph.components:
                try:
                    out[c.name] = PlacementInfo("unknown", str(explain(c.name)))
                except Exception:
                    continue
            return out
    except Exception:
        pass
    return {}


def _props_of(class_node: ast.ClassDef) -> list[str]:
    props: list[str] = []
    for node in class_node.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            props.append(node.target.id)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id not in props:
                    props.append(t.id)
    return props


def _scan_file(path: Path, rel: str, graph: AppGraph) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        tree = None

    for m in ROUTE_DEC_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        handler = "?"
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno > line - 3:
                    handler = node.name
                    break
        graph.routes.append(RouteInfo(m.group("path"), handler, rel, line))

    for m in COMPONENT_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        base = m.group("base")
        kind = "server" if base == "ServerComponent" else "client" if base == "ClientComponent" else "shared"
        props: list[str] = []
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == m.group("name"):
                    props = _props_of(node)
                    break
        graph.components.append(ComponentInfo(m.group("name"), kind, rel, line, props))

    for m in SIGNAL_RE.finditer(text):
        graph.signals.append(SignalInfo(m.group("name"), rel, text.count("\n", 0, m.start()) + 1))

    for m in RPC_DEC_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        name = "?"
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno >= line:
                    name = node.name
                    break
        graph.rpcs.append(RpcInfo(name, rel, line))


def discover_app_graph(root: str | Path = ".") -> AppGraph:
    """Scan ``root`` for routes/components/signals/RPCs and attach placement."""
    root_p = Path(root)
    graph = AppGraph(root=str(root_p))
    # Also consult a pipeline artifact manifest if present.
    manifest = root_p / ".pyweb" / "manifest.json"
    if manifest.exists():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            for r in data.get("routes", []):
                graph.routes.append(
                    RouteInfo(str(r.get("path", "?")), str(r.get("handler", "?")),
                              str(r.get("file", ".pyweb/manifest")), int(r.get("line", 0)))
                )
        except (OSError, ValueError):
            pass
    for path in sorted(root_p.rglob("*.py")):
        if ".venv" in path.parts or "__pycache__" in path.parts or "site-packages" in path.parts:
            continue
        _scan_file(path, str(path.relative_to(root_p)), graph)
    reasons = _load_placement_reasons(graph)
    for c in graph.components:
        if c.name in reasons:
            c.placement = reasons[c.name]
        elif c.kind == "server":
            c.placement = PlacementInfo("server", "ServerComponent base renders on the server")
        elif c.kind == "client":
            c.placement = PlacementInfo("client", "ClientComponent base hydrates in the browser")
        else:
            c.placement = PlacementInfo("shared", "no placement hint; defaults to shared rendering")
    rpc_names = {r.name for r in graph.rpcs}
    graph.rpc_edges = [
        {"from": c.name, "to": r, "via": "rpc"}
        for c in graph.components
        for r in sorted(rpc_names)
    ] if rpc_names else []
    return graph
