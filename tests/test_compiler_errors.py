"""Compile errors for things the compiler used to ignore."""

import pytest


def test_page_options_must_be_written_out():
    """login=SOME_NAME used to be ignored without a word (the page was public)."""
    from pyweb.compiler import compile_source
    from pyweb.compiler.errors import CompileError
    src = 'from pyweb import App\napp = App()\nNEED = True\n@app.page("/", login=NEED)\ndef Home():\n    <p>x</p>\n'
    with pytest.raises(CompileError, match="login= .* must be written out") as err:
        compile_source(src, filename="app.pyweb")
    assert err.value.lineno == 4
