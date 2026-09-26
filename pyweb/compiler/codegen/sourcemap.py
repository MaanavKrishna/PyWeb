"""Minimal inline VLQ sourcemap encoder/decoder (no new dependencies).

Maps generated JS lines back to .pyweb source lines for the browser
error overlay hook ``window.__pyweb_error(map)``.
"""
from __future__ import annotations

B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
B64_REV = {c: i for i, c in enumerate(B64)}


def encode_vlq(value: int) -> str:
    n = ((-value) << 1 | 1) if value < 0 else (value << 1)
    out = []
    while True:
        digit = n & 0x1F
        n >>= 5
        if n:
            digit |= 0x20
        out.append(B64[digit])
        if not n:
            break
    return "".join(out)


def decode_vlq(text: str, pos: int = 0) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        digit = B64_REV[text[pos]]
        pos += 1
        result |= (digit & 0x1F) << shift
        shift += 5
        if not (digit & 0x20):
            break
    value = -(result >> 1) if result & 1 else (result >> 1)
    return value, pos


def encode_segment(*fields: int) -> str:
    return "".join(encode_vlq(f) for f in fields)


def decode_segment(text: str) -> tuple[int, ...]:
    out = []
    pos = 0
    while pos < len(text):
        value, pos = decode_vlq(text, pos)
        out.append(value)
    return tuple(out)


def decode_mappings(mappings: str) -> list[list[tuple[int, int, int, int]]]:
    """Decode a mappings string into per-line segments.

    Returns, per generated line, a list of
    ``(gen_col, src_index, src_line, src_col)`` with absolute values.
    """
    lines: list[list[tuple[int, int, int, int]]] = []
    src = 0
    src_line = 0
    src_col = 0
    for line in mappings.split(";"):
        gen_col = 0
        segs: list[tuple[int, int, int, int]] = []
        if line.strip():
            for raw in line.split(","):
                fields = decode_segment(raw)
                gen_col += fields[0]
                if len(fields) >= 4:
                    src += fields[1]
                    src_line += fields[2]
                    src_col += fields[3]
                    segs.append((gen_col, src, src_line, src_col))
        lines.append(segs)
    return lines


def lookup(mappings: str, gen_line: int, gen_col: int = 0) -> tuple[int, int] | None:
    """Map a 1-based generated line/col back to a 0-based (src_line, src_col)."""
    lines = decode_mappings(mappings)
    if gen_line < 1 or gen_line > len(lines):
        return None
    best: tuple[int, int] | None = None
    for gcol, _src, sline, scol in lines[gen_line - 1]:
        if gcol <= gen_col:
            best = (sline, scol)
        else:
            break
    return best


class SourceMap:
    """Collects (generated line/col -> .pyweb line/col) entries."""

    def __init__(self, source: str):
        self.source = source
        self._entries: dict[int, list[tuple[int, int, int]]] = {}

    def add(self, gen_line: int, gen_col: int, src_line: int, src_col: int) -> None:
        self._entries.setdefault(gen_line, []).append((gen_col, src_line - 1, src_col))

    def mappings(self) -> str:
        if not self._entries:
            return ""
        last = max(self._entries)
        lines = []
        prev_src = 0
        prev_line = 0
        prev_col = 0
        for gen in range(1, last + 1):
            segs = []
            prev_gen_col = 0
            for gen_col, src_line, src_col in sorted(self._entries.get(gen, [])):
                seg = encode_segment(
                    gen_col - prev_gen_col, 0 - prev_src, src_line - prev_line, src_col - prev_col
                )
                segs.append(seg)
                prev_gen_col = gen_col
                prev_src, prev_line, prev_col = 0, src_line, src_col
            lines.append(",".join(segs))
        return ";".join(lines)

    def to_dict(self, js_file: str = "app.js") -> dict:
        return {
            "version": 3,
            "file": js_file,
            "sources": [self.source],
            "names": [],
            "mappings": self.mappings(),
        }
