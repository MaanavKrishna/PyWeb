"""pyweb openapi: an OpenAPI 3.1 document from the server functions' signatures, rules and guards."""

import json
import subprocess
import sys

from pyweb import openapi

APP = '''
import dataclasses
from typing import Literal, Optional
from pyweb import App, Email, Field, Model, server

app = App(title="Shop")


class Product(Model):
    name: str = Field(min=2, max=60)
    price: float = Field(0, min=0)
    secret: str = Field("", private=True)


@dataclasses.dataclass
class Address:
    street: str
    city: str = "Paris"


@server(login=True, rate="5/minute")
def add_product(name: str = Field(min=2, max=60), price: float = 0.0, tags: list[str] = [],
                kind: Literal["a", "b"] = "a", note: Optional[str] = None) -> int:
    """Add a product.

    It belongs to the signed-in user.
    """
    return 1


@server
def ship(to: Address, contact: Email) -> dict:
    return {}


@server(roles=["admin"])
def product(product_id: int) -> Product:
    return Product.get(product_id)


@app.page("/")
def Home():
    <p>hi</p>
'''


def test_operations_schemas_and_security(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    doc = openapi.generate(str(path), server_url="https://shop.example")
    assert doc["openapi"] == "3.1.0" and doc["info"]["title"] == "Shop API"
    assert doc["servers"] == [{"url": "https://shop.example"}]
    assert sorted(doc["paths"]) == ["/__pyweb/rpc/add_product", "/__pyweb/rpc/product", "/__pyweb/rpc/ship"]

    add = doc["paths"]["/__pyweb/rpc/add_product"]["post"]
    assert add["summary"] == "Add a product." and "signed-in user" in add["description"]
    args = add["requestBody"]["content"]["application/json"]["schema"]["properties"]["args"]
    assert args["properties"]["name"] == {"type": "string", "minLength": 2, "maxLength": 60}
    assert args["properties"]["kind"] == {"enum": ["a", "b"], "default": "a"}
    assert args["properties"]["tags"]["items"] == {"type": "string"}
    assert args["properties"]["note"]["anyOf"][1] == {"type": "null"}
    assert args["additionalProperties"] is False
    assert {"apiToken": []} in add["security"] and {} not in add["security"]
    assert add["x-pyweb-rate-limit"] == {"calls": 5, "seconds": 60, "per": "user"}
    result = add["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["result"]
    assert result == {"type": "integer"}

    ship = doc["paths"]["/__pyweb/rpc/ship"]["post"]
    sargs = ship["requestBody"]["content"]["application/json"]["schema"]["properties"]["args"]
    assert sargs["required"] == ["to", "contact"]
    assert sargs["properties"]["to"] == {"$ref": "#/components/schemas/Address"}
    assert sargs["properties"]["contact"]["format"] == "email"
    assert {} in ship["security"]                                     # no sign-in needed
    assert doc["components"]["schemas"]["Address"]["required"] == ["street"]

    prod = doc["paths"]["/__pyweb/rpc/product"]["post"]
    assert prod["x-pyweb-requires"] == ["roles: admin"]
    schema = doc["components"]["schemas"]["Product"]
    assert "secret" not in schema["properties"] and schema["properties"]["id"]["readOnly"]
    assert schema["properties"]["price"]["minimum"] == 0
    json.dumps(doc)                                                   # serialisable


def test_cli_writes_a_file(tmp_path):
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    out = tmp_path / "openapi.json"
    done = subprocess.run([sys.executable, "-m", "pyweb.cli", "openapi", str(path), "--out", str(out)],
                          capture_output=True, text=True, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "3 server function(s)" in done.stdout and json.loads(out.read_text())["openapi"] == "3.1.0"


def test_the_document_is_valid_openapi(tmp_path):
    import pytest
    validator = pytest.importorskip("openapi_spec_validator")
    path = tmp_path / "app.pyweb"
    path.write_text(APP)
    validator.validate(openapi.generate(str(path)))
