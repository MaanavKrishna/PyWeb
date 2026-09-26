"""Typed Python stub generation from TypeScript ``.d.ts`` files (Track D).

Parses ``interface`` blocks with primitive / array / optional fields and
emits ``@dataclass`` types plus ``from_dict``/``validate_*`` validators.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_TS_SCALARS = {
    "string": "str",
    "number": "float",
    "boolean": "bool",
    "any": "Any",
    "unknown": "Any",
    "void": "None",
    "null": "None",
    "undefined": "None",
    "object": "dict",
}

_INTERFACE_RE = re.compile(
    r"interface\s+(?P<name>[A-Za-z_][\w]*)\s*"
    r"(?:extends\s+(?P<base>[A-Za-z_][\w]*))?\s*\{(?P<body>[^}]*)\}",
    re.DOTALL,
)
_FIELD_RE = re.compile(
    r"(?P<name>[A-Za-z_][\w]*)(?P<opt>\?)?\s*:\s*(?P<type>[^;,\n]+)\s*;?,?\s*$"
)


@dataclass
class DtsField:
    name: str
    ts_type: str
    py_type: str
    optional: bool
    nested: str | None = None  # referenced interface name, if any


@dataclass
class DtsInterface:
    name: str
    base: str | None
    fields: list[DtsField] = field(default_factory=list)


def _ts_to_py(ts_type: str, known: set[str]) -> tuple[str, str | None]:
    """Map a TS type to a Python annotation; returns (annotation, nested)."""
    t = ts_type.strip()
    nested: str | None = None

    def scalar(tok: str) -> str:
        tok = tok.strip()
        if (tok.startswith('"') and tok.endswith('"')) or (
            tok.startswith("'") and tok.endswith("'")
        ):
            return "str"
        if tok in _TS_SCALARS:
            return _TS_SCALARS[tok]
        if re.fullmatch(r"-?\d+", tok):
            return "int"
        if re.fullmatch(r"-?\d*\.\d+", tok):
            return "float"
        if tok in known:
            return tok
        return "Any"

    # Array<X> form.
    m = re.fullmatch(r"Array\s*<\s*(.+)\s*>", t)
    if m:
        inner, nested = _ts_to_py(m.group(1), known)
        return f"list[{inner}]", nested
    # X[] form (supports nesting like string[][]).
    while t.endswith("[]"):
        t = t[:-2].strip()
        inner, nested = _ts_to_py(t, known)
        return f"list[{inner}]", nested
    # Union: string-literal unions collapse to str, `T | undefined`
    # collapses to T, anything else degrades to Any.
    if "|" in t:
        parts = [p.strip() for p in t.split("|")]
        parts = [p for p in parts if p not in ("undefined", "null")]
        if parts and all(
            (p.startswith('"') or p.startswith("'")) for p in parts
        ):
            return "str", None
        if len(parts) == 1:
            return _ts_to_py(parts[0], known)
        non_any = [scalar(p) for p in parts if scalar(p) != "Any"]
        if len(non_any) == 1 and all(p in known or p in _TS_SCALARS.values() for p in non_any):
            return non_any[0], (non_any[0] if non_any[0] in known else None)
        return "Any", None
    base = scalar(t)
    if base in known:
        nested = base
    return base, nested


def parse_dts(source: str) -> list[DtsInterface]:
    """Parse ``interface`` blocks out of ``.d.ts`` source text."""
    found = list(_INTERFACE_RE.finditer(source))
    known = {m.group("name") for m in found}
    out: list[DtsInterface] = []
    for m in found:
        iface = DtsInterface(name=m.group("name"), base=m.group("base"))
        for raw_line in m.group("body").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue
            fm = _FIELD_RE.match(line)
            if not fm:
                continue
            py_type, nested = _ts_to_py(fm.group("type"), known)
            iface.fields.append(
                DtsField(
                    name=fm.group("name"),
                    ts_type=fm.group("type").strip(),
                    py_type=py_type,
                    optional=bool(fm.group("opt")),
                    nested=nested,
                )
            )
        out.append(iface)
    return out


def _coerce(nested: str | None, py_type: str, value_expr: str) -> str:
    if nested is None:
        return value_expr
    if py_type.startswith("list["):
        return f"[{nested}.from_dict(v) if isinstance(v, dict) else v for v in {value_expr}]"
    return f"{nested}.from_dict({value_expr}) if isinstance({value_expr}, dict) else {value_expr}"


def generate_stub(interfaces: list[DtsInterface]) -> str:
    """Render parsed interfaces as Python dataclasses with validators."""
    lines = [
        '"""Generated stubs — do not edit. Source: .d.ts via `pyweb npm`.',
        "",
        "Regenerate with: pyweb npm <file.d.ts> [-o out.py]",
        '"""',
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "from typing import Any, Optional",
        "",
        "",
    ]
    known = {i.name for i in interfaces}
    for base in dict.fromkeys(i.base for i in interfaces if i.base and i.base not in known):
        lines += ["@dataclass", f"class {base}:", "    pass", "", ""]
    for iface in interfaces:
        base = f"({iface.base})" if iface.base else ""
        lines.append("@dataclass")
        lines.append(f"class {iface.name}{base}:")
        fields = sorted(iface.fields, key=lambda f: f.optional)
        if not fields and not iface.base:
            lines.append("    pass")
        else:
            for f in fields:
                ann = f"Optional[{f.py_type}]" if f.optional else f.py_type
                default = " = None" if f.optional else ""
                lines.append(f"    {f.name}: {ann}{default}")
        lines.append("")
        lines.append("    @classmethod")
        lines.append(f"    def from_dict(cls, data: dict) -> {iface.name}:")
        lines.append('        if not isinstance(data, dict):')
        lines.append(f'            raise TypeError(f"{iface.name} must be a dict")')
        lines.append("        kwargs: dict[str, Any] = {}")
        for f in fields:
            if f.optional:
                val = _coerce(f.nested, f.py_type, f"data[{f.name!r}]")
                lines.append(f"        if data.get({f.name!r}) is not None:")
                lines.append(f"            kwargs[{f.name!r}] = {val}")
            else:
                val = _coerce(f.nested, f.py_type, "_v")
                lines.append(f"        if {f.name!r} not in data:")
                lines.append(f"            raise TypeError(f\"{iface.name} missing required field: {f.name}\")")
                lines.append(f"        _v = data[{f.name!r}]")
                lines.append(f"        kwargs[{f.name!r}] = {val}")
        lines.append("        try:")
        lines.append("            return cls(**kwargs)")
        lines.append("        except TypeError as exc:")
        lines.append(f"            raise TypeError(f\"invalid {iface.name}: {{exc}}\") from exc")
        lines.append("")
        lines.append("")
        lines.append(f"def validate_{iface.name}(data: dict) -> {iface.name}:")
        lines.append(f'    """Validate a dict against {iface.name}; raise TypeError if invalid."""')
        lines.append(f"    return {iface.name}.from_dict(data)")
        lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def generate_stubs_from_file(dts_path: str | Path, out_path: str | Path | None = None) -> str:
    """Parse a ``.d.ts`` file and return (and optionally write) stub code."""
    source = Path(dts_path).read_text(encoding="utf-8")
    code = generate_stub(parse_dts(source))
    if out_path is not None:
        Path(out_path).write_text(code, encoding="utf-8")
    return code


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``pyweb npm <file.d.ts> [-o out.py]``."""
    import argparse

    ap = argparse.ArgumentParser(prog="pyweb npm", description="Generate typed Python stubs from .d.ts")
    ap.add_argument("dts", help="Input .d.ts file")
    ap.add_argument("-o", "--out", default=None, help="Write stubs to file (default: stdout)")
    args = ap.parse_args(argv)
    try:
        code = generate_stubs_from_file(args.dts, args.out)
    except FileNotFoundError:
        print(f"npm: {args.dts}: file not found")
        return 1
    if args.out:
        print(f"npm: wrote {args.out}")
    else:
        print(code, end="")
    return 0
