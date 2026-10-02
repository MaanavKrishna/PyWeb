"""Compiler errors that point at a `.pyweb` file and line."""

from __future__ import annotations


class CompileError(ValueError):
    """A user-facing compile error.

    ``str(err)`` is ``"<file>:<line>: <message>"`` so editors and CI logs
    can jump to the source; ``err.lineno`` and ``err.msg`` are available
    separately.
    """

    def __init__(self, msg, lineno=None, filename=None):
        self.msg = msg
        self.lineno = lineno
        self.filename = filename
        super().__init__(self._render())

    def _render(self):
        where = self.filename or "<pyweb>"
        if self.lineno:
            where += f":{self.lineno}"
        return f"{where}: {self.msg}"

    def with_file(self, filename):
        self.filename = filename
        self.args = (self._render(),)
        return self
