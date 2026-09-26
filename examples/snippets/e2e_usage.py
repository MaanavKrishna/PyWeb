"""Snippet: e2e harness usage used across docs (how QA asserts apps)."""

SOURCE = """from tests.e2e_harness import (
    assert_ssr_response, assert_hydration_markers, http_get, rpc_roundtrip,
)
res = http_get(base_url + "/")
assert_ssr_response(res, ["data-pw-id=\\"counter-value\\""])
assert_hydration_markers(res.body)
rpc_roundtrip(base_url, "increment", {"count": 1})
"""


def workflow() -> list[str]:
    """Steps the e2e suite performs against every reference app."""
    return [
        "compile .pyweb",
        "assert SSR nodes",
        "boot server on ephemeral port",
        "HTTP-assert SSR status/body/headers",
        "RPC roundtrip incl. validation errors",
        "assert hydration markers",
        "check static hashing or skip",
    ]


if __name__ == "__main__":
    print(workflow())
