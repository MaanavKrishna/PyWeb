"""npm interop stub: typed package references + .d.ts binding sketch."""

from __future__ import annotations


def package(name: str, version: str = "latest"):
    return {"npm": name, "version": version, "kind": "esm"}


def from_dts(dts: str):
    """Sketch: parse `interface X {..}` declarations into Python dataclass source."""
    import re
    out = []
    for m in re.finditer(r"interface\s+(\w+)\s*\{([^}]*)\}", dts):
        name, body = m.group(1), m.group(2)
        out.append(f"@dataclass\nclass {name}:")
        for line in body.splitlines():
            fm = re.match(r"\s*(\w+)(\?)?:\s*([\w\[\]<>| ]+)", line)
            if fm:
                fname, opt, ftype = fm.group(1), fm.group(2), fm.group(3).strip()
                py_t = {"number": "float", "string": "str", "boolean": "bool"}.get(ftype, "object")
                out.append(f"    {fname}: {py_t} = None" if opt else f"    {fname}: {py_t}")
    return "\n".join(out)
