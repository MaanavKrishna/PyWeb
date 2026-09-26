"""Snippet: realtime strategy used by docs (chat) + deploy config docs/06-deploy.md."""

SOURCE = """<ul data-pw-id="chat-log" pw-bind="messages"
    pw-stream="/__pyweb/events" pw-poll="/__pyweb/poll">
</ul>
# strategy: sse-with-poll-fallback
"""

SSE_PATH = "/__pyweb/events"
POLL_PATH = "/__pyweb/poll"
STRATEGY = "sse-with-poll-fallback"

DOCKERFILE = """FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install pyweb
EXPOSE 8000
CMD ["pyweb", "serve", "--host", "0.0.0.0", "--port", "8000"]
"""

COMPOSE = """services:
  web:
    build: .
    ports:
      - "8000:8000"
"""


def pick_transport(sse_available: bool) -> str:
    """Return the transport the client should use."""
    return SSE_PATH if sse_available else POLL_PATH


def parse_sse_frame(frame: str) -> dict:
    """Parse one SSE frame's data line (mirrors the e2e assertion)."""
    import json as _json

    for line in frame.splitlines():
        if line.startswith("data:"):
            return _json.loads(line[len("data:"):].strip())
    raise ValueError("no data line in SSE frame")


if __name__ == "__main__":
    print(STRATEGY, pick_transport(True), pick_transport(False))
    print(parse_sse_frame('event: message\ndata: {"text": "hi"}\n\n'))
    print(DOCKERFILE.splitlines()[0])
    print(COMPOSE.splitlines()[0])
