"""Demo http.server wrapper for the showcase (Track B)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def main():
    from examples.todo import make_app

    app = make_app(os.environ.get("TODO_DB", ":memory:"))
    host = os.environ.get("PYWEB_HOST", "127.0.0.1")
    port = int(os.environ.get("PYWEB_PORT", "8000"))
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
