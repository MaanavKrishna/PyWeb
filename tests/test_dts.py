"""Tests for `.d.ts` -> Python stub generation (Track D)."""
from __future__ import annotations

import subprocess
import sys

import pytest

from pyweb.npm import generate_stub, generate_stubs_from_file, main, parse_dts

SAMPLE = """
interface User {
  id: string;
  email?: string;
  age: number;
  tags: string[];
  admin: boolean;
  meta: Array<number>;
  role: "a" | "b";
}
interface Post extends Base {
  title: string;
  author: User;
}
"""


def test_parse_primitives_arrays_optionals():
    (user,) = [i for i in parse_dts(SAMPLE) if i.name == "User"]
    by_name = {f.name: f for f in user.fields}
    assert by_name["id"].py_type == "str" and not by_name["id"].optional
    assert by_name["email"].py_type == "str" and by_name["email"].optional
    assert by_name["age"].py_type == "float"
    assert by_name["tags"].py_type == "list[str]"
    assert by_name["admin"].py_type == "bool"
    assert by_name["meta"].py_type == "list[float]"
    assert by_name["role"].py_type == "str"


def test_generate_stub_validates_and_nests(tmp_path):
    code = generate_stub(parse_dts(SAMPLE))
    assert "class User" in code and "class Post(Base)" in code
    assert "Optional[str]" in code and "def validate_User" in code
    stub = tmp_path / "stubs.py"
    stub.write_text(code, encoding="utf-8")
    ns: dict = {}
    exec(compile(stub.read_text(), str(stub), "exec"), ns)
    user = ns["validate_User"]({"id": "1", "age": 3, "tags": ["x"],
                                "admin": True, "meta": [1], "role": "a"})
    assert user.id == "1" and user.email is None
    author = {"id": "1", "age": 3, "tags": [], "admin": False, "meta": [], "role": "a"}
    post = ns["Post"].from_dict({"title": "t", "author": author})
    assert post.author.id == "1"
    with pytest.raises(TypeError):
        ns["validate_User"]("nope")


def test_cli_file_roundtrip(tmp_path, capsys):
    dts = tmp_path / "a.d.ts"
    dts.write_text("interface A { name: string; }", encoding="utf-8")
    assert generate_stubs_from_file(dts).find("class A") != -1
    out = tmp_path / "out.py"
    assert main([str(dts), "-o", str(out)]) == 0
    assert "class A" in out.read_text()
    assert main([str(tmp_path / "missing.d.ts")]) == 1


def test_cli_end_to_end_subprocess(tmp_path):
    dts = tmp_path / "a.d.ts"
    dts.write_text("interface A { name: string; }", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "pyweb.cli", "npm", str(dts)],
                          capture_output=True, text=True, cwd=".")
    assert proc.returncode == 0 and "class A" in proc.stdout
