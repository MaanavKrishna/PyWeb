# 06 - Deploy (docker + self-host, no cloud lock-in)

> Sources: `examples/snippets/chat_realtime.py` (`DOCKERFILE`,
> `COMPOSE`) and `examples/snippets/e2e_usage.py` (verify step).

## Dockerfile

<!-- snippet: examples/snippets/chat_realtime.py -->
```python
FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install pyweb
EXPOSE 8000
CMD ["pyweb", "serve", "--host", "0.0.0.0", "--port", "8000"]
```

## Compose

<!-- snippet: examples/snippets/chat_realtime.py -->
```python
services:
  web:
    build: .
    ports:
      - "8000:8000"
```

## Ship checklist (any host, any cloud, or bare metal)

1. `pyweb build` is clean (CI runs it per reference app).
2. Serve on `$PORT` (default `8000`) behind any reverse proxy.
3. No cloud-only services: sqlite works single-node; bring your own
   postgres/redis via env vars when you outgrow it.

## Verify the deployment

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

Point the harness at the deployed `base_url`: SSR `200` + hydration
markers + RPC roundtrip must all pass before promoting the release.
