"""npm interop stub: typed package references + .d.ts binding sketch."""

from __future__ import annotations


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
