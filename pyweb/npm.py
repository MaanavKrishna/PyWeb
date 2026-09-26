"""npm interop stub: typed package references + .d.ts binding sketch."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def package(name: str, version: str = "latest"):
    return {"npm": name, "version": version, "kind": "esm"}


_TS_SCALARS = {"number": "float", "string": "str", "boolean": "bool",
               "any": "object", "unknown": "object", "void": "None",
               "null": "None", "undefined": "None", "Date": "str"}


def _ts_to_py(ts_type):
    import re
    t = ts_type.strip().rstrip(";").strip()
    if t.endswith("[]"):
        return f"list[{_ts_to_py(t[:-2])}]"
    m = re.match(r"Array<(.+)>", t)
    if m:
        return f"list[{_ts_to_py(m.group(1))}]"
    if "|" in t:
        parts = [_ts_to_py(p) for p in t.split("|")]
        parts = [p for p in parts if p != "None"]
        if len(parts) != len(t.split("|")):
            return f"Optional[{' | '.join(parts) if parts else 'object'}]"
        return " | ".join(parts) if parts else "object"
    m = re.match(r"Record<(.+),\s*(.+)>", t)
    if m:
        return f"dict[{_ts_to_py(m.group(1))}, {_ts_to_py(m.group(2))}]"
    return _TS_SCALARS.get(t, t)


def from_dts(dts: str):
    """Parse `interface`/`type` declarations into typed Python dataclasses."""
    import re
    out = ["from __future__ import annotations", "from dataclasses import dataclass",
           "from typing import Optional", ""]
    found = False
    for m in re.finditer(r"(?:interface|type)\s+(\w+)(?:\s*=\s*)?\s*\{([^}]*)\}", dts):
        found = True
        name, body = m.group(1), m.group(2)
        out.append("@dataclass")
        out.append(f"class {name}:")
        n_fields = 0
        for line in body.splitlines():
            line = line.split("//")[0].strip().rstrip(",;")
            fm = re.match(r"(?:readonly\s+)?(\w+)(\?)?:\s*(.+)", line)
            if fm:
                fname, opt, ftype = fm.group(1), fm.group(2), fm.group(3).strip()
                py_t = _ts_to_py(ftype)
                out.append(f"    {fname}: {py_t} = None" if opt else f"    {fname}: {py_t}")
                n_fields += 1
        if not n_fields:
            out.append("    pass")
        out.append("")
    for m in re.finditer(r"type\s+(\w+)\s*=\s*([^;]+);", dts):
        if m.group(1) not in dts:
            out.append(f"{m.group(1)} = {_ts_to_py(m.group(2))}")
    return "\n".join(out) if found else "\n".join(out)


@dataclass
class DtsField:
    name: str
    ts_type: str
    py_type: str
    optional: bool
    nested: str | None = None


@dataclass
class DtsInterface:
    name: str
    base: str | None
    fields: list = field(default_factory=list)


_INTERFACE_RE = None
_FIELD_RE = None


def _get_interface_re():
    import re as _re
    global _INTERFACE_RE
    if _INTERFACE_RE is None:
        _INTERFACE_RE = _re.compile(
            r"interface\s+(?P<name>[A-Za-z_][\w]*)\s*"
            r"(?:extends\s+(?P<base>[A-Za-z_][\w]*))?\s*\{(?P<body>[^}]*)\}",
            _re.DOTALL)
    return _INTERFACE_RE


def _get_field_re():
    import re as _re
    global _FIELD_RE
    if _FIELD_RE is None:
        _FIELD_RE = _re.compile(
            r"(?P<name>[A-Za-z_][\w]*)(?P<opt>\?)?\s*:\s*(?P<type>[^;,\n]+)\s*;?,?\s*$")
    return _FIELD_RE


def _ts_to_py_typed(ts_type, known):
    import re as _re
    _KNOWN_SCALARS = {"string": "str", "number": "float", "boolean": "bool",
                      "any": "Any", "unknown": "Any", "void": "None",
                      "null": "None", "undefined": "None", "object": "dict"}
    t = ts_type.strip()
    nested = None

    def scalar(tok):
        tok = tok.strip()
        if (tok.startswith('"') and tok.endswith('"')) or (
                tok.startswith("'") and tok.endswith("'")):
            return "str"
        if tok in _KNOWN_SCALARS:
            return _KNOWN_SCALARS[tok]
        if _re.fullmatch(r"-?\d+", tok):
            return "int"
        if _re.fullmatch(r"-?\d*\.\d+", tok):
            return "float"
        if tok in known:
            return tok
        return "Any"

    m = _re.fullmatch(r"Array\s*<\s*(.+)\s*>", t)
    if m:
        inner, nested = _ts_to_py_typed(m.group(1), known)
        return f"list[{inner}]", nested
    while t.endswith("[]"):
        t = t[:-2].strip()
        inner, nested = _ts_to_py_typed(t, known)
        return f"list[{inner}]", nested
    if "|" in t:
        parts = [p.strip() for p in t.split("|")]
        parts = [p for p in parts if p not in ("undefined", "null")]
        if parts and all((p.startswith('"') or p.startswith("'")) for p in parts):
            return "str", None
        if len(parts) == 1:
            return _ts_to_py_typed(parts[0], known)
        non_any = [scalar(p) for p in parts if scalar(p) != "Any"]
        if len(non_any) == 1:
            return non_any[0], (non_any[0] if non_any[0] in known else None)
        return "Any", None
    base = scalar(t)
    if base in known:
        nested = base
    return base, nested


def parse_dts(source):
    found = list(_get_interface_re().finditer(source))
    known = {m.group("name") for m in found}
    out = []
    for m in found:
        iface = DtsInterface(name=m.group("name"), base=m.group("base"))
        for raw_line in m.group("body").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue
            fm = _get_field_re().match(line)
            if not fm:
                continue
            py_type, nested = _ts_to_py_typed(fm.group("type"), known)
            iface.fields.append(DtsField(
                name=fm.group("name"), ts_type=fm.group("type").strip(),
                py_type=py_type, optional=bool(fm.group("opt")), nested=nested))
        out.append(iface)
    return out


def _coerce_typed(nested, py_type, value_expr):
    if nested is None:
        return value_expr
    if py_type.startswith("list["):
        return (f"[{nested}.from_dict(v) if isinstance(v, dict) else v "
                f"for v in {value_expr}]")
    return (f"{nested}.from_dict({value_expr}) "
            f"if isinstance({value_expr}, dict) else {value_expr}")


def generate_stub(interfaces):
    lines = [
        '"""Generated stubs - do not edit. Source: .d.ts via pyweb npm.',
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
        lines.append("        if not isinstance(data, dict):")
        lines.append(f'            raise TypeError(f"{iface.name} must be a dict")')
        lines.append("        kwargs: dict[str, Any] = {}")
        for f in fields:
            if f.optional:
                val = _coerce_typed(f.nested, f.py_type, f"data[{f.name!r}]")
                lines.append(f"        if data.get({f.name!r}) is not None:")
                lines.append(f"            kwargs[{f.name!r}] = {val}")
            else:
                val = _coerce_typed(f.nested, f.py_type, "_v")
                lines.append(f"        if {f.name!r} not in data:")
                lines.append(f'            raise TypeError(f"{iface.name} missing required field: {f.name}")')
                lines.append(f"        _v = data[{f.name!r}]")
                lines.append(f"        kwargs[{f.name!r}] = {val}")
        lines.append("        try:")
        lines.append("            return cls(**kwargs)")
        lines.append("        except TypeError as exc:")
        lines.append(f'            raise TypeError(f"invalid {iface.name}: {{exc}}") from exc')
        lines.append("")
        lines.append("")
        lines.append(f"def validate_{iface.name}(data: dict) -> {iface.name}:")
        lines.append(f'    """Validate a dict against {iface.name}; raise TypeError if invalid."""')
        lines.append(f"    return {iface.name}.from_dict(data)")
        lines.append("")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def generate_stubs_from_file(dts_path, out_path=None):
    source = Path(dts_path).read_text(encoding="utf-8")
    code = generate_stub(parse_dts(source))
    if out_path is not None:
        Path(out_path).write_text(code, encoding="utf-8")
    return code


def npm_main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="pyweb npm",
                                 description="Generate typed Python stubs from .d.ts")
    ap.add_argument("dts", help="Input .d.ts file")
    ap.add_argument("-o", "--out", default=None,
                    help="Write stubs to file (default: stdout)")
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
