# 00 - Quickstart: hello world in under 5 minutes

> Every code sample in this guide is extracted from a file the e2e suite
> executes. The marker under each block names its source.

## 1. Create the app

<!-- snippet: examples/snippets/hello_counter.py -->
```python
<main data-pw-id="counter-app">
  <h1 data-pw-id="counter-title">Counter</h1>
  <div data-pw-id="counter-value" pw-bind="count">0</div>
  <button data-pw-id="counter-inc" data-pw-action="increment">+</button>
</main>
```

Save it as `app.pyweb` (see the full file at
`examples/counter/app.pyweb`).

## 2. Build

<!-- snippet: examples/snippets/e2e_usage.py -->
```python
from tests.e2e_harness import (
    assert_ssr_response, assert_hydration_markers, http_get, rpc_roundtrip,
)
res = http_get(base_url + "/")
assert_ssr_response(res, ["data-pw-id=\"counter-value\""])
assert_hydration_markers(res.body)
rpc_roundtrip(base_url, "increment", {"count": 1})
```

`pyweb build` compiles the `.pyweb` file to SSR HTML; `pyweb serve`
serves it with a 200 status. QA asserts both steps
(`tests/test_e2e.py::TestCounterApp`).

## 3. Verify

Run the suite (which executes `examples/snippets/e2e_usage.py`):

<!-- snippet: examples/snippets/e2e_usage.py -->
```sh
python3 -m pytest tests/ -q -p no:cacheprovider
```

Green means your hello-world SSR-renders, hydrates (`data-pw-id`,
`pw-bind`), and round-trips the `increment` RPC.
