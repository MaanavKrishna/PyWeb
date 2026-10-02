"""Docs are tested: every ```pyweb block compiles, every ```python block parses."""

import ast
import re
from pathlib import Path

import pytest

from pyweb.compiler import compile_source

ROOT = Path(__file__).parent.parent
FILES = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]
BLOCK = re.compile(r"```(pyweb|python)\n(.*?)```", re.S)


def blocks():
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        for i, m in enumerate(BLOCK.finditer(text)):
            line = text[:m.start()].count("\n") + 1
            yield pytest.param(m.group(1), m.group(2), id=f"{path.name}:{line}")


@pytest.mark.parametrize("lang, code", list(blocks()))
def test_doc_block(lang, code):
    if lang == "python":
        ast.parse(code)
    else:
        out = compile_source(code, filename="doc.pyweb")
        assert out["pages"] or out["components"] or out["rpc"]
